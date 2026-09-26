"""Backend-for-frontend (BFF).

The browser only ever talks to this service. It validates the user's token, runs
the agent through the shared ``AssistantService``, and exposes read-only views of
the audit trail, the gateway configuration and the latest eval results.
"""

import json
import logging
import re
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
        app.state.validator = TokenValidator(f"{identity.idp_url}/.well-known/jwks.json", identity.idp_issuer)
        yield


app = FastAPI(title="Customer Facing Assistant BFF", lifespan=lifespan, docs_url=None, redoc_url=None)


async def current_user(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)]
) -> tuple[str, UserClaims]:
    validator: TokenValidator = request.app.state.validator
    try:
        claims = await validator.decode(credentials.credentials, identity_settings().bff_audience)
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
    claims = UserClaims.model_validate(await validator.decode(token, identity_settings().bff_audience))
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
    except httpx.HTTPError as exc:
        log.warning("identity provider error: %s", type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provider unavailable.") from exc
    return asdict(result)


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


@app.get("/api/gateway")
async def gateway() -> dict[str, Any]:
    """Alias -> deployment mapping currently loaded in the gateway (no secrets)."""
    directory = _gateway_dir()
    path = directory / "active.yaml"
    config: dict[str, Any] = yaml.safe_load(path.read_text()) if path.exists() else {}
    aliases: dict[str, list[dict[str, str]]] = {}
    for entry in config.get("model_list", []):
        params = entry.get("litellm_params", {})
        base = str(params.get("api_base", ""))
        aliases.setdefault(entry["model_name"], []).append(
            {
                "model": str(params.get("model", "")),
                "api_base_host": urlparse(base).hostname or base if not base.startswith("os.environ/") else base,
            }
        )
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
