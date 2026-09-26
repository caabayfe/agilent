import httpx
import pytest

from cfa.identity.client import IdpClient
from tests.integration.conftest import IDP_URL


async def test_exchange_requires_agent_credentials(idp: IdpClient) -> None:
    user_token = await idp.login("alice")
    wrong = IdpClient(IDP_URL, "customer-assistant", "wrong-secret")
    with pytest.raises(httpx.HTTPStatusError):
        await wrong.exchange_for_skills(user_token)


async def test_exchange_rejects_non_user_tokens(idp: IdpClient) -> None:
    delegated = await idp.exchange_for_skills(await idp.login("alice"))
    with pytest.raises(httpx.HTTPStatusError):
        await idp.exchange_for_skills(delegated)  # a delegated token cannot be re-exchanged


async def test_exchange_rejects_unknown_audiences(idp: IdpClient) -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await idp.exchange_for_skills(await idp.login("alice"), audience="mcp-orders")
