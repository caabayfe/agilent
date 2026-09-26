"""Mutation runs: prove each gate earns its place.

Each mutant injects ONE realistic regression. The harness must catch it with the
gate that exists for that failure. A gate that never fails on any mutant is
decoration and should be removed or fixed.
"""

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from cfa.agent.graph import AgentConfig
from cfa.agent.routing import DEFAULT_ROUTING, Intent, ModelAlias

MUTANTS_DIR = Path(__file__).parent / "mutants"


@dataclass(frozen=True, slots=True)
class Mutant:
    id: str
    name: str
    regression: str
    target_gate: str
    config: AgentConfig = field(default_factory=AgentConfig)
    eval_mutant_header: str | None = None


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        "M1",
        "prompt-sloppy",
        "A 'friendlier' prompt edit asks the model to round amounts.",
        "G1",
        AgentConfig(system_prompt_path=MUTANTS_DIR / "system_sloppy.md"),
    ),
    Mutant(
        "M2",
        "no-tenant-check",
        "The billing/orders skills skip the tenant check (bug in a skill release).",
        "G2",
        eval_mutant_header="bypass-tenant",
    ),
    Mutant(
        "M3",
        "router-flip",
        "Routing table edit sends troubleshooting to the fast model and billing to the reasoning model.",
        "G3",
        AgentConfig(
            routing_table=MappingProxyType(
                {**DEFAULT_ROUTING, Intent.TROUBLESHOOTING: ModelAlias.FAST, Intent.BILLING: ModelAlias.REASONING}
            )
        ),
    ),
    Mutant(
        "M4",
        "weak-model",
        "Gateway alias re-pointed to a cheaper model that looks fluent but corrupts facts.",
        "G1",
        AgentConfig(
            alias_overrides=MappingProxyType(
                {ModelAlias.FAST.value: "eval-weak", ModelAlias.REASONING.value: "eval-weak"}
            )
        ),
    ),
)
