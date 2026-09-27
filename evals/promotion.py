"""Promotion decision: a pure function of eval results and the policy file.

* The risk tier is DERIVED from the manifests of every skill the composition is
  bound to or was observed touching; nobody declares it.
* The tier selects which gates are mandatory.
* The decision is bound to hashes of everything that defines this version of the
  agent. Change any of them (prompt, routing, gateway config, dataset...) and the
  certificate is stale until re-evaluated.
"""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

import cfa
from cfa.agent.graph import PROMPTS_DIR
from cfa.agent.routing import DEFAULT_ROUTING, ROUTING_POLICY_VERSION
from cfa.skills import skill_component
from evals.gates import GateResult

EVALS_DIR = Path(__file__).parent
SKILLS_DIR = Path(cfa.__file__).parent / "skills"
AGENT_DIR = Path(cfa.__file__).parent / "agent"


class Decision(StrEnum):
    CERTIFIED = "certified"
    BLOCKED = "blocked"
    HARNESS_INVALID = "harness_invalid"


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    decision: Decision
    risk_tier: int
    mandatory: list[str]
    advisory: list[str]
    reasons: list[str]


def load_policy(path: Path = EVALS_DIR / "promotion_policy.yaml") -> dict[str, Any]:
    policy: dict[str, Any] = yaml.safe_load(path.read_text())
    return policy


def skill_manifests() -> dict[str, dict[str, Any]]:
    """Skill component (e.g. ``mcp-customer/billing``) -> manifest, for every skill.

    Keyed by skill, not by server: several skills share one MCP server, but each
    declares its own risk tier.
    """
    manifests = {}
    for path in sorted(SKILLS_DIR.glob("*/manifest.yaml")):
        manifest: dict[str, Any] = yaml.safe_load(path.read_text())
        manifests[skill_component(manifest["name"])] = manifest
    return manifests


def derive_tier(
    touched_skills: Iterable[str], manifests: Mapping[str, Mapping[str, Any]]
) -> tuple[int, dict[str, int]]:
    sources = {s: int(manifests[s]["risk_tier"]) for s in sorted(set(touched_skills)) if s in manifests}
    return (max(sources.values()) if sources else 1), sources


def decide(
    gates: Mapping[str, GateResult],
    canaries_ok: bool,
    risk_tier: int,
    policy: Mapping[str, Any],
) -> PromotionDecision:
    tier = policy["tiers"][risk_tier]
    mandatory, advisory = list(tier["mandatory"]), list(tier.get("advisory", []))
    if not canaries_ok:
        return PromotionDecision(
            Decision.HARNESS_INVALID,
            risk_tier,
            mandatory,
            advisory,
            ["grader canaries failed: the graders cannot be trusted, so no gate result counts"],
        )
    failed = [g for g in mandatory if not gates[g].passed]
    if failed:
        reasons = [f"{g} {gates[g].name} failed (mandatory at tier {risk_tier}): {gates[g].detail}" for g in failed]
        return PromotionDecision(Decision.BLOCKED, risk_tier, mandatory, advisory, reasons)
    notes = [f"{g} {gates[g].name} failed (advisory at tier {risk_tier})" for g in advisory if not gates[g].passed]
    return PromotionDecision(Decision.CERTIFIED, risk_tier, mandatory, advisory, notes)


def _hash_files(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes() if path.exists() else b"<missing>")
    return "sha256:" + digest.hexdigest()[:16]


def current_bindings(gateway_dir: Path) -> dict[str, str]:
    """Everything the certificate is bound to."""
    routing = ",".join(f"{k.value}={v.value}" for k, v in sorted(DEFAULT_ROUTING.items()))
    return {
        "graph_version": cfa.__version__,
        "prompt_hash": _hash_files(sorted(PROMPTS_DIR.glob("*.md"))),
        "agent_code_hash": _hash_files(sorted(AGENT_DIR.glob("*.py"))),
        "routing_policy": f"{ROUTING_POLICY_VERSION} ({hashlib.sha256(routing.encode()).hexdigest()[:8]})",
        "gateway_config_hash": _hash_files([gateway_dir / "active.yaml"]),
        "skill_manifests_hash": _hash_files(sorted(SKILLS_DIR.glob("*/manifest.yaml"))),
        "dataset_hash": _hash_files(
            [EVALS_DIR / "cases.yaml", EVALS_DIR / "canaries.yaml", EVALS_DIR / "promotion_policy.yaml"]
        ),
    }


def gateway_profile(gateway_dir: Path) -> str:
    marker = gateway_dir / "active.profile"
    return marker.read_text().strip() if marker.exists() else "unknown"
