"""Integration tests run inside the compose network (``make test``)."""

import os
from collections.abc import AsyncIterator

import httpx
import pytest

from cfa.audit import AuditLog
from cfa.db import open_pool
from cfa.identity.client import IdpClient

IDP_URL = os.environ.get("IDP_URL", "http://idp:8000")


def _stack_up() -> bool:
    try:
        return httpx.get(f"{IDP_URL}/healthz", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
            if not _stack_up():
                item.add_marker(pytest.mark.skip(reason="compose stack not reachable"))


@pytest.fixture
def idp() -> IdpClient:
    return IdpClient(IDP_URL, "customer-assistant", os.environ["AGENT_CLIENT_SECRET"])


@pytest.fixture
async def audit() -> AsyncIterator[AuditLog]:
    async with open_pool(os.environ["EVAL_DATABASE_URL"], max_size=2) as pool:
        yield AuditLog(pool)
