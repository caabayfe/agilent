import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import type { MatrixCase, MatrixResult, ModelMatrix } from "../types";
import { MatrixPage } from "./MatrixPage";

const gate = (passed: boolean, score: number) => ({
  passed,
  score,
  threshold: 0.95,
  detail: "",
  failing_cases: [],
});
const ok: MatrixCase = {
  passed: true,
  failed_gates: [],
  repeats_passed: 3,
  repeats: 3,
  notes: [],
  sample_answer: "KB-101: fine",
};
const bad: MatrixCase = {
  passed: false,
  failed_gates: ["G1"],
  repeats_passed: 2,
  repeats: 3,
  notes: ["G1: unsupported identifier: SO-10231"],
  sample_answer: "If order SO-10231 persists",
  sample_model: "azure/gpt-5.4",
};

const result = (name: string, cases: Record<string, MatrixCase>, g1: boolean): MatrixResult => ({
  name,
  change: { profile: name },
  profile: name,
  aliases: {
    "assistant-fast": { models: ["azure/gpt-4.1-mini"], runs: 3, p50_ms: 2000, p95_ms: 3000 },
    "assistant-reasoning": { models: ["azure/gpt-5.4"], runs: 3, p50_ms: 4000, p95_ms: 6500 },
  },
  decision: "blocked",
  reasons: [],
  gates: {
    G1: gate(g1, g1 ? 1 : 0.93),
    G2: gate(true, 1),
    G3: gate(true, 1),
    G4: { ...gate(false, 13000), threshold: 12000 },
  },
  canaries_ok: true,
  tokens_in: 1000,
  tokens_out: 100,
  model_calls: 10,
  cases,
  repeats: 3,
  duration_s: 120,
});

const matrix: ModelMatrix = {
  created_at: "2026-09-27T10:00:00+00:00",
  repeats: 3,
  case_ids: ["orders-open", "troubleshoot-e217"],
  complete: true,
  results: [
    result("live", { "orders-open": ok, "troubleshoot-e217": bad }, false),
    {
      ...result("gpt-4o", { "orders-open": ok, "troubleshoot-e217": ok }, true),
      aliases: {
        "assistant-fast": { models: ["azure/gpt-4o"], refusals: 3, runs: 60, p50_ms: 2000, p95_ms: 3000 },
        "assistant-reasoning": { models: ["azure/gpt-4o"], runs: 3, p50_ms: 2000, p95_ms: 3000 },
      },
      history: [
        {
          run_id: "20260927T102123Z",
          decision: "blocked",
          gates: { G1: false },
          failed_cases: { "orders-open": ["G1: run crashed: ValidationError"] },
        },
      ],
    },
    { name: "o4-mini", change: { profile: "x" }, error: "gateway did not start" },
  ],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("MatrixPage", () => {
  it("shows gates per combination, failed runs, and the evidence for a failing case", async () => {
    vi.spyOn(api, "matrix").mockResolvedValue({ matrix });
    render(<MatrixPage onBack={vi.fn()} />);
    expect(await screen.findByText("FAIL 0.93")).toBeInTheDocument();
    expect(screen.getAllByText("FAIL 13.0 s / 12.0 s")).toHaveLength(2);
    expect(screen.getByText("Run failed: gateway did not start")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "troubleshoot-e217 on live" }));
    const evidence = screen.getByRole("region", { name: "Case evidence" });
    expect(evidence).toHaveTextContent("unsupported identifier: SO-10231");
    expect(evidence).toHaveTextContent("answered by azure/gpt-5.4");
  });

  it("counts content-filter refusals apart from models and keeps earlier attempts", async () => {
    vi.spyOn(api, "matrix").mockResolvedValue({ matrix });
    render(<MatrixPage onBack={vi.fn()} />);
    expect(await screen.findByText("(3 refused by content filter)")).toBeInTheDocument();
    expect(screen.getByText("20260927T102123Z")).toBeInTheDocument();
    expect(screen.getByText("orders-open: G1: run crashed: ValidationError")).toBeInTheDocument();
  });

  it("explains how to produce a matrix when there is none", async () => {
    vi.spyOn(api, "matrix").mockResolvedValue({ matrix: null });
    render(<MatrixPage onBack={vi.fn()} />);
    expect(await screen.findByText(/No matrix yet/)).toBeInTheDocument();
  });
});
