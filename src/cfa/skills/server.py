"""``mcp-customer``: one MCP server for the customer-data bounded context.

Three skills - orders, billing, service - share this process, its token audience
and its transport. Each keeps its own manifest, database role, agent scope, user
role, risk tier and audit identity. Run with ``uvicorn cfa.skills.server:app``.
"""

from cfa.skills.billing.skill import MODULE as BILLING
from cfa.skills.orders.skill import MODULE as ORDERS
from cfa.skills.runtime import create_app, create_server
from cfa.skills.service.skill import MODULE as SERVICE

mcp, boundaries = create_server(
    [ORDERS, BILLING, SERVICE],
    instructions=(
        "Read-only access to the signed-in customer's data, organised in skills: "
        "orders (orders and shipments), billing (invoices and balances) and service "
        "(instruments, service visits, troubleshooting KB). Read skills://catalog for details."
    ),
)
app = create_app(mcp, boundaries)
