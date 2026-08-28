// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

import type { ChatArtifact } from "@repowise-dev/types/chat";

export interface ConversationResponse {
  id: string;
  repository_id: string;
  title: string;
  message_count: number;
  pinned?: boolean;
  created_at: string;
  updated_at: string;
}

export interface ChatMessageResponse {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: {
    text?: string;
    tool_calls?: Array<{
      id: string;
      name: string;
      arguments?: Record<string, unknown>;
      result?: Record<string, unknown>;
      summary?: string;
      artifact_type?: string;
      artifact?: ChatArtifact;
    }>;
    provider?: string;
    model?: string;
  };
  created_at: string;
}

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
    }
  | { type: "done"; conversation_id: string; message_id: string; user_message_id?: string; provider?: string; model?: string }
  | { type: "error"; message: string };
