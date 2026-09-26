"""JWT validation shared by the BFF and the skill servers.

Tokens are RS256, signed by the IdP and validated against its JWKS endpoint.
Audience validation is mandatory: a token minted for ``mcp-orders`` is useless at
``mcp-billing`` (no token passthrough, no confused deputy).
"""

import asyncio
from typing import Any

import jwt
from pydantic import BaseModel

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"  # noqa: S105 - RFC 8693 URN
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"  # noqa: S105 - RFC 8693 URN, not a secret


class UserClaims(BaseModel):
    """Claims of a user token issued to the UI (audience: the BFF)."""

    sub: str
    name: str
    customer_id: str
    customer_name: str
    roles: list[str]


class Actor(BaseModel):
    sub: str


class DelegatedClaims(BaseModel):
    """Claims of a delegated token (RFC 8693): the user is the subject, the agent
    is the actor, and the audience is exactly one skill server."""

    sub: str
    customer_id: str
    roles: list[str]
    scope: str
    act: Actor
    aud: str

    @property
    def scopes(self) -> frozenset[str]:
        return frozenset(self.scope.split())


class TokenValidator:
    def __init__(self, jwks_url: str, issuer: str) -> None:
        # No per-kid key cache: PyJWT's is unbounded in time, so a retired key would stay
        # trusted for the life of the process. The JWK-set cache below expires.
        # cooldown_duration: the mock IdP rotates its key on every restart and publishes
        # the new key only then, so an unknown kid must trigger a prompt re-fetch (PyJWT's
        # default is 30 s). A short cooldown still bounds re-fetches caused by forged kids.
        self._jwks = jwt.PyJWKClient(jwks_url, lifespan=60, cooldown_duration=1.0)
        self._issuer = issuer

    async def decode(self, token: str, audience: str) -> dict[str, Any]:
        """Return verified claims or raise ``jwt.InvalidTokenError``.

        A token signed with a key the IdP no longer publishes (e.g. after an IdP
        restart) is invalid, not a server error. An unreachable IdP still raises
        ``jwt.PyJWKClientConnectionError``.
        """
        try:
            signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
        except jwt.PyJWKClientConnectionError:
            raise
        except jwt.PyJWKClientError as exc:
            raise jwt.InvalidTokenError("unknown signing key") from exc
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=self._issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
        return claims
