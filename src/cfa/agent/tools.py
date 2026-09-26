"""LangChain tools that front the MCP skills.

Tool names, descriptions and argument schemas are pinned here, client-side.
The model never sees server-supplied tool descriptions, so a compromised or
changed skill server cannot inject instructions via its tool catalogue
("rug pull"); it can only return data, which the prompt treats as untrusted.
"""

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, tool

from cfa.agent.context import RequestContext
from cfa.agent.skills_client import SkillClient


def build_tools(skills: SkillClient) -> list[BaseTool]:
    @tool
    async def list_orders(runtime: ToolRuntime[RequestContext], open_only: bool = False) -> str:
        """List the customer's orders (newest first) with status and total. Set open_only for undelivered orders."""
        return await skills.call("orders", "list_orders", {"open_only": open_only}, runtime.context)

    @tool
    async def get_order(order_id: str, runtime: ToolRuntime[RequestContext]) -> str:
        """Get one order (e.g. SO-10231) with its lines, per-line status, tracking numbers and ETAs."""
        return await skills.call("orders", "get_order", {"order_id": order_id}, runtime.context)

    @tool
    async def list_invoices(runtime: ToolRuntime[RequestContext], unpaid_only: bool = False) -> str:
        """List the customer's invoices with total, paid, outstanding, status and due date."""
        return await skills.call("billing", "list_invoices", {"unpaid_only": unpaid_only}, runtime.context)

    @tool
    async def get_invoice(invoice_id: str, runtime: ToolRuntime[RequestContext]) -> str:
        """Get one invoice (e.g. INV-4127): total, amount paid, outstanding balance, status, due date."""
        return await skills.call("billing", "get_invoice", {"invoice_id": invoice_id}, runtime.context)

    @tool
    async def get_service_history(runtime: ToolRuntime[RequestContext], serial: str | None = None) -> str:
        """Instruments with install date, warranty end and service visits. Serial like HX-LC-7781, or omit for all."""
        args = {"serial": serial} if serial else {}
        return await skills.call("service", "get_service_history", args, runtime.context)

    @tool
    async def search_troubleshooting(query: str, runtime: ToolRuntime[RequestContext]) -> str:
        """Search the troubleshooting knowledge base by symptoms, error code (e.g. E-217) or instrument model."""
        return await skills.call("service", "search_troubleshooting", {"query": query}, runtime.context)

    return [list_orders, get_order, list_invoices, get_invoice, get_service_history, search_troubleshooting]
