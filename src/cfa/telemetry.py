"""OpenTelemetry tracing shared by every service.

* One W3C trace per customer question: the BFF starts it, ``traceparent`` is
  propagated on every outbound HTTP call (IdP, model gateway, MCP), and the skill
  server continues it. The audit ``trace_id`` IS the OTel trace id, and each audit
  row stores the ``span_id`` that produced it, so lineage and traces join 1:1.
* Spans follow the OpenTelemetry GenAI semantic conventions (``invoke_agent``,
  ``chat``, ``execute_tool``) so any GenAI-aware backend (Jaeger here; Azure
  Monitor / Foundry tracing or AgentCore Observability in production) renders them.
* Export is OTLP/HTTP and only enabled when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set;
  ids are generated regardless, so the audit trail never depends on the exporter.
  httpx is instrumented, so ``traceparent`` rides on every outbound request.
* Content (prompts, answers, tool results) is NOT put on spans: it lives in the
  access-controlled audit store. Spans carry ids, models, token counts and decisions.
"""

import os
import random
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import context, propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import NonRecordingSpan, Span, SpanContext, SpanKind, TraceFlags

TRACER_NAME = "cfa"
_configured = False


def configure_tracing(default_service: str) -> None:
    """Install the global tracer provider once per process (idempotent)."""
    global _configured  # noqa: PLW0603 - process-wide, like the OTel global provider itself
    if _configured:
        return
    service = os.environ.get("OTEL_SERVICE_NAME", default_service)
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service}))
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _instrument_httpx()
    _configured = True


def _instrument_httpx() -> None:
    instrumentor = HTTPXClientInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument()


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def trace_id_hex(span: Span | None = None) -> str | None:
    ctx = (span or trace.get_current_span()).get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def span_id_hex(span: Span | None = None) -> str | None:
    ctx = (span or trace.get_current_span()).get_span_context()
    return format(ctx.span_id, "016x") if ctx.is_valid else None


def set_attributes(span: Span, attributes: dict[str, Any]) -> None:
    span.set_attributes({k: v for k, v in attributes.items() if v is not None})


def remote_trace_id(headers: Mapping[str, str]) -> str | None:
    """Trace id carried by an incoming W3C ``traceparent`` header, if any."""
    ctx = trace.get_current_span(propagate.extract(dict(headers))).get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


@contextmanager
def continue_trace(trace_id: str) -> Iterator[None]:
    """Make outbound calls carry ``trace_id`` in their W3C context.

    A no-op inside the agent (the current span already has that trace id); for
    callers without an active span (tests, CLIs) it joins the given trace, so the
    skill server's audit rows always land under the caller's trace id.
    """
    current = trace.get_current_span().get_span_context()
    if (current.is_valid and format(current.trace_id, "032x") == trace_id) or len(trace_id) != 32:
        yield
        return
    try:
        parent = SpanContext(
            trace_id=int(trace_id, 16),
            span_id=random.getrandbits(64),
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
    except ValueError:
        yield
        return
    token = context.attach(trace.set_span_in_context(NonRecordingSpan(parent)))
    try:
        yield
    finally:
        context.detach(token)


# --- GenAI semantic-convention spans ----------------------------------------------


@contextmanager
def agent_span(agent: str, conversation_id: str) -> Iterator[Span]:
    with tracer().start_as_current_span(
        f"invoke_agent {agent}",
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.id": agent,
            "gen_ai.agent.name": agent,
            "gen_ai.conversation.id": conversation_id,
        },
    ) as span:
        yield span


@contextmanager
def chat_span(alias: str) -> Iterator[Span]:
    """One model call through the gateway. ``request.model`` is the alias the agent
    asked for; ``response.model`` is the deployment that actually answered."""
    with tracer().start_as_current_span(
        f"chat {alias}",
        kind=SpanKind.CLIENT,
        attributes={"gen_ai.operation.name": "chat", "gen_ai.provider.name": "openai", "gen_ai.request.model": alias},
    ) as span:
        yield span


def record_model_response(
    span: Span, *, resolved: str | None, host: str | None, tokens_in: int | None, tokens_out: int | None
) -> None:
    set_attributes(
        span,
        {
            "gen_ai.response.model": resolved,
            "server.address": host,
            "gen_ai.usage.input_tokens": tokens_in,
            "gen_ai.usage.output_tokens": tokens_out,
        },
    )


@contextmanager
def tool_span(name: str) -> Iterator[Span]:
    with tracer().start_as_current_span(
        f"execute_tool {name}",
        attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name, "gen_ai.tool.type": "function"},
    ) as span:
        yield span
