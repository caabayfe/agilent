"""Calls skill tools on the ``mcp-customer`` server over MCP (streamable HTTP).

Every call runs under ``Resilience`` (timeout, jittered retry of transient
failures, per-skill circuit breaker). The W3C ``traceparent`` is added to every
request by the httpx OpenTelemetry instrumentation, so the server continues the
agent's trace; ``X-Trace-Id`` is kept as a fallback for non-OTel tooling.
"""

import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ReadResourceResult, TextContent
from opentelemetry import trace
from pydantic import AnyUrl

from cfa.agent.context import RequestContext
from cfa.agent.resilience import CircuitOpenError, Resilience
from cfa.telemetry import continue_trace, set_attributes

log = logging.getLogger(__name__)
_TOOL_ERROR_PREFIX = re.compile(r"^Error executing tool \w+: ")
CATALOG_URI = AnyUrl("skills://catalog")


class SkillClient:
    def __init__(self, url: str, resilience: Resilience | None = None) -> None:
        self._url = url
        self.resilience = resilience or Resilience()

    @asynccontextmanager
    async def _session(self, token: str, trace_id: str, eval_mutant: str | None = None) -> AsyncIterator[ClientSession]:
        headers = {"Authorization": f"Bearer {token}", "X-Trace-Id": trace_id}
        if eval_mutant:
            headers["X-Eval-Mutant"] = eval_mutant
        async with (
            httpx.AsyncClient(headers=headers, timeout=self.resilience.attempt_timeout_s) as http,
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

        async def attempt() -> CallToolResult:
            with continue_trace(ctx.trace_id):
                async with self._session(ctx.skill_token, ctx.trace_id, ctx.eval_mutant) as session:
                    return await session.call_tool(tool, args)

        span = trace.get_current_span()
        try:
            result, attempts = await self.resilience.run(skill, attempt)
        except CircuitOpenError:
            log.warning("circuit open for skill %s: failing fast", skill)
            set_attributes(span, {"cfa.circuit.state": "open", "error.type": "circuit_open"})
            return json.dumps({"error": f"The {skill} service is unavailable right now."})
        except (httpx.HTTPError, OSError, TimeoutError, ExceptionGroup) as exc:
            log.warning("skill server unavailable (%s.%s): %s", skill, tool, type(exc).__name__)
            state = self.resilience.breaker(skill).state
            set_attributes(span, {"cfa.circuit.state": state.value, "error.type": type(exc).__name__})
            return json.dumps({"error": f"The {skill} service is unavailable right now."})
        set_attributes(span, {"cfa.retry.attempts": attempts, "cfa.circuit.state": "closed"})

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

        async def attempt() -> ReadResourceResult:
            with continue_trace(trace_id):
                async with self._session(token, trace_id) as session:
                    return await session.read_resource(CATALOG_URI)

        result, _ = await self.resilience.run("catalog", attempt)
        text = "".join(getattr(block, "text", "") for block in result.contents)
        catalog: dict[str, Any] = json.loads(text)
        return catalog
