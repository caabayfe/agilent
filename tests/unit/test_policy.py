import pytest

from cfa.policy import POLICY_VERSION, DataProduct, DenyReason, Principal, authorize

BILLING = DataProduct("billing", "billing.read", "billing:view", 2)
ALL_SCOPES = frozenset({"orders.read", "billing.read", "service.read"})


def principal(roles: set[str], scopes: frozenset[str] = ALL_SCOPES, customer: str = "ACME-001") -> Principal:
    return Principal("alice", customer, frozenset(roles), "customer-assistant", scopes)


@pytest.mark.parametrize(
    ("roles", "scopes", "resource_customer", "allowed", "reason"),
    [
        ({"billing:view"}, ALL_SCOPES, None, True, "entitled"),
        ({"billing:view"}, ALL_SCOPES, "ACME-001", True, "entitled"),
        ({"orders:view"}, ALL_SCOPES, None, False, DenyReason.USER_ROLE_MISSING),
        ({"billing:view"}, ALL_SCOPES - {"billing.read"}, None, False, DenyReason.AGENT_SCOPE_MISSING),
        ({"billing:view"}, ALL_SCOPES, "NOV-002", False, DenyReason.CROSS_TENANT),
    ],
    ids=["allow-list", "allow-own-record", "deny-user-role", "deny-agent-scope", "deny-cross-tenant"],
)
def test_entitlement_matrix(
    roles: set[str], scopes: frozenset[str], resource_customer: str | None, allowed: bool, reason: str
) -> None:
    decision = authorize(principal(roles, scopes), BILLING, resource_customer)
    assert decision.allowed is allowed
    assert decision.reason == reason
    assert decision.policy_version == POLICY_VERSION


def test_agent_scope_checked_before_user_role() -> None:
    """A user without the role, through an agent without the scope, is denied for the agent."""
    decision = authorize(principal(set(), frozenset()), BILLING)
    assert decision.reason == DenyReason.AGENT_SCOPE_MISSING
