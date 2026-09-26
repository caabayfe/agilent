from cfa.db import Pool
from cfa.skills.billing.models import Invoice, InvoiceList

_SELECT = (
    "SELECT invoice_id, customer_id, order_id, issued_on, due_on, total, amount_paid, "
    "total - amount_paid AS outstanding, status, currency FROM billing.invoices "
)
_UNPAID = ("open", "partially_paid", "overdue")
MAX_ROWS = 50


class BillingRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def list_invoices(self, customer_id: str, *, unpaid_only: bool) -> InvoiceList:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    _SELECT
                    + "WHERE customer_id = %s AND (NOT %s OR status = ANY(%s)) ORDER BY issued_on DESC LIMIT %s",
                    (customer_id, unpaid_only, list(_UNPAID), MAX_ROWS),
                )
            ).fetchall()
        return InvoiceList(invoices=[Invoice.model_validate(row) for row in rows])

    async def get_invoice(self, invoice_id: str) -> Invoice | None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(_SELECT + "WHERE invoice_id = %s", (invoice_id,))).fetchone()
        return Invoice.model_validate(row) if row else None
