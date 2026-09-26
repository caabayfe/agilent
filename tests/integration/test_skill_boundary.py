"""The data boundary, tested directly at the MCP server (no agent, no model)."""

import json
import uuid
from typing import Any

import httpx
import jwt
import pytest

from cfa.agent.context import AGENT_ID, RequestContext
from cfa.agent.skills_client import SkillClient
from cfa.audit import Action, AuditLog
from cfa.identity.client import IdpClient

SERVER = "http://mcp-customer:8000/mcp"
ACCEPT = "application/json, text/event-stream"
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
CUSTOMERS = {"alice": "ACME-001", "bob": "ACME-001", "carol": "NOV-002"}


async def context(idp: IdpClient, username: str) -> RequestContext:
    token = await idp.exchange_for_skills(await idp.login(username))
    return RequestContext(uuid.uuid4().hex, username, CUSTOMERS[username], AGENT_ID, token)


async def call(idp: IdpClient, user: str, skill: str, tool: str, args: dict[str, object]) -> tuple[dict[str, Any], str]:
    ctx = await context(idp, user)
    return json.loads(await SkillClient(SERVER).call(skill, tool, args, ctx)), ctx.trace_id


async def test_owner_reads_own_order(idp: IdpClient, audit: AuditLog) -> None:
    result, trace = await call(idp, "alice", "orders", "get_order", {"order_id": "SO-10231"})
    assert result["order_id"] == "SO-10231"
    (event,) = [e for e in await audit.for_trace(trace) if e.action is Action.ACCESS_DECISION]
    assert (event.decision, event.actor_user, event.actor_agent) == ("allow", "alice", AGENT_ID)
    assert event.output_hash


async def test_cross_tenant_is_denied_without_confirming_existence(idp: IdpClient, audit: AuditLog) -> None:
    result, trace = await call(idp, "alice", "orders", "get_order", {"order_id": "SO-20017"})
    missing, _ = await call(idp, "alice", "orders", "get_order", {"order_id": "SO-99999"})
    assert "not found" in str(result["error"])
    # Same message whether the order exists for another tenant or not at all.
    assert result["error"].replace("SO-20017", "X") == missing["error"].replace("SO-99999", "X")
    (event,) = [e for e in await audit.for_trace(trace) if e.action is Action.ACCESS_DECISION]
    assert (event.decision, event.reason) == ("deny", "cross_tenant")
    assert event.output == {}  # nothing of the other tenant is written to the audit either


async def test_list_is_scoped_to_the_token_tenant(idp: IdpClient) -> None:
    carol, _ = await call(idp, "carol", "orders", "list_orders", {})
    assert {o["order_id"] for o in carol["orders"]} == {"SO-20017", "SO-20018"}


async def test_user_without_role_is_denied(idp: IdpClient, audit: AuditLog) -> None:
    result, trace = await call(idp, "bob", "billing", "list_invoices", {})
    assert "permission" in str(result["error"])
    (event,) = [e for e in await audit.for_trace(trace) if e.action is Action.ACCESS_DECISION]
    assert (event.reason, event.component) == ("user_role_missing", "mcp-customer/billing")


async def test_invalid_arguments_are_rejected(idp: IdpClient) -> None:
    result, _ = await call(idp, "alice", "orders", "get_order", {"order_id": "SO-1; DROP TABLE"})
    assert "error" in result


@pytest.mark.parametrize("token", [None, "not-a-jwt"])
async def test_requests_without_a_valid_token_are_rejected(token: str | None) -> None:
    headers = {"Accept": ACCEPT}
    if token:
        headers["Authorization"] = "Bearer " + token
    async with httpx.AsyncClient(timeout=5) as http:
        response = await http.post(SERVER, headers=headers, json=INITIALIZE)
    assert response.status_code == 401


async def test_user_token_is_useless_at_the_skill_server(idp: IdpClient) -> None:
    """No token passthrough: the user's own token (audience cfa-bff) is rejected."""
    user_token = await idp.login("alice")
    async with httpx.AsyncClient(timeout=5) as http:
        response = await http.post(
            SERVER, headers={"Authorization": f"Bearer {user_token}", "Accept": ACCEPT}, json=INITIALIZE
        )
    assert response.status_code == 401


async def test_delegated_token_is_bound_to_the_server_and_lists_skill_scopes(idp: IdpClient) -> None:
    token = await idp.exchange_for_skills(await idp.login("bob"))
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["aud"] == "mcp-customer"
    assert claims["act"] == {"sub": AGENT_ID}
    # Agent scopes per skill; the user's roles travel separately and are checked per skill too.
    assert claims["scope"].split() == ["billing.read", "orders.read", "service.read"]
    assert "billing:view" not in claims["roles"]


async def test_catalogue_groups_tools_by_skill_and_shows_the_callers_access(idp: IdpClient) -> None:
    token = await idp.exchange_for_skills(await idp.login("bob"))
    catalog = await SkillClient(SERVER).catalog(token, uuid.uuid4().hex)
    skills = {s["name"]: s for s in catalog["skills"]}
    assert catalog["server"] == "mcp-customer"
    assert {name: [t["name"] for t in s["tools"]] for name, s in skills.items()} == {
        "orders": ["list_orders", "get_order"],
        "billing": ["list_invoices", "get_invoice"],
        "service": ["get_service_history", "search_troubleshooting"],
    }
    assert skills["billing"]["risk_tier"] == 2
    assert skills["billing"]["access"] == {"allowed": False, "reason": "user_role_missing"}
    assert skills["orders"]["access"]["allowed"]


async def test_dns_rebinding_protection(idp: IdpClient) -> None:
    token = await idp.exchange_for_skills(await idp.login("alice"))
    async with httpx.AsyncClient(timeout=5) as http:
        response = await http.post(
            SERVER,
            headers={"Authorization": f"Bearer {token}", "Host": "evil.example", "Accept": ACCEPT},
            json=INITIALIZE,
        )
    assert response.status_code in (400, 421)
