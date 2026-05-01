/**
 * Canonical chat types â€” conversation, messages, and the discriminated-union
 * `ChatArtifact` type that lets the chat UI render tool results as
 * mini-visualizations instead of `<pre>{JSON}</pre>`.
 *
 * The discriminated union is net-new in @repowise-dev/types so the artifact
 * panel can switch on `artifact.type` to pick a renderer with full
 * compile-time narrowing per variant.
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

/**
 * Tool call as the chat UI sees it after streaming has accumulated state.
 * Distinct from the wire `ChatToolCall` because it carries UI-only fields
 * (`status`, `summary`, `artifact`) that are derived during the SSE merge.
 */
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
 * referenced specific files/symbols. Drives `chat/source-citations.tsx`.
 */
export interface ChatCitation {
  file_path: string;
  symbol_name?: string;
  start_line?: number;
  end_line?: number;
}

export interface GraphArtifact {
  type: "graph";
  data: GraphExport;
}

export interface HotspotArtifact {
  type: "hotspot";
  data: { hotspots: Hotspot[] };
}

export interface DeadCodeArtifact {
  type: "dead_code";
  data: { findings: DeadCodeFinding[] };
}

export interface DecisionsArtifact {
  type: "decisions";
  data: { decisions: DecisionRecord[] };
}

export interface AnswerArtifact {
  type: "answer";
  data: {
    answer: string;
    citations: ChatCitation[];
    confidence: "high" | "medium" | "low";
  };
}

/**
 * Fallback for tools that haven't yet been promoted to a typed variant.
 * The renderer falls back to JSON pretty-print for this case.
 */
export interface GenericArtifact {
  type: string;
  data: Record<string, unknown>;
}

/**
 * Typed variants only â€” use this when a consumer needs to narrow on `.type`
 * and access the per-variant `data` shape. The renderer's `switch` exhaust
 * check should be against this type.
 */
export type KnownChatArtifact =
  | GraphArtifact
  | HotspotArtifact
  | DeadCodeArtifact
  | DecisionsArtifact
  | AnswerArtifact;

/**
 * Full union accepted on the wire. Consumers should branch on
 * `isKnownChatArtifact(a)` first, then switch on `a.type` for a typed render
 * path; the `else` falls through to a JSON pretty-print.
 */
export type ChatArtifact = KnownChatArtifact | GenericArtifact;

const KNOWN_ARTIFACT_TYPES: ReadonlyArray<KnownChatArtifact["type"]> = [
  "graph",
  "hotspot",
  "dead_code",
  "decisions",
  "answer",
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
