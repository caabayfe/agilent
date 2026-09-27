"""Agent middleware: dynamic model selection and audit/lineage capture."""

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NotRequired
from urllib.parse import urlparse

import openai
from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from cfa.agent.context import AGENT_ID as COMPONENT
from cfa.agent.context import RequestContext
from cfa.agent.refusals import REFUSED, is_provider_refusal, refusal_message
from cfa.audit import Action, AuditEvent, AuditLog, content_hash
from cfa.telemetry import chat_span, record_model_response, set_attributes, tool_span


class AssistantState(AgentState[Any]):
    """Graph state: conversation plus the router's decision."""

    intent: NotRequired[str]
    model_alias: NotRequired[str]


def model_metadata(message: AIMessage) -> tuple[str | None, str | None]:
    """(resolved deployment, provider host) as reported by the gateway.

    The response body only echoes the alias; the gateway's headers say which
    deployment actually answered and whether it had to fail over.
    """
    metadata = message.response_metadata
    headers: Mapping[str, str] = metadata.get("headers") or {}
    api_base = headers.get("x-litellm-model-api-base")
    provider = urlparse(api_base).hostname if api_base else None
    resolved = headers.get("x-litellm-model-name") or metadata.get("model_name")
    if resolved and headers.get("x-litellm-attempted-fallbacks", "0") not in ("0", ""):
        resolved = f"{resolved} (failover)"
    return resolved, provider


def _last_ai(messages: list[BaseMessage]) -> AIMessage | None:
    return next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)


class ModelByAliasMiddleware(AgentMiddleware[AssistantState, RequestContext]):
    """Serve each model call with the alias chosen by the router node."""

    state_schema = AssistantState

    def __init__(self, models: Mapping[str, BaseChatModel], default_alias: str) -> None:
        super().__init__()
        self._models = models
        self._default = default_alias

    async def awrap_model_call(
        self,
        request: ModelRequest[RequestContext],
        handler: Callable[[ModelRequest[RequestContext]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        alias = str(request.state.get("model_alias") or self._default)
        return await handler(request.override(model=self._models[alias]))


class AuditMiddleware(AgentMiddleware[AssistantState, RequestContext]):
    """One audit event and one OpenTelemetry GenAI span per model call and per tool
    call; the audit row stores the span id, so the two views join exactly."""

    state_schema = AssistantState

    def __init__(self, audit: AuditLog, default_alias: str) -> None:
        super().__init__()
        self._audit = audit
        self._default = default_alias

    async def awrap_model_call(
        self,
        request: ModelRequest[RequestContext],
        handler: Callable[[ModelRequest[RequestContext]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        ctx = request.runtime.context
        alias = str(request.state.get("model_alias") or self._default)
        prompt = [m.model_dump(include={"type", "content", "tool_calls"}) for m in request.messages]
        with chat_span(alias) as span:
            started = time.perf_counter()
            decision, reason = "ok", None
            try:
                response = await handler(request)
            except openai.BadRequestError as exc:
                if not is_provider_refusal(exc):
                    raise
                response = ModelResponse(result=[refusal_message(alias)])
                decision, reason = REFUSED, "blocked by the provider's content policy"
            latency_ms = int((time.perf_counter() - started) * 1000)
            ai = _last_ai(response.result)
            resolved, provider = model_metadata(ai) if ai else (None, None)
            usage = ai.usage_metadata if ai else None
            tokens_in, tokens_out = (usage["input_tokens"], usage["output_tokens"]) if usage else (None, None)
            record_model_response(span, resolved=resolved, host=provider, tokens_in=tokens_in, tokens_out=tokens_out)
            await self._audit.record(
                AuditEvent(
                    trace_id=ctx.trace_id,
                    component=COMPONENT,
                    action=Action.MODEL_CALL,
                    name=alias,
                    actor_user=ctx.user,
                    actor_agent=ctx.agent,
                    decision=decision,
                    reason=reason,
                    model_alias=alias,
                    model_resolved=resolved,
                    provider=provider,
                    input={
                        "messages": len(request.messages),
                        "prompt_hash": content_hash(prompt),
                        "system_prompt_hash": content_hash(
                            request.system_message.text if request.system_message else ""
                        ),
                        "tools": sorted(getattr(t, "name", "") for t in request.tools),
                    },
                    output={
                        "content": ai.text if ai else "",
                        "tool_calls": [{"name": c["name"], "args": c["args"]} for c in (ai.tool_calls if ai else [])],
                    },
                    latency_ms=latency_ms,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )
            )
        return response

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        ctx: object = request.runtime.context  # ToolCallRequest types context loosely
        if not isinstance(ctx, RequestContext):  # fail closed: no identity, no tool call
            raise TypeError("tool call without a RequestContext")
        with tool_span(request.tool_call["name"]) as span:
            started = time.perf_counter()
            result = await handler(request)
            latency_ms = int((time.perf_counter() - started) * 1000)
            content = result.text if isinstance(result, ToolMessage) else ""
            decision = "error" if content.startswith('{"error"') else "ok"
            set_attributes(span, {"cfa.decision": decision})
            await self._audit.record(
                AuditEvent(
                    trace_id=ctx.trace_id,
                    component=COMPONENT,
                    action=Action.TOOL_CALL,
                    name=request.tool_call["name"],
                    actor_user=ctx.user,
                    actor_agent=ctx.agent,
                    decision=decision,
                    input={"args": request.tool_call["args"]},
                    output={"content": content},
                    output_hash=content_hash(content),
                    latency_ms=latency_ms,
                )
            )
        return result
