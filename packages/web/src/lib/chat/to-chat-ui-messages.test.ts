import { describe, expect, it } from "vitest";
import type { ChatMessageResponse } from "@/lib/api/types";
import { toChatUiMessages } from "./to-chat-ui-messages";

describe("toChatUiMessages", () => {
  it("restores the persisted artifact envelope without rebuilding it", () => {
    const stored: ChatMessageResponse[] = [
      {
        id: "m1",
        conversation_id: "c1",
        role: "assistant",
        content: {
          tool_calls: [
            {
              id: "t1",
              name: "get_health",
              artifact: {
                id: "artifact-1",
                version: 1,
                type: "health",
                tool_name: "get_health",
                presentation: "health",
                data: { score: 8.4 },
                pinned: true,
              },
            },
          ],
        },
        created_at: "2026-08-28T00:00:00Z",
      },
    ];

    expect(toChatUiMessages(stored)[0]?.toolCalls[0]?.artifact).toMatchObject({
      id: "artifact-1",
      type: "health",
      data: { score: 8.4 },
      pinned: true,
    });
  });

  it("restores artifacts from stored tool results", () => {
    const stored: ChatMessageResponse[] = [
      {
        id: "m1",
        conversation_id: "c1",
        role: "assistant",
        content: {
          text: "The file is a hotspot.",
          tool_calls: [
            {
              id: "t1",
              name: "get_risk",
              arguments: { targets: ["src/a.ts"] },
              result: { targets: { "src/a.ts": { trend: "increasing" } } },
            },
          ],
        },
        created_at: "2026-08-28T00:00:00Z",
      },
    ];

    const [message] = toChatUiMessages(stored);
    expect(message?.toolCalls[0]?.artifact).toMatchObject({
      type: "risk_report",
      data: { targets: { "src/a.ts": { trend: "increasing" } } },
    });
  });

  it("uses the server-persisted artifact type and summary when available", () => {
    const stored: ChatMessageResponse[] = [
      {
        id: "m1",
        conversation_id: "c1",
        role: "assistant",
        content: {
          tool_calls: [
            {
              id: "t1",
              name: "get_context",
              result: { stale: true },
              summary: "Context for one target",
              artifact_type: "context",
            },
          ],
        },
        created_at: "2026-08-28T00:00:00Z",
      },
    ];

    expect(toChatUiMessages(stored)[0]?.toolCalls[0]).toMatchObject({
      summary: "Context for one target",
      artifact: { type: "context", data: { stale: true } },
    });
  });

  it("does not invent an artifact when a stored call has no result", () => {
    const stored: ChatMessageResponse[] = [
      {
        id: "m1",
        conversation_id: "c1",
        role: "assistant",
        content: {
          tool_calls: [{ id: "t1", name: "get_context" }],
        },
        created_at: "2026-08-28T00:00:00Z",
      },
    ];

    expect(toChatUiMessages(stored)[0]?.toolCalls[0]?.artifact).toBeUndefined();
  });
});
