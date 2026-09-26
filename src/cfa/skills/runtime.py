"""Shared runtime for skill (MCP) servers.

Every skill is a separate process with its own database role, its own token
audience and its own manifest. This module holds what they have in common:

* ``DelegatedTokenVerifier`` - validates RS256 delegated tokens for one audience.
* ``Boundary`` - the data-product boundary: entitlement check (user AND agent AND
  tenant), audit of every decision, rate limiting and error hygiene.
* ``create_app`` - the ASGI app with DNS-rebinding protection and a health probe.
"""

import logging
import re
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import jwt
import yaml
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from cfa.audit import Action, AuditEvent, AuditLog, content_hash
from cfa.config import SkillServerSettings, database_settings, skill_server_settings
from cfa.db import Pool, open_pool
from cfa.identity.tokens import DelegatedClaims, TokenValidator
from cfa.logging import configure_logging
from cfa.policy import AccessDecision, DataProduct, DenyReason, Principal, authorize

log = logging.getLogger(__name__)

EVAL_MUTANT_HEADER = "x-eval-mutant"
TRACE_HEADER = "x-trace-id"
_TRACE_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
RATE_LIMIT_PER_MINUTE = 60


class Manifest(BaseModel):
    """Declared by the skill owner; the harness derives risk tiers from it."""

    name: str
    audience: str
    description: str
    risk_tier: int
    required_agent_scope: str
    required_user_role: str
    tools: list[str]

    def product(self) -> DataProduct:
        return DataProduct(self.name, self.required_agent_scope, self.required_user_role, self.risk_tier)


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate(yaml.safe_load(path.read_text()))


class DelegatedTokenVerifier(TokenVerifier):
    def __init__(self, validator: TokenValidator, audience: str) -> None:
        self._validator = validator
        self._audience = audience

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            raw = await self._validator.decode(token, self._audience)
            claims = DelegatedClaims.model_validate(raw)
        except (jwt.InvalidTokenError, jwt.PyJWKClientError, ValidationError):
            log.warning("rejected token for audience %s", self._audience)
            return None
        return AccessToken(
            token=token,
            client_id=claims.act.sub,
            scopes=sorted(claims.scopes),
            expires_at=int(raw["exp"]),
            resource=self._audience,
            subject=claims.sub,
            claims=raw,
        )


class _RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= self._per_minute:
            return False
        hits.append(now)
        return True


class Boundary:
    """Enforces entitlements and writes an audit record for every decision."""

    def __init__(self, manifest: Manifest, settings: SkillServerSettings) -> None:
        self.manifest = manifest
        self.product = manifest.product()
        self._settings = settings
        self._pool: Pool | None = None
        self._rate_limiter = _RateLimiter(RATE_LIMIT_PER_MINUTE)

    def bind(self, pool: Pool | None) -> None:
        self._pool = pool

    @property
    def pool(self) -> Pool:
        if self._pool is None:
            raise RuntimeError("skill server not started")
        return self._pool

    async def guard[T](
        self,
        ctx: Context[Any, Any, Any],
        tool: str,
        args: dict[str, Any],
        fetch: Callable[[Principal, Pool], Awaitable[T | None]],
        *,
        owner_of: Callable[[T], str] | None = None,
        not_found: str = "Not found for your account.",
    ) -> T:
        """Authorise, fetch, check tenancy, audit, and return the result.

        The tenant key always comes from the verified token, never from tool
        arguments chosen by the model.
        """
        principal = _principal()
        trace_id, mutant = _request_meta(ctx)
        audit = AuditLog(self.pool)

        async def record(decision: AccessDecision, output: Any = None, note: str | None = None) -> None:
            snapshot = _jsonable(output)
            await audit.record(
                AuditEvent(
                    trace_id=trace_id,
                    component=self.manifest.audience,
                    action=Action.ACCESS_DECISION,
                    name=tool,
                    actor_user=principal.user,
                    actor_agent=principal.agent,
                    decision="allow" if decision.allowed else "deny",
                    reason=note or decision.reason,
                    input={
                        "args": args,
                        "customer_id": principal.customer_id,
                        "agent_scopes": sorted(principal.agent_scopes),
                    },
                    output={"result": snapshot} if snapshot is not None else {},
                    output_hash=content_hash(snapshot) if snapshot is not None else None,
                    policy_version=decision.policy_version,
                )
            )

        if not self._rate_limiter.allow(principal.user):
            raise ToolError("Rate limit exceeded, please retry later.")

        decision = authorize(principal, self.product)
        if not decision.allowed:
            await record(decision)
            raise ToolError(_deny_message(decision.reason, self.product.name))

        try:
            result = await fetch(principal, self.pool)
        except Exception as exc:
            log.exception("skill %s tool %s failed", self.product.name, tool)
            raise ToolError("The service is temporarily unavailable.") from exc

        if result is None:
            await record(AccessDecision(False, DenyReason.NOT_FOUND))
            raise ToolError(not_found)

        if owner_of is not None:
            decision = authorize(principal, self.product, owner_of(result))
            if not decision.allowed:
                if mutant == "bypass-tenant" and self._settings.allow_eval_mutants:
                    await record(AccessDecision(True, "eval_mutant"), result, note="EVAL MUTANT: tenant check bypassed")
                    return result
                await record(decision)
                # Same message as "not found": do not confirm that another tenant's record exists.
                raise ToolError(not_found)

        await record(decision, result)
        return result


