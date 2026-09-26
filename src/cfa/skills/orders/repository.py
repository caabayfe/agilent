from cfa.db import Pool
from cfa.skills.orders.models import OrderDetail, OrderLine, OrderList, OrderSummary

_OPEN_STATUSES = ("processing", "partially_shipped", "shipped")
MAX_ROWS = 50


class OrdersRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def list_orders(self, customer_id: str, *, open_only: bool) -> OrderList:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT order_id, placed_on, status, total, currency FROM orders.orders "
                    "WHERE customer_id = %s AND (NOT %s OR status = ANY(%s)) "
                    "ORDER BY placed_on DESC LIMIT %s",
                    (customer_id, open_only, list(_OPEN_STATUSES), MAX_ROWS),
                )
            ).fetchall()
        return OrderList(orders=[OrderSummary.model_validate(row) for row in rows])

    async def get_order(self, order_id: str) -> OrderDetail | None:
        async with self._pool.connection() as conn:
            order = await (
                await conn.execute(
                    "SELECT order_id, customer_id, placed_on, status, total, currency "
                    "FROM orders.orders WHERE order_id = %s",
                    (order_id,),
                )
            ).fetchone()
            if order is None:
                return None
            lines = await (
                await conn.execute(
                    "SELECT line_no, sku, description, quantity, status, tracking_number, eta "
                    "FROM orders.order_lines WHERE order_id = %s ORDER BY line_no",
                    (order_id,),
                )
            ).fetchall()
        return OrderDetail.model_validate({**order, "lines": [OrderLine.model_validate(line) for line in lines]})
