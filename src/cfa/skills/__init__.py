"""Customer data skills (orders, billing, service) served by ONE MCP server.

The server is the deployment unit; the skill is the unit of entitlement, audit,
database role, risk tier and promotion (see docs/adr/004-one-mcp-server-many-skills.md).
"""

SKILL_SERVER = "mcp-customer"


def skill_component(skill: str) -> str:
    """Audit ``component`` for a skill, e.g. ``mcp-customer/billing``."""
    return f"{SKILL_SERVER}/{skill}"