def _principal() -> Principal:
    token = get_access_token()
    if token is None or token.claims is None:
        raise ToolError("Unauthenticated.")
    claims = DelegatedClaims.model_validate(token.claims)
    return Principal(
        user=claims.sub,
        customer_id=claims.customer_id,
        roles=frozenset(claims.roles),
        agent=claims.act.sub,
        agent_scopes=claims.scopes,
    )


def _request_meta(ctx: Context[Any, Any, Any]) -> tuple[str, str | None]:
    request = ctx.request_context.request
    headers = request.headers if isinstance(request, Request) else {}
    trace_id = headers.get(TRACE_HEADER, "")
    if not _TRACE_ID.match(trace_id):
        trace_id = uuid.uuid4().hex
    return trace_id, headers.get(EVAL_MUTANT_HEADER)


def _deny_message(reason: str, product: str) -> str:
    if reason == DenyReason.AGENT_SCOPE_MISSING:
        return f"Access denied: the assistant is not authorised to read {product} data."
    if reason == DenyReason.USER_ROLE_MISSING:
        return f"Access denied: your account does not have permission to view {product} data."
    return "Access denied."


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def create_skill(manifest_path: Path, instructions: str) -> tuple[FastMCP, Boundary]:
    settings = skill_server_settings()
    manifest = load_manifest(manifest_path)
    allowed_hosts = [h for h in settings.skill_allowed_hosts.split(",") if h] or [f"{manifest.audience}:*"]
    mcp = FastMCP(
        name=manifest.audience,
        instructions=instructions,
        token_verifier=DelegatedTokenVerifier(
            TokenValidator(f"{settings.idp_url}/.well-known/jwks.json", settings.idp_issuer), manifest.audience
        ),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.idp_issuer),
            resource_server_url=AnyHttpUrl(f"http://{manifest.audience}:{settings.skill_port}"),
        ),
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=allowed_hosts, allowed_origins=[]
        ),
    )
    return mcp, Boundary(manifest, settings)


def create_app(mcp: FastMCP, boundary: Boundary) -> Starlette:
    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        configure_logging()
        async with open_pool(database_settings().database_url.get_secret_value()) as pool:
            boundary.bind(pool)
            async with mcp.session_manager.run():
                yield
            boundary.bind(None)

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "skill": boundary.manifest.name})

    return Starlette(
        routes=[Route("/healthz", healthz), Mount("/", app=mcp.streamable_http_app())],
        lifespan=lifespan,
    )
