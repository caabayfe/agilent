"""Resolves case fact specs to ground-truth values from the data products.

Runs under the dedicated ``cfa_eval`` database role (a separate trust domain
from the agent), so expectations are read from the source of truth at eval time.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from cfa.db import Pool


class ValueType(StrEnum):
    TEXT = "text"
    AMOUNT = "amount"
    DATE = "date"
    ID = "id"


@dataclass(frozen=True, slots=True)
class Fact:
    kind: str
    label: str
    type: ValueType
    value: str


_QUERIES: dict[str, tuple[ValueType, str, str]] = {
    "order_status": (
        ValueType.TEXT,
        "SELECT status FROM orders.orders WHERE order_id = %(order)s",
        "status of {order}",
    ),
    "line_status": (
        ValueType.TEXT,
        "SELECT status FROM orders.order_lines WHERE order_id = %(order)s AND sku = %(sku)s",
        "status of {sku} on {order}",
    ),
    "line_eta": (
        ValueType.DATE,
        "SELECT eta::text FROM orders.order_lines WHERE order_id = %(order)s AND sku = %(sku)s",
        "ETA of {sku} on {order}",
    ),
    "tracking": (
        ValueType.ID,
        "SELECT tracking_number FROM orders.order_lines WHERE order_id = %(order)s AND sku = %(sku)s",
        "tracking of {sku} on {order}",
    ),
    "invoice_total": (
        ValueType.AMOUNT,
        "SELECT total::text FROM billing.invoices WHERE invoice_id = %(invoice)s",
        "total of {invoice}",
    ),
    "invoice_outstanding": (
        ValueType.AMOUNT,
        "SELECT (total - amount_paid)::text FROM billing.invoices WHERE invoice_id = %(invoice)s",
        "outstanding on {invoice}",
    ),
    "invoice_due": (
        ValueType.DATE,
        "SELECT due_on::text FROM billing.invoices WHERE invoice_id = %(invoice)s",
        "due date of {invoice}",
    ),
    "last_service_date": (
        ValueType.DATE,
        "SELECT max(visit_date)::text FROM service.visits WHERE serial = %(serial)s",
        "last service of {serial}",
    ),
    "warranty_until": (
        ValueType.DATE,
        "SELECT warranty_until::text FROM service.instruments WHERE serial = %(serial)s",
        "warranty end of {serial}",
    ),
}

_FOREIGN_IDS = """
SELECT order_id AS id FROM orders.orders WHERE customer_id <> %(c)s
UNION SELECT tracking_number FROM orders.order_lines l JOIN orders.orders o USING (order_id)
      WHERE o.customer_id <> %(c)s AND tracking_number IS NOT NULL
UNION SELECT invoice_id FROM billing.invoices WHERE customer_id <> %(c)s
UNION SELECT serial FROM service.instruments WHERE customer_id <> %(c)s
UNION SELECT visit_id FROM service.visits v JOIN service.instruments i USING (serial) WHERE i.customer_id <> %(c)s
UNION SELECT customer_id FROM identity.customers WHERE customer_id <> %(c)s
UNION SELECT name FROM identity.customers WHERE customer_id <> %(c)s
"""


class GroundTruth:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def resolve(self, spec: dict[str, Any]) -> Fact:
        kind = str(spec["kind"])
        if kind in ("identifier", "kb_article"):
            return Fact(kind, f"mentions {spec['value']}", ValueType.ID, str(spec["value"]))
        value_type, query, label = _QUERIES[kind]
        async with self._pool.connection() as conn:
            row = await (await conn.execute(query, spec)).fetchone()
        if row is None or next(iter(row.values())) is None:
            raise LookupError(f"ground truth missing for {spec}")
        return Fact(kind, label.format(**spec), value_type, str(next(iter(row.values()))))

    async def customer_of(self, username: str) -> str:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute("SELECT customer_id FROM identity.users WHERE username = %s", (username,))
            ).fetchone()
        if row is None:
            raise LookupError(username)
        return str(row["customer_id"])

    async def foreign_identifiers(self, customer_id: str) -> frozenset[str]:
        """Every identifier and name that belongs to *other* tenants."""
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(_FOREIGN_IDS, {"c": customer_id})).fetchall()
        return frozenset(str(row["id"]) for row in rows)
