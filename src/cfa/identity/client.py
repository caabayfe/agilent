"""Client the BFF and eval harness use to talk to the IdP."""

from dataclasses import dataclass

import httpx

from cfa.identity.tokens import ACCESS_TOKEN_TYPE, TOKEN_EXCHANGE_GRANT

SKILL_SERVER_AUDIENCE = "mcp-customer"


@dataclass(frozen=True, slots=True)
class IdpClient:
    base_url: str
    agent_client_id: str
    agent_client_secret: str
    timeout_s: float = 10.0

    async def personas(self) -> list[dict[str, object]]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_s) as http:
            response = await http.get("/personas")
            response.raise_for_status()
            result: list[dict[str, object]] = response.json()
            return result

    async def login(self, username: str) -> str:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_s) as http:
            response = await http.post("/login", json={"username": username})
            response.raise_for_status()
            return str(response.json()["access_token"])

    async def exchange_for_skills(self, user_token: str, audience: str = SKILL_SERVER_AUDIENCE) -> str:
        """Delegated token for the skill server (never forward the user token).

        One audience, many skills: the token's ``scope`` lists the agent scopes
        granted for each skill on that server, and each skill checks its own.
        """
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_s,
            auth=(self.agent_client_id, self.agent_client_secret),
        ) as http:
            response = await http.post(
                "/token/exchange",
                json={
                    "grant_type": TOKEN_EXCHANGE_GRANT,
                    "subject_token": user_token,
                    "subject_token_type": ACCESS_TOKEN_TYPE,
                    "audience": audience,
                },
            )
            response.raise_for_status()
            return str(response.json()["access_token"])
