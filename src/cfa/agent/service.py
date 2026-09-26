"""Application service: one customer question -> one audited agent run.

Used by both the BFF and the eval harness, so what is evaluated is exactly what
is served.
"""

import time
import uuid
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer

from cfa.agent.context import AGENT_ID, RequestContext
from cfa.agent.graph import AgentConfig, build_graph
from cfa.agent.middleware import AssistantState, model_metadata
from cfa.agent.skills_client import SkillClient, skill_endpoints
from cfa.audit import Action, AuditEvent, AuditLog, content_hash
from cfa.config import AgentSettings, IdentitySettings
from cfa.identity.client import IdpClient
from cfa.identity.tokens import UserClaims

RECURSION_LIMIT = 16


@dataclass(frozen=True, slots=True)
class AskResult:
    trace_id: str
    answer: str
    intent: str | None
    model_alias: str | None
    model_resolved: str | None
    latency_ms: int


class AssistantService:
    def __init__(
        self,
        graph: CompiledStateGraph[AssistantState, RequestContext, Any, Any],
        audit: AuditLog,
        idp: IdpClient,
    ) -> None:
        self._graph = graph
        self._audit = audit
        self._idp = idp

    async def ask(
        self,
        *,
        user_token: str,
        user: UserClaims,
        question: str,
        thread_id: str,
        eval_mutant: str | None = None,
    ) -> AskResult:
        trace_id = uuid.uuid4().hex
        started = time.perf_counter()
        # The agent trades the user's token for one delegated token per skill.
        skill_tokens = await self._idp.exchange_for_skills(user_token)
        context = RequestContext(
            trace_id=trace_id,
            user=user.sub,
            customer_id=user.customer_id,
            agent=AGENT_ID,
            skill_tokens=skill_tokens,
            eval_mutant=eval_mutant,
        )
        state = await self._graph.ainvoke(
            {"messages": [HumanMessage(question)]},
            config={"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT},
            context=context,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        final = next((m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None)
        answer = final.text if final else ""
        resolved, provider = model_metadata(final) if final else (None, None)
        await self._audit.record(
            AuditEvent(
                trace_id=trace_id,
                component=AGENT_ID,
                action=Action.ANSWER,
                actor_user=user.sub,
                actor_agent=AGENT_ID,
                decision="ok",
                model_alias=state.get("model_alias"),
                model_resolved=resolved,
                provider=provider,
                input={"question": question, "thread_id": thread_id},
                output={"answer": answer},
                output_hash=content_hash(answer),
                latency_ms=latency_ms,
            )
        )
        return AskResult(
            trace_id=trace_id,
            answer=answer,
            intent=state.get("intent"),
            model_alias=state.get("model_alias"),
            model_resolved=resolved,
            latency_ms=latency_ms,
        )


def build_service(
    *,
    settings: AgentSettings,
    identity: IdentitySettings,
    audit: AuditLog,
    config: AgentConfig | None = None,
    checkpointer: Checkpointer | None = None,
) -> AssistantService:
    """Single composition root shared by the BFF and the eval harness."""
    skills = SkillClient(skill_endpoints(settings.orders_mcp_url, settings.billing_mcp_url, settings.service_mcp_url))
    graph = build_graph(settings=settings, audit=audit, skills=skills, config=config, checkpointer=checkpointer)
    idp = IdpClient(identity.idp_url, identity.agent_client_id, identity.agent_client_secret.get_secret_value())
    return AssistantService(graph, audit, idp)
