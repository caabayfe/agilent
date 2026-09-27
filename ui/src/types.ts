export interface Persona {
  username: string;
  display_name: string;
  title: string;
  customer_id: string;
  customer_name: string;
  roles: string[];
}

export interface User {
  sub: string;
  name: string;
  customer_id: string;
  customer_name: string;
  roles: string[];
}

export interface Session {
  token: string;
  user: User;
}

export interface ChatAnswer {
  trace_id: string;
  answer: string;
  intent: string | null;
  model_alias: string | null;
  model_resolved: string | null;
  latency_ms: number;
}

export type Message =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "assistant"; text: string; meta: ChatAnswer }
  | { id: string; role: "error"; text: string };

export type AuditAction = "route" | "model_call" | "tool_call" | "access_decision" | "answer";

export interface AuditEvent {
  event_id: string;
  ts: string;
  seq: number;
  prev_hash: string;
  hash: string;
  trace_id: string;
  span_id: string | null;
  component: string;
  action: AuditAction;
  name: string | null;
  actor_user: string | null;
  actor_agent: string | null;
  decision: string | null;
  reason: string | null;
  model_alias: string | null;
  model_resolved: string | null;
  provider: string | null;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  output_hash: string | null;
  latency_ms: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  policy_version: string | null;
}

export interface GatewayInfo {
  profile: string;
  aliases: Record<string, { model: string; api_base_host: string }[]>;
  fallbacks: Record<string, string[]>[];
  admin_enabled?: boolean;
}

export interface CatalogModel {
  id: string;
  label: string;
  vendor: string;
  notes: string;
  resolves_to: string;
  api_base_host: string;
  available: boolean;
  missing: string[];
}

export interface GatewayCatalog {
  profile: string;
  aliases: string[];
  models: CatalogModel[];
  /** alias -> catalog model id; null when the alias points at something outside the catalog. */
  selection: Record<string, string | null>;
  profiles: { name: string; missing: string[] }[];
  busy: boolean;
}

export type GatewayChange = { profile: string } | { selection: Record<string, string> };

export interface GateResult {
  id: string;
  name: string;
  catches: string;
  passed: boolean;
  score: number;
  threshold: number;
  detail: string;
  failing_cases: string[];
}

export interface CanaryResult {
  id: string;
  case: string;
  corruption: string;
  doctored: string;
  doctored_rejected: boolean;
  rejected_because: string[];
  control_accepted: boolean;
  ok: boolean;
}

export interface BaselineReport {
  run_id: string;
  mode: string;
  repeats: number;
  created_at: string;
  decision: {
    decision: "certified" | "blocked" | "harness_invalid";
    risk_tier: number;
    tier_sources: Record<string, number>;
    mandatory: string[];
    advisory: string[];
    reasons: string[];
  };
  gates: Record<string, GateResult>;
  canaries: { ok: boolean; results: CanaryResult[] };
  cases: { id: string; persona: string; risk: string; gates: Record<string, boolean | null> }[];
  bindings: Record<string, string>;
}

export interface MutantRow {
  id: string;
  name: string;
  regression: string;
  target_gate: string | null;
  killed: boolean | null;
  gates: Record<string, { passed: boolean; score: number; failing_cases: string[] }>;
}

export interface MutantsReport {
  run_id: string;
  mode: string;
  rows: MutantRow[];
  all_killed: boolean;
}

export interface EvalsView {
  baseline: BaselineReport | null;
  mutants: MutantsReport | null;
  staleness: { stale: boolean; changed: string[] };
}

export interface SkillTool {
  name: string;
  description: string | null;
  input_schema: { properties?: Record<string, { description?: string }> };
}

export interface Skill {
  name: string;
  title: string;
  description: string;
  component: string;
  risk_tier: number;
  required_agent_scope: string;
  required_user_role: string;
  examples: string[];
  enabled: boolean;
  access: { allowed: boolean; reason: string };
  tools: SkillTool[];
}

export interface SkillCatalog {
  server: string;
  skills: Skill[];
}

export interface MatrixGate {
  passed: boolean;
  score: number;
  threshold: number;
  detail: string;
  failing_cases: string[];
}

export interface MatrixCase {
  passed: boolean;
  failed_gates: string[];
  repeats_passed: number;
  repeats: number;
  notes: string[];
  sample_answer: string;
  sample_model?: string | null;
}

export interface MatrixAlias {
  models: string[];
  refusals?: number;
  runs: number;
  p50_ms: number | null;
  p95_ms: number | null;
}

export interface MatrixResult {
  name: string;
  change: GatewayChange;
  error?: string;
  profile?: string;
  aliases?: Record<string, MatrixAlias>;
  decision?: string;
  reasons?: string[];
  gates?: Record<string, MatrixGate>;
  canaries_ok?: boolean;
  tokens_in?: number;
  tokens_out?: number;
  model_calls?: number;
  cases?: Record<string, MatrixCase>;
  repeats?: number;
  duration_s?: number;
  history?: MatrixAttempt[];
}

/** An earlier attempt of the same combination, kept when it was re-run. */
export interface MatrixAttempt {
  run_id: string | null;
  decision?: string;
  error?: string;
  gates?: Record<string, boolean | null>;
  failed_cases?: Record<string, string[]>;
}

export interface ModelMatrix {
  created_at: string;
  repeats: number;
  case_ids: string[];
  results: MatrixResult[];
  complete: boolean;
}
