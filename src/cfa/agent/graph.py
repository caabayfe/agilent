"""The Customer Facing Assistant graph.

    START -> route -> assistant -> END

* ``route``: one structured-output call on the fast model classifies intent, then
  the pure ``choose_alias`` policy picks a model alias.
* ``assistant``: a LangChain ``create_agent`` ReAct subgraph whose model is
  selected per request by ``ModelByAliasMiddleware``; every model and tool call is
  audited by ``AuditMiddleware``; built-in limits stop runaway loops.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Checkpointer

from cfa.agent.context import AGENT_ID, RequestContext
from cfa.agent.middleware import AssistantState, AuditMiddleware, ModelByAliasMiddleware, model_metadata
from cfa.agent.routing import (
    DEFAULT_ROUTING,
    ROUTING_POLICY_VERSION,
    Intent,
    IntentClassification,
    ModelAlias,
    RoutingTable,
    choose_alias,
)
from cfa.agent.skills_client import SkillClient
from cfa.agent.tools import build_tools
from cfa.audit import Action, AuditEvent, AuditLog
from cfa.config import AgentSettings

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Everything that defines *this version* of the agent (and is bound into its
    certification record). Eval mutants override fields here."""

    system_prompt_path: Path = PROMPTS_DIR / "system.md"
    routing_table: RoutingTable = DEFAULT_ROUTING
    # Map a logical alias to a different gateway alias (used by the M4 mutant).
    alias_overrides: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def system_prompt(self) -> str:
        return self.system_prompt_path.read_text()


def _chat_model(settings: AgentSettings, gateway_alias: str) -> ChatOpenAI:
    """Every model is reached through the gateway, by alias. No vendor SDK here."""
    return ChatOpenAI(
        model=gateway_alias,
        base_url=settings.gateway_url,
        api_key=settings.gateway_api_key,
        timeout=settings.gateway_timeout_s,
        max_retries=1,
        use_responses_api=False,
        include_response_headers=True,
    )


def build_graph(
    *,
    settings: AgentSettings,
    audit: AuditLog,
    skills: SkillClient,
    config: AgentConfig | None = None,
    checkpointer: Checkpointer | None = None,
) -> CompiledStateGraph[AssistantState, RequestContext, Any, Any]:
    config = config or AgentConfig()
    models = {
        alias.value: _chat_model(settings, config.alias_overrides.get(alias.value, alias.value)) for alias in ModelAlias
    }
    classifier = models[ModelAlias.FAST].with_structured_output(IntentClassification, include_raw=True)
    classifier_prompt = (PROMPTS_DIR / "classifier.md").read_text()

    async def route(state: AssistantState, runtime: Runtime[RequestContext]) -> dict[str, Any]:
        ctx = runtime.context
        question = next(m for m in reversed(state["messages"]) if isinstance(m, HumanMessage))
        started = time.perf_counter()
        result: dict[str, Any] = await classifier.ainvoke([SystemMessage(classifier_prompt), question])
        latency_ms = int((time.perf_counter() - started) * 1000)
        parsed: IntentClassification | None = result["parsed"]
        intent = parsed.intent if parsed else Intent.OTHER
        alias = choose_alias(intent, config.routing_table)
        raw: AIMessage = result["raw"]
        resolved, provider = model_metadata(raw)
        await audit.record(
            AuditEvent(
                trace_id=ctx.trace_id,
                component=AGENT_ID,
                action=Action.ROUTE,
                name=alias.value,
                actor_user=ctx.user,
                actor_agent=ctx.agent,
                decision=intent.value,
                reason=f"intent={intent.value} -> alias={alias.value}",
                model_alias=ModelAlias.FAST.value,
                model_resolved=resolved,
                provider=provider,
                input={"question": question.text},
                output={"intent": intent.value, "alias": alias.value},
                latency_ms=latency_ms,
                tokens_in=raw.usage_metadata["input_tokens"] if raw.usage_metadata else None,
                tokens_out=raw.usage_metadata["output_tokens"] if raw.usage_metadata else None,
                policy_version=ROUTING_POLICY_VERSION,
            )
        )
        return {"intent": intent.value, "model_alias": alias.value}

    middleware: list[AgentMiddleware[Any, RequestContext]] = [
        ModelByAliasMiddleware(models, ModelAlias.FAST.value),
        AuditMiddleware(audit, ModelAlias.FAST.value),
        ModelCallLimitMiddleware(run_limit=6, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=4),
    ]
    assistant = create_agent(
        model=models[ModelAlias.FAST],
        tools=build_tools(skills),
        system_prompt=config.system_prompt,
        middleware=middleware,
        state_schema=AssistantState,
        context_schema=RequestContext,
        name="customer_assistant",
    )

    graph = StateGraph(AssistantState, context_schema=RequestContext)
    graph.add_node("route", route)
    graph.add_node("assistant", assistant)
    graph.add_edge(START, "route")
    graph.add_edge("route", "assistant")
    graph.add_edge("assistant", END)
    return graph.compile(checkpointer=checkpointer, name="customer_facing_assistant")
