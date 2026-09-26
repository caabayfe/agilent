"""Orders skill: order history and shipment status (MCP server)."""

from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import Context
from pydantic import Field

from cfa.skills.orders.models import OrderDetail, OrderList
from cfa.skills.orders.repository import OrdersRepository
from cfa.skills.runtime import create_app, create_skill

mcp, boundary = create_skill(
    Path(__file__).with_name("manifest.yaml"),
    instructions="Read-only access to the signed-in customer's orders and shipments.",
)


@mcp.tool()
async def list_orders(
    ctx: Context[Any, Any, Any],
    open_only: Annotated[bool, Field(description="Only orders not yet delivered")] = False,
) -> OrderList:
    """List the customer's orders (most recent first) with status and total."""
    return await boundary.guard(
        ctx,
        "list_orders",
        {"open_only": open_only},
        lambda principal, pool: OrdersRepository(pool).list_orders(principal.customer_id, open_only=open_only),
    )


@mcp.tool()
async def get_order(
    ctx: Context[Any, Any, Any],
    order_id: Annotated[str, Field(pattern=r"^SO-\d{5}$", description="Order number, e.g. SO-10231")],
) -> OrderDetail:
    """Get one order with its lines, per-line shipment status, tracking numbers and ETAs."""
    return await boundary.guard(
        ctx,
        "get_order",
        {"order_id": order_id},
        lambda _principal, pool: OrdersRepository(pool).get_order(order_id),
        owner_of=lambda order: order.customer_id,
        not_found=f"Order {order_id} was not found for your account.",
    )


app = create_app(mcp, boundary)
