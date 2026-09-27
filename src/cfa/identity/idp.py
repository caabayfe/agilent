"""Mock identity provider.

STUB (deliberate, see docs/adr/003-deliberate-stubs.md): stands in for Entra ID
(Entra Agent ID + on-behalf-of) or AWS AgentCore Identity. It keeps the *shape*
of the real thing so the rest of the system is honest:

* ``POST /login``           - "SSO": pick a persona, receive a user token (aud=BFF).
* ``POST /token/exchange``  - RFC 8693 token exchange. The agent authenticates with
  its own client credentials and trades the user's token for a short-lived token
  bound to the skill server (aud=mcp-customer), carrying ``sub`` (user), ``act.sub``
  (agent) and the agent scopes it holds for that server's skills.
* ``GET /.well-known/jwks.json`` - public keys for validation.

What is faked: no passwords/MFA, an ephemeral in-memory signing key, JSON
instead of form-encoded requests, and no refresh tokens.
"""

import hmac
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from jwt.algorithms import RSAAlgorithm
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from cfa.config import database_settings, identity_settings
from cfa.db import Pool, open_pool
from cfa.identity.tokens import ACCESS_TOKEN_TYPE, TOKEN_EXCHANGE_GRANT, UserClaims
from cfa.logging import configure_logging
from cfa.telemetry import configure_tracing

USER_TOKEN_TTL_S = 30 * 60
DELEGATED_TOKEN_TTL_S = 5 * 60

# The scopes each resource server (audience) understands: one per skill it hosts.
AUDIENCE_SCOPES: dict[str, frozenset[str]] = {
    "mcp-customer": frozenset({"orders.read", "billing.read", "service.read"}),
}


def grant_scopes(audience_scopes: frozenset[str], agent_scopes: list[str]) -> str:
    """Down-scope: only scopes the audience understands AND the agent currently holds."""
    return " ".join(sorted(audience_scopes.intersection(agent_scopes)))


class Persona(BaseModel):
    username: str
    display_name: str
    title: str
    customer_id: str
    customer_name: str
    roles: list[str]


class LoginRequest(BaseModel):
    username: str


class TokenExchangeRequest(BaseModel):
    grant_type: str
    subject_token: str
    subject_token_type: str = ACCESS_TOKEN_TYPE
    audience: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105 - token type label, not a secret
    expires_in: int
    issued_token_type: str = ACCESS_TOKEN_TYPE


class _SigningKey:
    def __init__(self) -> None:
        self.kid = uuid.uuid4().hex
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwks(self) -> dict[str, Any]:
        jwk: dict[str, Any] = RSAAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return {"keys": [jwk]}

    def sign(self, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": self.kid})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    app.state.signing_key = _SigningKey()
    async with open_pool(database_settings().database_url.get_secret_value()) as pool:
        app.state.pool = pool
        yield


app = FastAPI(title="Mock IdP (STUB)", lifespan=lifespan, docs_url=None, redoc_url=None)
configure_tracing("cfa-idp")
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz", exclude_spans=["receive", "send"])
_basic = HTTPBasic()


def _key(request: Request) -> _SigningKey:
    key: _SigningKey = request.app.state.signing_key
    return key


def _pool(request: Request) -> Pool:
    pool: Pool = request.app.state.pool
    return pool


async def _load_personas(pool: Pool, username: str | None = None) -> list[Persona]:
    query = (
        "SELECT u.username, u.display_name, u.title, u.customer_id, c.name AS customer_name, u.roles "
        "FROM identity.users u JOIN identity.customers c USING (customer_id) "
        "WHERE (%(username)s::text IS NULL OR u.username = %(username)s) ORDER BY u.username"
    )
    async with pool.connection() as conn:
        rows = await (await conn.execute(query, {"username": username})).fetchall()
    return [Persona.model_validate(row) for row in rows]


@app.get("/.well-known/jwks.json")
async def jwks(key: Annotated[_SigningKey, Depends(_key)]) -> dict[str, Any]:
    return key.jwks()


@app.get("/personas")
async def personas(pool: Annotated[Pool, Depends(_pool)]) -> list[Persona]:
    return await _load_personas(pool)


@app.post("/login")
async def login(
    body: LoginRequest,
    key: Annotated[_SigningKey, Depends(_key)],
    pool: Annotated[Pool, Depends(_pool)],
) -> TokenResponse:
    found = await _load_personas(pool, body.username)
    if not found:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user")
    persona = found[0]
    settings = identity_settings()
    now = int(time.time())
    claims = UserClaims(
        sub=persona.username,
        name=persona.display_name,
        customer_id=persona.customer_id,
        customer_name=persona.customer_name,
        roles=persona.roles,
    ).model_dump() | {
        "iss": settings.idp_issuer,
        "aud": settings.bff_audience,
        "iat": now,
        "exp": now + USER_TOKEN_TTL_S,
        "jti": uuid.uuid4().hex,
    }
    return TokenResponse(access_token=key.sign(claims), expires_in=USER_TOKEN_TTL_S)


@app.post("/token/exchange")
async def token_exchange(
    body: TokenExchangeRequest,
    client: Annotated[HTTPBasicCredentials, Depends(_basic)],
    key: Annotated[_SigningKey, Depends(_key)],
    pool: Annotated[Pool, Depends(_pool)],
) -> TokenResponse:
    settings = identity_settings()
    if body.grant_type != TOKEN_EXCHANGE_GRANT or body.subject_token_type != ACCESS_TOKEN_TYPE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported_grant_type")
    if body.audience not in AUDIENCE_SCOPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_target")

    # 1. Authenticate the agent (its own identity, distinct from the user's).
    secret_ok = hmac.compare_digest(client.password.encode(), settings.agent_client_secret.get_secret_value().encode())
    if client.username != settings.agent_client_id or not secret_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_client")
    async with pool.connection() as conn:
        agent = await (
            await conn.execute("SELECT scopes, active FROM identity.agents WHERE agent_id = %s", (client.username,))
        ).fetchone()
    if agent is None or not agent["active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_client")

    # 2. Validate the user's token (signed by us, minted for the BFF).
    try:
        user = jwt.decode(
            body.subject_token,
            key.private_key.public_key(),
            algorithms=["RS256"],
            audience=settings.bff_audience,
            issuer=settings.idp_issuer,
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_grant") from exc

    # 3. Down-scope: the delegated token carries, per skill on this audience, the
    #    agent's scope only if the agent currently holds it (revocation is immediate).
    granted = grant_scopes(AUDIENCE_SCOPES[body.audience], agent["scopes"])
    now = int(time.time())
    claims = {
        "iss": settings.idp_issuer,
        "aud": body.audience,
        "sub": user["sub"],
        "customer_id": user["customer_id"],
        "roles": user["roles"],
        "scope": granted,
        "act": {"sub": client.username},
        "iat": now,
        "exp": now + DELEGATED_TOKEN_TTL_S,
        "jti": uuid.uuid4().hex,
    }
    return TokenResponse(access_token=key.sign(claims), expires_in=DELEGATED_TOKEN_TTL_S)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
