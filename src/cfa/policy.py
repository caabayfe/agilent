"""Entitlement rules enforced at the data-product boundary.

STUB (deliberate, see docs/adr/003-deliberate-stubs.md): in production this is an
external policy decision point (OPA/Rego or Cedar, e.g. Amazon Verified
Permissions) with versioned, independently tested policy bundles. Here the rule
is plain Python so the whole decision is visible on one screen.
"""

from dataclasses import dataclass
from enum import StrEnum

POLICY_VERSION = "entitlements-2026-09-26.1"


class DenyReason(StrEnum):
    AGENT_SCOPE_MISSING = "agent_scope_missing"
    USER_ROLE_MISSING = "user_role_missing"
    CROSS_TENANT = "cross_tenant"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class DataProduct:
    name: str
    required_agent_scope: str
    required_user_role: str
    risk_tier: int


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is asking: the human user *and* the agent acting on their behalf."""

    user: str
    customer_id: str
    roles: frozenset[str]
    agent: str
    agent_scopes: frozenset[str]


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    reason: str
    policy_version: str = POLICY_VERSION


def authorize(principal: Principal, product: DataProduct, resource_customer_id: str | None = None) -> AccessDecision:
    """Allow only if the agent holds the scope, the user holds the role, and the
    resource (when known) belongs to the user's own tenant.

    Both identities must pass: a fully entitled user cannot reach data through an
    agent that lacks the scope, and vice versa.
    """
    if product.required_agent_scope not in principal.agent_scopes:
        return AccessDecision(False, DenyReason.AGENT_SCOPE_MISSING)
    if product.required_user_role not in principal.roles:
        return AccessDecision(False, DenyReason.USER_ROLE_MISSING)
    if resource_customer_id is not None and resource_customer_id != principal.customer_id:
        return AccessDecision(False, DenyReason.CROSS_TENANT)
    return AccessDecision(True, "entitled")
