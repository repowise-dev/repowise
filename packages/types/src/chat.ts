/**
 * Canonical chat types — conversation, messages, and the discriminated-union
 * `ChatArtifact` type that lets the chat UI render tool results as
 * mini-visualizations instead of `<pre>{JSON}</pre>`.
 *
 * The variants below mirror the artifact shapes actually emitted by the
 * hosted-backend chat router (`backend/app/routers/chat.py:_tool_*`). They are
 * convenience-shaped (denormalised, not strict `DecisionRecord[]` /
 * `DeadCodeFinding[]` / `GraphExport`) because the backend currently passes
 * raw tool result dicts through the SSE wrapper.
 *
 * KNOWN FOLLOWUP — Phase 2D candidate: normalise backend tool results to use
 * strict typed contracts (`DecisionRecord[]`, `DeadCodeFinding[]`, etc.) so
 * renderers stop reaching for ad-hoc fields like `mode` or
 * `high_confidence`/`medium_confidence`. Out of scope for Phase 2B because it
 * would touch all eight `_tool_*` functions in `backend/app/routers/chat.py`,
 * rewrite `tests/unit/server/test_mcp.py`, and risk LLM tool-call quality
 * regressions if information density shrinks.
 */

import type { GraphExport } from "./graph.js";
import type { Hotspot } from "./git.js";
import type { DeadCodeFinding } from "./dead-code.js";
import type { DecisionRecord } from "./decisions.js";

// ---------------------------------------------------------------------------
// Product context surrounding a chat composer
// ---------------------------------------------------------------------------

export type ChatContextKind =
  | "repository"
  | "overview"
  | "documentation"
  | "architecture"
  | "graph"
  | "health"
  | "refactoring"
  | "file"
  | "symbol"
  | "module"
  | "commit"
  | "contributor"
  | "decision"
  | "risk"
  | "security"
  | "usage"
  | "settings"
  | "chat";

export type ChatContextTargetKind =
  | "path"
  | "symbol"
  | "module"
  | "dependency"
  | "commit"
  | "person"
  | "decision"
  | "documentation";

/** Portable navigation context supplied by a product host for one chat turn. */
export interface ChatContext {
  kind: ChatContextKind;
  label: string;
  target?: string;
  targetKind?: ChatContextTargetKind;
}

// ---------------------------------------------------------------------------
// Conversations + messages
// ---------------------------------------------------------------------------

export interface Conversation {
  id: string;
  repository_id: string;
  title: string;
  message_count: number;
  pinned?: boolean;
  created_at: string;
  updated_at: string;
}

export interface ChatToolCall {
  id: string;
  name: string;
  arguments?: Record<string, unknown>;
  result?: Record<string, unknown>;
  summary?: string;
  artifact_type?: string;
  artifact?: ChatArtifact;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: {
    text?: string;
    tool_calls?: ChatToolCall[];
    provider?: string;
    model?: string;
  };
  created_at: string;
}

// ---------------------------------------------------------------------------
// UI-flattened message shape (consumed by chat presentation components)
// ---------------------------------------------------------------------------

export interface ChatUIToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  result?: Record<string, unknown>;
  summary?: string;
  artifact?: ChatArtifact;
  status: "running" | "done" | "error";
}

export interface ChatUIMessage {
  id: string;
  serverId?: string;
  role: "user" | "assistant";
  text: string;
  toolCalls: ChatUIToolCall[];
  isStreaming: boolean;
  /** Provenance recorded on assistant responses. */
  provider?: string;
  model?: string;
}

// ---------------------------------------------------------------------------
// Tool-result artifacts (discriminated union)
// ---------------------------------------------------------------------------

/**
 * Common citation surface emitted alongside any artifact when the tool
 * referenced specific files/symbols. Drives `chat/source-citation.tsx`.
 */
export interface ChatCitation {
  file_path: string;
  symbol_name?: string;
  start_line?: number;
  end_line?: number;
}

export interface ArtifactEvidence {
  basis: "measured" | "inferred" | "unknown" | string;
  confidence?: number | string;
  coverage?: unknown;
  limits?: Record<string, unknown>;
  truncated?: boolean;
  stale?: string;
}

