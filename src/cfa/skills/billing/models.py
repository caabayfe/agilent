from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class Invoice(BaseModel):
    invoice_id: str
    customer_id: str
    order_id: str
    issued_on: date
    due_on: date
    total: Decimal
    amount_paid: Decimal
    outstanding: Decimal
    status: str
    currency: str


class InvoiceList(BaseModel):
    invoices: list[Invoice]
