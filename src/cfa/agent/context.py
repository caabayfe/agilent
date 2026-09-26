"""Per-request runtime context.

Identity travels here, in LangGraph's runtime context, never in graph state or
in the prompt: it is not checkpointed, not visible to the model, and cannot be
altered by model output.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

AGENT_ID = "customer-assistant"


@dataclass(frozen=True, slots=True)
class RequestContext:
    trace_id: str
    user: str
    customer_id: str
    agent: str
    skill_tokens: Mapping[str, str] = field(repr=False)
    eval_mutant: str | None = None