export interface ArtifactEnvelopeIdentity {
  id: string;
  version: 1;
  tool_name: string;
  title?: string;
  presentation: string;
  evidence?: ArtifactEvidence;
  pinned?: boolean;
  created_at?: string;
}

/** `get_overview` — repository fact sheet. */
export interface OverviewArtifactData {
  total_files: number;
  total_symbols: number;
  languages: Record<string, number>;
  modules: string[];
  entry_points: string[];
  hotspot_count: number;
  git_summary?: Record<string, unknown> | null;
  is_monorepo: boolean;
}
export interface OverviewArtifact extends ArtifactEnvelopeIdentity {
  type: "overview";
  data: OverviewArtifactData;
}

/** `get_context` — per-target wiki snippet + git/decision context. */
export interface ContextArtifactData {
  targets: Record<
    string,
    {
      docs?: { content_md?: string; title?: string; page_type?: string; page_id?: string } | null;
      hotspot_info?: Record<string, unknown> | null;
      decisions?: Array<Record<string, unknown>>;
      [k: string]: unknown;
    }
  >;
}
export interface ContextArtifact extends ArtifactEnvelopeIdentity {
  type: "context";
  data: ContextArtifactData;
}

/** `get_risk` / `get_change_risk` — modification or commit-range risk report. */
export interface RiskTargetRow {
  /** Path key; optional on MCP dict values that use `target` instead. */
  file_path?: string;
  /** MCP per-target id when `targets` is a path-keyed object. */
  target?: string;
  /**
   * MCP `get_risk` hotspot score: a 0–1 fraction from
   * `GitMetadata.churn_percentile` (rank / total), not 0–100.
   */
  hotspot_score?: number;
  /** Same 0–1 fraction as `hotspot_score` (legacy fixture field name). */
  churn_percentile?: number;
  is_hotspot?: boolean;
  risk_type?: string;
  trend?: string;
  risk_summary?: string;
  [k: string]: unknown;
}

export interface RiskReportArtifactData {
  /**
   * MCP `get_risk` returns a path-keyed object; legacy fixtures used an array.
   * Renderers normalize either shape.
   */
  targets?: RiskTargetRow[] | Record<string, RiskTargetRow>;
  global_hotspots?: Array<{
    /** MCP wire. */
    file_path?: string;
    hotspot_score?: number;
    /** Legacy chat fixture. */
    path?: string;
    churn_percentile?: number;
  }>;
  /** `get_change_risk` live-git payload fields. */
  ref?: string;
  score?: number;
  risk_percentile?: number | null;
  review_priority?: string;
  classification?: string;
  warning?: string;
  error?: string;
  /** `get_change_risk` action-first blocks. */
  directive?: ChangeRiskDirective;
  health_delta?: ChangeHealthDeltaData;
  change_shape?: Record<string, unknown>;
  impacted_tests?: { tests_to_run?: string[]; status?: string; summary?: string };
  /** Bug-fix record of the touched files: the "historically fragile" signal. */
  fix_history?: {
    available?: boolean;
    files?: Array<{ path: string; churn: number; fix_pressure: number }>;
  };
  prior_fixes?: { files_with_fixes?: number; total_fixes?: number };
  [k: string]: unknown;
}

/** What the change did to the codebase's health, and how sure the tool is. */
export interface ChangeFindingRow {
  id: string;
  change: "introduced" | "worsened";
  dimension: "defect" | "maintainability" | "performance";
  biomarker: string;
  severity: "low" | "medium" | "high" | "critical";
  path: string;
  reason: string;
  attribution: { basis: string; confidence: "high" | "medium" | "low"; why: string };
  symbol?: string;
  lines?: [number, number];
  severity_before?: string;
  suggestion?: string;
  opportunity_id?: string;
  opportunity_rank?: number;
  inspect?: string;
}

export interface ChangeHealthDeltaData {
  /** `partial` and `unavailable` must never render as a clean result. */
  status:
    | "available"
    | "partial"
    | "unavailable"
    | "unsupported_range"
    | "too_large"
    | "timeout"
    | "analyzer_mismatch"
    | "rules_mismatch"
    | "stale_baseline";
  explanation: string;
  introduced: number;
  worsened: number;
  resolved: number;
  top_findings: ChangeFindingRow[];
  findings_total: number;
  findings_emitted: number;
  by_dimension?: Record<string, number>;
  by_severity?: Record<string, number>;
  scope?: { changed: number; eligible: number; analyzed: number; skipped: number; failed: number };
  skipped?: { total: number; by_reason: Record<string, number> };
  base?: { ref: string; sha: string; kind: string };
  head?: { ref: string; sha: string; kind: string };
  limits?: string[];
}

