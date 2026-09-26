"""Calls skill tools on the ``mcp-customer`` server over MCP (streamable HTTP)."""

import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from pydantic import AnyUrl

from cfa.agent.context import RequestContext

log = logging.getLogger(__name__)
_TOOL_ERROR_PREFIX = re.compile(r"^Error executing tool \w+: ")
CATALOG_URI = AnyUrl("skills://catalog")


class SkillClient:
    def __init__(self, url: str, timeout_s: float = 15.0) -> None:
        self._url = url
        self._timeout_s = timeout_s

    @asynccontextmanager
    async def _session(self, token: str, trace_id: str, eval_mutant: str | None = None) -> AsyncIterator[ClientSession]:
        headers = {"Authorization": f"Bearer {token}", "X-Trace-Id": trace_id}
        if eval_mutant:
            headers["X-Eval-Mutant"] = eval_mutant
        async with (
            httpx.AsyncClient(headers=headers, timeout=self._timeout_s) as http,
            streamable_http_client(self._url, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    async def call(self, skill: str, tool: str, args: dict[str, Any], ctx: RequestContext) -> str:
        """Invoke ``tool`` (owned by ``skill``) and return a JSON string for the model.

        Errors (denials, not-found, outages) are returned as ``{"error": ...}`` so
        the model can explain them; they are never raised into the graph.
        """
        try:
            async with self._session(ctx.skill_token, ctx.trace_id, ctx.eval_mutant) as session:
                result = await session.call_tool(tool, args)
        except (httpx.HTTPError, OSError, ExceptionGroup) as exc:
            log.warning("skill server unavailable (%s.%s): %s", skill, tool, type(exc).__name__)
            return json.dumps({"error": f"The {skill} service is unavailable right now."})

        text = "".join(block.text for block in result.content if isinstance(block, TextContent))
        if result.isError:
            message = _TOOL_ERROR_PREFIX.sub("", text) or "The request failed."
            return json.dumps({"error": message})
        if result.structuredContent is not None:
            return json.dumps(result.structuredContent)
        return text

    async def catalog(self, token: str, trace_id: str) -> dict[str, Any]:
        """The server's skill catalogue, as seen by the principal in ``token``.

        Used for display only; the agent's tools stay pinned client-side (tools.py).
        """
        async with self._session(token, trace_id) as session:
            result = await session.read_resource(CATALOG_URI)
        text = "".join(getattr(block, "text", "") for block in result.contents)
        catalog: dict[str, Any] = json.loads(text)
        return catalog
