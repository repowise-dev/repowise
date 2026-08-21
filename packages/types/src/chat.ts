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
// Conversations + messages
// ---------------------------------------------------------------------------

export interface Conversation {
  id: string;
  repository_id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ChatToolCall {
  id: string;
  name: string;
  arguments?: Record<string, unknown>;
  result?: Record<string, unknown>;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: {
    text?: string;
    tool_calls?: ChatToolCall[];
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
  artifact?: { type: string; data: Record<string, unknown> };
  status: "running" | "done" | "error";
}

export interface ChatUIMessage {
  id: string;
  serverId?: string;
  role: "user" | "assistant";
  text: string;
  toolCalls: ChatUIToolCall[];
  isStreaming: boolean;
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
export interface OverviewArtifact {
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
export interface ContextArtifact {
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
  [k: string]: unknown;
}
export interface RiskReportArtifact {
  type: "risk_report";
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
export interface SearchResultsArtifact {
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
export interface GraphPathArtifact {
  type: "graph";
  data: GraphPathArtifactData;
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
export interface DecisionsArtifact {
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
export interface DeadCodeArtifact {
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
export interface DiagramArtifact {
  type: "diagram";
  data: DiagramArtifactData;
}

/**
 * Fallback for tools that haven't yet been promoted to a typed variant.
 * The renderer falls back to JSON pretty-print for this case.
 */
export interface GenericArtifact {
  type: string;
  data: Record<string, unknown>;
}

/** Future variants — declared for the type system, not yet emitted on the wire. */
export interface HotspotArtifact {
  type: "hotspot";
  data: { hotspots: Hotspot[] };
}
export interface AnswerArtifact {
  type: "answer";
  data: {
    answer: string;
    citations: ChatCitation[];
    confidence: "high" | "medium" | "low";
  };
}
/** Strict-typed future variants — wire-format alternatives mirroring engine canonicals. */
export interface StrictGraphArtifact {
  type: "graph_export";
  data: GraphExport;
}
export interface StrictDeadCodeArtifact {
  type: "dead_code_strict";
  data: { findings: DeadCodeFinding[] };
}
export interface StrictDecisionsArtifact {
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
  | RiskReportArtifact
  | SearchResultsArtifact
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
  "risk_report",
  "search_results",
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
  | { type: "done"; conversation_id: string; message_id: string }
  | { type: "error"; message: string };
