"""Backend-for-frontend (BFF).

The browser only ever talks to this service. It validates the user's token, runs
the agent through the shared ``AssistantService``, and exposes read-only views of
the audit trail, the gateway configuration and the latest eval results.
"""

import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
import jwt
import openai
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field

from cfa.agent.service import AssistantService, build_service
from cfa.agent.skills_client import SkillClient
from cfa.audit import AuditLog
from cfa.config import agent_settings, database_settings, gateway_info_settings, identity_settings
from cfa.db import open_pool
from cfa.identity.client import IdpClient
from cfa.identity.tokens import TokenValidator, UserClaims
from cfa.logging import configure_logging
from evals.promotion import current_bindings, gateway_profile

log = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=True)
_THREAD = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32, pattern=r"^[a-z]+$")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str = Field(min_length=1, max_length=64, pattern=_THREAD.pattern)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    identity = identity_settings()
    async with open_pool(database_settings().database_url.get_secret_value()) as pool:
        audit = AuditLog(pool)
        # Simplification: in-memory conversation memory. Production: PostgresSaver.
        app.state.service = build_service(
            settings=agent_settings(), identity=identity, audit=audit, checkpointer=InMemorySaver()
        )
        app.state.audit = audit
        app.state.idp = IdpClient(
            identity.idp_url, identity.agent_client_id, identity.agent_client_secret.get_secret_value()
        )
        app.state.skills = SkillClient(agent_settings().skill_server_url)
        app.state.validator = TokenValidator(f"{identity.idp_url}/.well-known/jwks.json", identity.idp_issuer)
        yield


app = FastAPI(title="Customer Facing Assistant BFF", lifespan=lifespan, docs_url=None, redoc_url=None)


async def current_user(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)]
) -> tuple[str, UserClaims]:
    validator: TokenValidator = request.app.state.validator
    try:
        claims = await validator.decode(credentials.credentials, identity_settings().bff_audience)
    except jwt.PyJWKClientConnectionError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provider unavailable.") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session, please sign in again.") from exc
    return credentials.credentials, UserClaims.model_validate(claims)


User = Annotated[tuple[str, UserClaims], Depends(current_user)]


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/personas")
async def personas(request: Request) -> list[dict[str, object]]:
    idp: IdpClient = request.app.state.idp
    return await idp.personas()


