"""Calls skill tools over MCP (streamable HTTP) with the per-audience delegated token."""

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from cfa.agent.context import RequestContext

log = logging.getLogger(__name__)
_TOOL_ERROR_PREFIX = re.compile(r"^Error executing tool \w+: ")


@dataclass(frozen=True, slots=True)
class SkillEndpoint:
    name: str
    audience: str
    url: str


class SkillClient:
    def __init__(self, endpoints: Mapping[str, SkillEndpoint], timeout_s: float = 15.0) -> None:
        self._endpoints = endpoints
        self._timeout_s = timeout_s

    async def call(self, skill: str, tool: str, args: dict[str, Any], ctx: RequestContext) -> str:
        """Invoke ``tool`` and return a JSON string for the model.

        Errors (denials, not-found, outages) are returned as ``{"error": ...}`` so
        the model can explain them; they are never raised into the graph.
        """
        endpoint = self._endpoints[skill]
        headers = {
            "Authorization": f"Bearer {ctx.skill_tokens[endpoint.audience]}",
            "X-Trace-Id": ctx.trace_id,
        }
        if ctx.eval_mutant:
            headers["X-Eval-Mutant"] = ctx.eval_mutant
        try:
            async with (
                httpx.AsyncClient(headers=headers, timeout=self._timeout_s) as http,
                streamable_http_client(endpoint.url, http_client=http) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool(tool, args)
        except (httpx.HTTPError, OSError, ExceptionGroup) as exc:
            log.warning("skill %s unavailable: %s", skill, type(exc).__name__)
            return json.dumps({"error": f"The {skill} service is unavailable right now."})

        text = "".join(block.text for block in result.content if isinstance(block, TextContent))
        if result.isError:
            message = _TOOL_ERROR_PREFIX.sub("", text) or "The request failed."
            return json.dumps({"error": message})
        if result.structuredContent is not None:
            return json.dumps(result.structuredContent)
        return text


def skill_endpoints(orders_url: str, billing_url: str, service_url: str) -> dict[str, SkillEndpoint]:
    return {
        "orders": SkillEndpoint("orders", "mcp-orders", orders_url),
        "billing": SkillEndpoint("billing", "mcp-billing", billing_url),
        "service": SkillEndpoint("service", "mcp-service", service_url),
    }
