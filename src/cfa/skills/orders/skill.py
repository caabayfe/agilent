"""Orders skill: order history and shipment status. Registered on the shared ``mcp-customer`` server."""

from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from cfa.skills.orders.models import OrderDetail, OrderList
from cfa.skills.orders.repository import OrdersRepository
from cfa.skills.runtime import Boundary, SkillModule


def register(mcp: FastMCP, boundary: Boundary) -> None:
    @boundary.tool(mcp)
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

    @boundary.tool(mcp)
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


MODULE = SkillModule(Path(__file__).with_name("manifest.yaml"), register)
