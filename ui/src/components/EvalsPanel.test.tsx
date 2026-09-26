import { render, screen } from "@testing-library/react";

import type { EvalsView } from "../types";
import { EvalsPanel } from "./EvalsPanel";

const gate = (id: string, passed: boolean) => ({
  id,
  name: `${id} name`,
  catches: "something",
  passed,
  score: 1,
  threshold: 1,
  detail: `${id} detail`,
  failing_cases: [],
});

const view = (stale: boolean): EvalsView => ({
  baseline: {
    run_id: "r1",
    mode: "fake",
    repeats: 1,
    created_at: "2026-09-26T10:00:00Z",
    decision: {
      decision: "blocked",
      risk_tier: 2,
      tier_sources: { "mcp-billing": 2 },
      mandatory: ["G1", "G2", "G3", "G4"],
      advisory: [],
      reasons: ["G4 Latency budget failed (mandatory at tier 2)"],
    },
    gates: { G1: gate("G1", true), G4: gate("G4", false) },
    canaries: {
      ok: true,
      results: [
        {
          id: "C1",
          case: "billing-outstanding",
          corruption: "Outstanding balance off by EUR 12.40.",
          doctored: "EUR 2,847.60",
          doctored_rejected: true,
          rejected_because: ["unsupported amount: 2847.60"],
          control_accepted: true,
          ok: true,
        },
      ],
    },
    cases: [],
    bindings: { gateway_config_hash: "sha256:abc" },
  },
  mutants: {
    run_id: "m1",
    mode: "fake",
    all_killed: true,
    rows: [
      {
        id: "M2",
        name: "no-tenant-check",
        regression: "tenant check skipped",
        target_gate: "G2",
        killed: true,
        gates: { G2: { passed: false, score: 0.9, failing_cases: ["deny-cross-tenant-order"] } },
      },
    ],
  },
  staleness: stale ? { stale: true, changed: ["gateway_config_hash"] } : { stale: false, changed: [] },
});

describe("EvalsPanel", () => {
  it("shows the computed decision and why", () => {
    render(<EvalsPanel view={view(false)} onRefresh={() => undefined} />);
    expect(screen.getByText("BLOCKED")).toBeInTheDocument();
    expect(screen.getByText(/G4 Latency budget failed/)).toBeInTheDocument();
    expect(screen.queryByText("stale")).not.toBeInTheDocument();
  });

  it("flags the certificate as stale after a gateway change", () => {
    render(<EvalsPanel view={view(true)} onRefresh={() => undefined} />);
    expect(screen.getByText("stale")).toBeInTheDocument();
    expect(screen.getAllByText("gateway_config_hash").length).toBeGreaterThan(0);
  });

  it("shows that the mutant was caught by its target gate", () => {
    render(<EvalsPanel view={view(false)} onRefresh={() => undefined} />);
    expect(screen.getByText("caught")).toBeInTheDocument();
    expect(screen.getByText("1/1 rejected")).toBeInTheDocument();
  });
});