@app.post("/api/login")
async def login(body: LoginRequest, request: Request) -> dict[str, Any]:
    """STUB: stands in for an OIDC authorization-code flow with PKCE."""
    idp: IdpClient = request.app.state.idp
    try:
        token = await idp.login(body.username)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user.") from exc
    validator: TokenValidator = request.app.state.validator
    try:
        raw = await validator.decode(token, identity_settings().bff_audience)
    except jwt.PyJWTError as exc:
        log.warning("IdP issued a token that does not validate: %s", type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is temporarily unavailable.") from exc
    claims = UserClaims.model_validate(raw)
    return {"access_token": token, "user": claims.model_dump()}


@app.post("/api/chat")
async def chat(body: ChatRequest, request: Request, user: User) -> dict[str, Any]:
    token, claims = user
    service: AssistantService = request.app.state.service
    try:
        result = await service.ask(
            user_token=token,
            user=claims,
            question=body.message,
            thread_id=f"{claims.sub}:{body.thread_id}",  # threads are namespaced per user
        )
    except openai.OpenAIError as exc:
        log.warning("model gateway error: %s", type(exc).__name__)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"The model gateway returned an error ({type(exc).__name__})."
        ) from exc
    except httpx.HTTPStatusError as exc:
        if _is_invalid_grant(exc.response):
            # The IdP no longer accepts the user's token (e.g. its signing key was rotated).
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again.") from exc
        log.warning("identity provider error: HTTP %s", exc.response.status_code)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provider unavailable.") from exc
    except httpx.HTTPError as exc:
        log.warning("identity provider error: %s", type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provider unavailable.") from exc
    return asdict(result)


def _is_invalid_grant(response: httpx.Response) -> bool:
    if response.status_code != status.HTTP_400_BAD_REQUEST:
        return False
    try:
        return bool(response.json().get("detail") == "invalid_grant")
    except ValueError:
        return False


@app.get("/api/skills")
async def skills(request: Request, user: User) -> dict[str, Any]:
    """The skill server's catalogue as the agent would see it on this user's behalf:
    skills, tools, risk tiers, entitlements, kill-switch state and current access."""
    token, _ = user
    idp: IdpClient = request.app.state.idp
    try:
        delegated = await idp.exchange_for_skills(token)
    except httpx.HTTPStatusError as exc:
        if _is_invalid_grant(exc.response):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again.") from exc
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provider unavailable.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provider unavailable.") from exc
    client: SkillClient = request.app.state.skills
    try:
        return await client.catalog(delegated, uuid.uuid4().hex)
    except (httpx.HTTPError, OSError, ExceptionGroup) as exc:
        log.warning("skill catalogue unavailable: %s", type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Skill server unavailable.") from exc


@app.get("/api/audit/{trace_id}")
async def audit_trail(trace_id: str, request: Request, user: User) -> list[dict[str, Any]]:
    """Lineage for one answer. Users only see traces of their own requests."""
    _, claims = user
    if not re.fullmatch(r"[0-9a-f]{32}", trace_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trace not found.")
    audit: AuditLog = request.app.state.audit
    events = await audit.for_trace(trace_id)
    if not events or any(e.actor_user not in (None, claims.sub) for e in events):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trace not found.")
    return [e.model_dump(mode="json") for e in events]


def _gateway_dir() -> Path:
    return Path(gateway_info_settings().gateway_config_dir)


def _alias_entry(params: dict[str, Any]) -> dict[str, str]:
    base = str(params.get("api_base") or "")
    host = base if base.startswith("os.environ/") else (urlparse(base).hostname or base)
    return {"model": str(params.get("model", "")), "api_base_host": host}


async def _loaded_aliases() -> dict[str, list[dict[str, str]]] | None:
    """What the gateway actually loaded (env references resolved); None if unreachable."""
    settings = agent_settings()
    url = settings.gateway_url.removesuffix("/v1") + "/model/info"
    headers = {"authorization": f"Bearer {settings.gateway_api_key.get_secret_value()}"}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data: list[dict[str, Any]] = response.json()["data"]
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    aliases: dict[str, list[dict[str, str]]] = {}
    for entry in data:
        aliases.setdefault(entry["model_name"], []).append(_alias_entry(entry.get("litellm_params", {})))
    return aliases


@app.get("/api/gateway")
async def gateway() -> dict[str, Any]:
    """Alias -> deployment mapping currently loaded in the gateway (no secrets)."""
    directory = _gateway_dir()
    path = directory / "active.yaml"
    config: dict[str, Any] = yaml.safe_load(path.read_text()) if path.exists() else {}
    aliases = await _loaded_aliases()
    if aliases is None:
        aliases = {}
        for entry in config.get("model_list", []):
            aliases.setdefault(entry["model_name"], []).append(_alias_entry(entry.get("litellm_params", {})))
    fallbacks = config.get("router_settings", {}).get("fallbacks", [])
    return {"profile": gateway_profile(directory), "aliases": aliases, "fallbacks": fallbacks}


@app.get("/api/evals/latest")
async def evals_latest() -> dict[str, Any]:
    """Latest eval report plus live staleness: the certificate is bound to hashes
    of what was evaluated; if anything changed since, it no longer applies."""
    reports = Path(gateway_info_settings().eval_reports_dir)
    baseline = _read_json(reports / "latest.json")
    mutants = _read_json(reports / "mutants-latest.json")
    staleness: dict[str, Any] = {"stale": False, "changed": []}
    if baseline:
        now = current_bindings(_gateway_dir())
        changed = [k for k, v in baseline.get("bindings", {}).items() if now.get(k) != v]
        staleness = {"stale": bool(changed), "changed": changed, "current": now}
    return {"baseline": baseline, "mutants": mutants, "staleness": staleness}


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text())
    return data
