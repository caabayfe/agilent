from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class OrderSummary(BaseModel):
    order_id: str
    placed_on: date
    status: str
    total: Decimal
    currency: str


class OrderLine(BaseModel):
    line_no: int
    sku: str
    description: str
    quantity: int
    status: str
    tracking_number: str | None
    eta: date | None


class OrderDetail(OrderSummary):
    customer_id: str
    lines: list[OrderLine]


class OrderList(BaseModel):
    orders: list[OrderSummary]