export interface ChangeRiskDirective {
  status: "review_required" | "review_recommended" | "clear_in_analyzed_scope" | "unknown";
  headline: string;
  reasons?: string[];
  next_actions?: string[];
}
export interface RiskReportArtifact extends ArtifactEnvelopeIdentity {
  type: "risk_report";
  data: RiskReportArtifactData;
}
export interface RiskArtifact extends ArtifactEnvelopeIdentity {
  type: "risk";
  data: RiskReportArtifactData;
}
export interface ChangeRiskArtifact extends ArtifactEnvelopeIdentity {
  type: "change_risk";
  data: RiskReportArtifactData;
}

/** `search_codebase` — wiki page hits. */
export interface SearchResultsArtifactData {
  query: string;
  results: Array<{
    title: string;
    page_type: string;
    page_id?: string;
    target_path?: string;
    snippet?: string;
    relevance_score?: number;
  }>;
}
export interface SearchResultsArtifact extends ArtifactEnvelopeIdentity {
  type: "search_results";
  data: SearchResultsArtifactData;
}

/**
 * Legacy wire shape from the removed chat tool `get_dependency_path`.
 * Retained so stored conversations / SSE history still narrow; not emitted
 * by the current 7-tool chat registry (MCP still offers it as opt-in).
 */
export interface GraphPathArtifactData {
  path: string[];
  distance: number;
  explanation: string;
}
export interface GraphPathArtifact extends ArtifactEnvelopeIdentity {
  type: "graph";
  data: GraphPathArtifactData;
}
export interface DependencyPathArtifact extends ArtifactEnvelopeIdentity {
  type: "dependency_path";
  data: Record<string, unknown>;
}
export interface CallPathArtifact extends ArtifactEnvelopeIdentity {
  type: "call_path";
  data: Record<string, unknown>;
}
export interface SourceArtifact extends ArtifactEnvelopeIdentity {
  type: "source";
  data: Record<string, unknown>;
}
export interface HealthArtifact extends ArtifactEnvelopeIdentity {
  type: "health";
  data: Record<string, unknown>;
}

/** `get_why` — decision register search / path lookup / health dashboard. */
export interface DecisionsArtifactData {
  mode: "health" | "search" | "path";
  query?: string;
  path?: string;
  summary?: string;
  /** Health mode: counters from `get_decision_health_summary`. */
  counts?: Record<string, number>;
  /** @deprecated Legacy chat fixture field; prefer `counts`. */
  total_decisions?: number;
  /** @deprecated Legacy chat fixture field; unused by MCP wire format. */
  by_source?: Record<string, number>;
  stale_decisions?: Array<{
    title: string;
    status?: string;
    affected_files?: string[];
    staleness_score?: number;
  }>;
  proposed_awaiting_review?: Array<{
    title: string;
    source?: string;
    confidence?: number;
  }>;
  ungoverned_hotspots?: Array<string | { file_path?: string; path?: string }>;
  /** Search/path mode: governing or matched decisions (MCP wire). */
  decisions?: Array<{
    title: string;
    status?: string;
    decision?: string;
    rationale?: string;
    affected_files?: string[];
  }>;
  /**
   * @deprecated Legacy chat fixture shape. MCP search/path return `decisions`.
   * Renderers still accept this for stored conversations.
   */
  results?: Array<{
    title: string;
    decision: string;
    rationale?: string;
    affected_files?: string[];
  }>;
  origin_story?: {
    first_commit?: { date?: string; author?: string; message?: string };
    primary_author?: string;
    [k: string]: unknown;
  };
  alignment?: {
    score?: number;
    label?: string;
    summary?: string;
    [k: string]: unknown;
  };
}
export interface DecisionsArtifact extends ArtifactEnvelopeIdentity {
  type: "decisions";
  data: DecisionsArtifactData;
}

