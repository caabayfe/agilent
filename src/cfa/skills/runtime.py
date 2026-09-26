"""Shared runtime for the customer-data MCP server and its skills.

One server process (``mcp-customer``) hosts several skills. Each skill keeps its
own manifest, its own database role and connection pool, its own agent scope and
user role, and its own audit identity (``mcp-customer/<skill>``), so least
privilege and lineage are per skill even though the deployment unit is shared.

* ``DelegatedTokenVerifier`` - validates RS256 delegated tokens for the server audience.
* ``Boundary`` - one per skill: entitlement check (user AND agent AND tenant),
  kill switch, audit of every decision, rate limiting and error hygiene.
* ``create_server`` / ``create_app`` - one FastMCP server with every skill
  registered, a per-principal skill catalogue resource, DNS-rebinding protection
  and a health probe.
"""

import json
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
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
from pydantic import AnyHttpUrl, BaseModel, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from cfa.audit import Action, AuditEvent, AuditLog, content_hash
from cfa.config import SkillServerSettings, skill_database_settings, skill_server_settings
from cfa.db import Pool, open_pool
from cfa.identity.tokens import DelegatedClaims, TokenValidator
from cfa.logging import configure_logging
from cfa.policy import AccessDecision, DataProduct, DenyReason, Principal, authorize
from cfa.skills import SKILL_SERVER, skill_component

log = logging.getLogger(__name__)

EVAL_MUTANT_HEADER = "x-eval-mutant"
TRACE_HEADER = "x-trace-id"
_TRACE_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
RATE_LIMIT_PER_MINUTE = 60
CATALOG_URI = "skills://catalog"
SKILL_DISABLED = "skill_disabled"


class Manifest(BaseModel):
    """Declared by the skill owner; the harness derives risk tiers from it."""

    name: str
    title: str
    description: str
    risk_tier: int
    required_agent_scope: str
    required_user_role: str
    tools: list[str]
    examples: list[str] = Field(default_factory=list)

    @property
    def component(self) -> str:
        return skill_component(self.name)

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
    """One skill's data boundary: enforces entitlements and audits every decision."""

    def __init__(self, manifest: Manifest, settings: SkillServerSettings, rate_limiter: _RateLimiter) -> None:
        self.manifest = manifest
        self.product = manifest.product()
        self._settings = settings
        self._pool: Pool | None = None
        self._rate_limiter = rate_limiter

    @property
    def enabled(self) -> bool:
        return self.manifest.name not in self._settings.disabled

    def tool(self, mcp: FastMCP) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """``@mcp.tool`` tagged with the owning skill, so clients can group the catalogue."""
        return mcp.tool(meta={"skill": self.manifest.name, "risk_tier": self.manifest.risk_tier})

    def describe(self, principal: Principal, tools: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Catalogue entry, including whether THIS principal may use the skill right now."""
        decision = authorize(principal, self.product)
        if decision.allowed and not self.enabled:
            decision = AccessDecision(False, SKILL_DISABLED)
        return {
            **self.manifest.model_dump(exclude={"tools"}),
            "component": self.manifest.component,
            "enabled": self.enabled,
            "access": {"allowed": decision.allowed, "reason": decision.reason},
            "tools": [t for t in tools if t["name"] in self.manifest.tools],
        }

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
                    component=self.manifest.component,
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

        if not self.enabled:
            # Kill switch: the skill is switched off without redeploying the server.
            await record(AccessDecision(False, SKILL_DISABLED))
            raise ToolError(f"The {self.product.name} service is temporarily unavailable.")

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


type Register = Callable[[FastMCP, Boundary], None]


@dataclass(frozen=True, slots=True)
class SkillModule:
    manifest_path: Path
    register: Register


def create_server(modules: Sequence[SkillModule], instructions: str) -> tuple[FastMCP, dict[str, Boundary]]:
    settings = skill_server_settings()
    allowed_hosts = [h for h in settings.skill_allowed_hosts.split(",") if h] or [f"{SKILL_SERVER}:*"]
    mcp = FastMCP(
        name=SKILL_SERVER,
        instructions=instructions,
        token_verifier=DelegatedTokenVerifier(
            TokenValidator(f"{settings.idp_url}/.well-known/jwks.json", settings.idp_issuer), SKILL_SERVER
        ),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.idp_issuer),
            resource_server_url=AnyHttpUrl(f"http://{SKILL_SERVER}:{settings.skill_port}"),
            validate_token_resource=False,  # DelegatedTokenVerifier already enforces aud == mcp-customer
        ),
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=allowed_hosts, allowed_origins=[]
        ),
    )
    limiter = _RateLimiter(RATE_LIMIT_PER_MINUTE)  # per user, across all skills of this server
    boundaries: dict[str, Boundary] = {}
    for module in modules:
        manifest = load_manifest(module.manifest_path)
        boundary = Boundary(manifest, settings, limiter)
        known = {t.name for t in mcp._tool_manager.list_tools()}
        module.register(mcp, boundary)
        added = {t.name for t in mcp._tool_manager.list_tools()} - known
        if added != set(manifest.tools):
            raise RuntimeError(f"skill {manifest.name} registered {sorted(added)}, manifest declares {manifest.tools}")
        for name in added:
            registered = mcp._tool_manager.get_tool(name)
            if registered is not None:
                registered.title = f"[{manifest.name}] {name}"
        boundaries[manifest.name] = boundary

    @mcp.resource(
        CATALOG_URI,
        name="skill-catalog",
        title="Skill catalogue",
        description="Skills on this server, their tools, risk tier, entitlements and the caller's access.",
        mime_type="application/json",
    )
    async def catalog() -> str:
        principal = _principal()
        tools = [
            {"name": t.name, "description": t.description, "input_schema": t.inputSchema}
            for t in await mcp.list_tools()
        ]
        return json.dumps(
            {"server": SKILL_SERVER, "skills": [b.describe(principal, tools) for b in boundaries.values()]}
        )

    return mcp, boundaries


def create_app(mcp: FastMCP, boundaries: Mapping[str, Boundary]) -> Starlette:
    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        configure_logging()
        databases = skill_database_settings()
        async with AsyncExitStack() as stack:
            # One pool per skill, each logging in as that skill's least-privilege role.
            for name, boundary in boundaries.items():
                boundary.bind(await stack.enter_async_context(open_pool(databases.for_skill(name), max_size=3)))
            async with mcp.session_manager.run():
                yield
            for boundary in boundaries.values():
                boundary.bind(None)

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "server": SKILL_SERVER,
                "skills": {name: "enabled" if b.enabled else "disabled" for name, b in boundaries.items()},
            }
        )

    return Starlette(
        routes=[Route("/healthz", healthz), Mount("/", app=mcp.streamable_http_app())],
        lifespan=lifespan,
    )
