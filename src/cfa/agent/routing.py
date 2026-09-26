"""Model routing policy: intent -> model alias.

This is deliberately a static, versioned lookup table. The agent only ever names
*aliases* (capability classes); which vendor model serves an alias is decided at
the model gateway. See docs/adr/001-model-gateway-and-routing.md.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, Field

ROUTING_POLICY_VERSION = "routing-2026-09-26.1"


class Intent(StrEnum):
    ORDERS = "orders"
    BILLING = "billing"
    SERVICE = "service"
    TROUBLESHOOTING = "troubleshooting"
    OTHER = "other"


class ModelAlias(StrEnum):
    FAST = "assistant-fast"
    REASONING = "assistant-reasoning"


RoutingTable = Mapping[Intent, ModelAlias]

DEFAULT_ROUTING: RoutingTable = MappingProxyType(
    {
        Intent.ORDERS: ModelAlias.FAST,
        Intent.BILLING: ModelAlias.FAST,
        Intent.SERVICE: ModelAlias.FAST,
        Intent.TROUBLESHOOTING: ModelAlias.REASONING,
        Intent.OTHER: ModelAlias.FAST,
    }
)


class IntentClassification(BaseModel):
    """Structured output of the router's classification call."""

    intent: Intent = Field(
        description=(
            "orders: order status, shipments, tracking, deliveries. "
            "billing: invoices, payments, balances, amounts owed. "
            "service: installed instruments, warranty, past service visits. "
            "troubleshooting: an instrument fault, error code or how-to fix/operate. "
            "other: anything else, including greetings."
        )
    )


def choose_alias(intent: Intent, table: RoutingTable = DEFAULT_ROUTING) -> ModelAlias:
    return table.get(intent, ModelAlias.FAST)
