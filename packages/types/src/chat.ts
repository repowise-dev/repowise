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

/** `get_risk` — modification risk report per target. */
export interface RiskReportArtifactData {
  targets: Array<{
    file_path: string;
    churn_percentile?: number;
    is_hotspot?: boolean;
    [k: string]: unknown;
  }>;
  global_hotspots: Array<{ path: string; churn_percentile: number }>;
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

/** `get_dependency_path` — short import-graph path. */
export interface GraphPathArtifactData {
  path: string[];
  distance: number;
  explanation: string;
}
export interface GraphPathArtifact {
  type: "graph";
  data: GraphPathArtifactData;
}

/** `get_why` — decision register search results / health dashboard. */
export interface DecisionsArtifactData {
  mode: "health" | "search";
  query?: string;
  total_decisions?: number;
  by_source?: Record<string, number>;
  decisions?: Array<{ title: string; status?: string }>;
  results?: Array<{
    title: string;
    decision: string;
    rationale?: string;
    affected_files?: string[];
  }>;
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

/** `get_architecture_diagram` — Mermaid flowchart. */
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
