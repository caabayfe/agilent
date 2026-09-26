"""One MCP server, three skills: registration invariants and the kill switch."""

from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from cfa.config import SkillServerSettings
from cfa.identity.idp import AUDIENCE_SCOPES, grant_scopes
from cfa.policy import Principal
from cfa.skills.runtime import Boundary, SkillModule, _RateLimiter, create_server, load_manifest
from cfa.skills.server import mcp

SKILLS = Path(__file__).parents[2] / "src" / "cfa" / "skills"
ALICE = Principal(
    "alice",
    "ACME-001",
    frozenset({"orders:view", "billing:view"}),
    "customer-assistant",
    frozenset({"orders.read", "billing.read", "service.read"}),
)


async def test_every_tool_is_tagged_with_the_skill_that_owns_it() -> None:
    by_skill: dict[str, list[str]] = {}
    for tool in await mcp.list_tools():
        assert tool.meta is not None
        by_skill.setdefault(tool.meta["skill"], []).append(tool.name)
        assert tool.title == f"[{tool.meta['skill']}] {tool.name}"
    assert by_skill == {
        name: load_manifest(SKILLS / name / "manifest.yaml").tools for name in ("orders", "billing", "service")
    }


def test_a_skill_must_register_exactly_the_tools_its_manifest_declares() -> None:
    def register_nothing(_mcp: FastMCP, _boundary: Boundary) -> None:
        return None

    with pytest.raises(RuntimeError, match="manifest declares"):
        create_server([SkillModule(SKILLS / "orders" / "manifest.yaml", register_nothing)], "test")


def test_kill_switch_reports_the_skill_as_unavailable() -> None:
    settings = SkillServerSettings(disabled_skills="billing")
    billing = Boundary(load_manifest(SKILLS / "billing" / "manifest.yaml"), settings, _RateLimiter(60))
    orders = Boundary(load_manifest(SKILLS / "orders" / "manifest.yaml"), settings, _RateLimiter(60))
    assert billing.describe(ALICE, [])["access"] == {"allowed": False, "reason": "skill_disabled"}
    assert orders.describe(ALICE, [])["access"] == {"allowed": True, "reason": "entitled"}


def test_delegated_token_is_down_scoped_to_what_the_agent_holds() -> None:
    audience = AUDIENCE_SCOPES["mcp-customer"]
    assert grant_scopes(audience, ["orders.read", "service.read", "admin.write"]) == "orders.read service.read"
    assert grant_scopes(audience, []) == ""
