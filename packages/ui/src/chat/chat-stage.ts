import { ORB_STATE, type OrbState } from "../shared/orb-loader";
import type { ChatUIMessage } from "@repowise-dev/types/chat";

export type ChatToolKind = "searching" | "tracing" | "working";
export type ChatStage = "thinking" | ChatToolKind | "writing";

/** Tools by the kind of wait they cause. Anything not listed is `working`. */
const TOOL_KINDS: Readonly<Record<string, ChatToolKind>> = {
  search_codebase: "searching",
  get_answer: "searching",
  get_overview: "searching",
  get_why: "searching",
  get_context: "tracing",
  get_symbol: "tracing",
  get_callers: "tracing",
  get_architecture: "tracing",
  get_blast_radius: "tracing",
  get_dependency_path: "tracing",
};

export function chatToolKind(name: string): ChatToolKind {
  return TOOL_KINDS[name] ?? "working";
}

export const CHAT_STAGES: Readonly<Record<ChatStage, { state: OrbState; label: string }>> = {
  thinking: { state: ORB_STATE.thinking, label: "Thinking" },
  searching: { state: ORB_STATE.searching, label: "Searching the index" },
  tracing: { state: ORB_STATE.tracing, label: "Tracing callers" },
  working: { state: ORB_STATE.working, label: "Working" },
  writing: { state: ORB_STATE.writing, label: "Writing" },
};

/**
 * What a streaming answer is doing right now: thinking until the first step,
 * the running tool's kind during a call, writing once text arrives. Null once
 * the turn is complete.
 */
export function chatStage(message: ChatUIMessage): ChatStage | null {
  if (!message.isStreaming) return null;
  for (let i = message.toolCalls.length - 1; i >= 0; i -= 1) {
    const call = message.toolCalls[i]!;
    if (call.status === "running") return chatToolKind(call.name);
  }
  return message.text ? "writing" : "thinking";
}
