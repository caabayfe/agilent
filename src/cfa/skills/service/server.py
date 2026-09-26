"""Service skill: installed base, service history and troubleshooting KB (MCP server)."""

from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import Context
from pydantic import Field

from cfa.skills.runtime import create_app, create_skill
from cfa.skills.service.models import KbSearchResult, ServiceHistory
from cfa.skills.service.repository import ServiceRepository

mcp, boundary = create_skill(
    Path(__file__).with_name("manifest.yaml"),
    instructions="Read-only access to the customer's instruments, service visits and the troubleshooting KB.",
)


@mcp.tool()
async def get_service_history(
    ctx: Context[Any, Any, Any],
    serial: Annotated[
        str | None, Field(pattern=r"^HX-[A-Z]{2}-\d{4}$", description="Instrument serial, e.g. HX-LC-7781")
    ] = None,
) -> ServiceHistory:
    """Installed instruments with install date, warranty end date and service visits (newest first).
    Omit the serial to get all of the customer's instruments."""
    if serial is None:
        return await boundary.guard(
            ctx,
            "get_service_history",
            {},
            lambda principal, pool: ServiceRepository(pool).history(customer_id=principal.customer_id),
        )
    return await boundary.guard(
        ctx,
        "get_service_history",
        {"serial": serial},
        lambda _principal, pool: ServiceRepository(pool).instrument(serial),
        owner_of=lambda history: history.instruments[0].customer_id,
        not_found=f"Instrument {serial} was not found for your account.",
    )


@mcp.tool()
async def search_troubleshooting(
    ctx: Context[Any, Any, Any],
    query: Annotated[str, Field(min_length=2, max_length=300, description="Symptoms, error code or model")],
) -> KbSearchResult:
    """Search the troubleshooting knowledge base. Returns up to 3 articles with steps and whether to escalate."""
    return await boundary.guard(
        ctx,
        "search_troubleshooting",
        {"query": query},
        lambda _principal, pool: ServiceRepository(pool).search_kb(query),
    )


app = create_app(mcp, boundary)
