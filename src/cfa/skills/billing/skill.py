"""Billing skill: invoices and balances. Registered on the shared ``mcp-customer`` server."""

from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from cfa.skills.billing.models import Invoice, InvoiceList
from cfa.skills.billing.repository import BillingRepository
from cfa.skills.runtime import Boundary, SkillModule


def register(mcp: FastMCP, boundary: Boundary) -> None:
    @boundary.tool(mcp)
    async def list_invoices(
        ctx: Context[Any, Any, Any],
        unpaid_only: Annotated[bool, Field(description="Only invoices with an outstanding balance")] = False,
    ) -> InvoiceList:
        """List the customer's invoices with total, amount paid, outstanding balance, status and due date."""
        return await boundary.guard(
            ctx,
            "list_invoices",
            {"unpaid_only": unpaid_only},
            lambda principal, pool: BillingRepository(pool).list_invoices(
                principal.customer_id, unpaid_only=unpaid_only
            ),
        )

    @boundary.tool(mcp)
    async def get_invoice(
        ctx: Context[Any, Any, Any],
        invoice_id: Annotated[str, Field(pattern=r"^INV-\d{4}$", description="Invoice number, e.g. INV-4127")],
    ) -> Invoice:
        """Get one invoice: total, amount paid, outstanding balance, status, issue and due dates."""
        return await boundary.guard(
            ctx,
            "get_invoice",
            {"invoice_id": invoice_id},
            lambda _principal, pool: BillingRepository(pool).get_invoice(invoice_id),
            owner_of=lambda invoice: invoice.customer_id,
            not_found=f"Invoice {invoice_id} was not found for your account.",
        )


MODULE = SkillModule(Path(__file__).with_name("manifest.yaml"), register)