/** `get_dead_code` — confidence-tiered dead-code findings. */
export interface DeadCodeArtifactData {
  total_findings: number;
  deletable_lines: number;
  high_confidence: Array<{
    file_path: string;
    symbol_name?: string | null;
    kind: string;
    confidence: number;
    reason: string;
    lines: number;
    safe_to_delete: boolean;
  }>;
  medium_confidence: Array<{
    file_path: string;
    symbol_name?: string | null;
    kind: string;
    confidence: number;
    reason: string;
  }>;
}
export interface DeadCodeArtifact extends ArtifactEnvelopeIdentity {
  type: "dead_code";
  data: DeadCodeArtifactData;
}

/**
 * Legacy wire shape from the removed chat tool `get_architecture_diagram`.
 * Retained for historical SSE payloads; current chat does not emit `diagram`.
 */
export interface DiagramArtifactData {
  diagram_type: string;
  mermaid_syntax: string;
  description?: string;
}
export interface DiagramArtifact extends ArtifactEnvelopeIdentity {
  type: "diagram";
  data: DiagramArtifactData;
}

/**
 * Fallback for tools that haven't yet been promoted to a typed variant.
 * The renderer falls back to JSON pretty-print for this case.
 */
export interface GenericArtifact extends ArtifactEnvelopeIdentity {
  type: string;
  data: Record<string, unknown>;
}

/** Future variants — declared for the type system, not yet emitted on the wire. */
export interface HotspotArtifact extends ArtifactEnvelopeIdentity {
  type: "hotspot";
  data: { hotspots: Hotspot[] };
}
export interface AnswerArtifact extends ArtifactEnvelopeIdentity {
  type: "answer";
  data: {
    answer: string;
    citations: ChatCitation[];
    confidence: "high" | "medium" | "low";
  };
}
/** Strict-typed future variants — wire-format alternatives mirroring engine canonicals. */
export interface StrictGraphArtifact extends ArtifactEnvelopeIdentity {
  type: "graph_export";
  data: GraphExport;
}
export interface StrictDeadCodeArtifact extends ArtifactEnvelopeIdentity {
  type: "dead_code_strict";
  data: { findings: DeadCodeFinding[] };
}
export interface StrictDecisionsArtifact extends ArtifactEnvelopeIdentity {
  type: "decisions_strict";
  data: { decisions: DecisionRecord[] };
}

/**
 * Typed variants only — use this when a consumer needs to narrow on `.type`
 * and access the per-variant `data` shape. The renderer's `switch` exhaust
 * check should be against this type.
 */
export type KnownChatArtifact =
  | OverviewArtifact
  | ContextArtifact
  | SourceArtifact
  | RiskArtifact
  | ChangeRiskArtifact
  | RiskReportArtifact
  | HealthArtifact
  | SearchResultsArtifact
  | DependencyPathArtifact
  | CallPathArtifact
  | GraphPathArtifact
  | DecisionsArtifact
  | DeadCodeArtifact
  | DiagramArtifact;

/**
 * Full union accepted on the wire. Consumers should branch on
 * `isKnownChatArtifact(a)` first, then switch on `a.type` for a typed render
 * path; the `else` falls through to a JSON pretty-print.
 */
export type ChatArtifact = KnownChatArtifact | GenericArtifact;

const KNOWN_ARTIFACT_TYPES: ReadonlyArray<KnownChatArtifact["type"]> = [
  "overview",
  "context",
  "source",
  "risk",
  "change_risk",
  "risk_report",
  "health",
  "search_results",
  "dependency_path",
  "call_path",
  "graph",
  "decisions",
  "dead_code",
  "diagram",
];

export function isKnownChatArtifact(
  a: ChatArtifact,
): a is KnownChatArtifact {
  return (KNOWN_ARTIFACT_TYPES as readonly string[]).includes(a.type);
}

// ---------------------------------------------------------------------------
// SSE event stream
// ---------------------------------------------------------------------------

export type ChatSSEEvent =
  | { type: "text_delta"; text: string }
  | {
      type: "tool_start";
      tool_id: string;
      tool_name: string;
      input: Record<string, unknown>;
    }
  | {
      type: "tool_result";
      tool_id: string;
      tool_name: string;
      summary: string;
      artifact: ChatArtifact;
      citations?: ChatCitation[];
    }
  | { type: "done"; conversation_id: string; message_id: string; user_message_id?: string; provider?: string; model?: string }
  | { type: "error"; message: string };
