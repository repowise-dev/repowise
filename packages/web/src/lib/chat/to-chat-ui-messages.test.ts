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

describe("toChatUiMessages grounding and truncation", () => {
  it("keeps the grounding origin and the truncation flag from stored content", () => {
    const stored: ChatMessageResponse[] = [
      {
        id: "m1",
        conversation_id: "c1",
        role: "assistant",
        content: {
          text: "",
          truncated: true,
          tool_calls: [
            {
              id: "grounding-1",
              name: "get_context",
              origin: "grounding",
              summary: "src/a.py",
              artifact: {
                id: "art-1",
                version: 1,
                type: "context",
                tool_name: "get_context",
                presentation: "context",
                data: {},
              },
            },
            { id: "t2", name: "get_risk", result: { targets: {} } },
          ],
        },
        created_at: "2026-09-09T00:00:00Z",
      },
    ];

    const [message] = toChatUiMessages(stored);
    expect(message?.truncated).toBe(true);
    expect(message?.toolCalls[0]?.origin).toBe("grounding");
    expect(message?.toolCalls[1]).not.toHaveProperty("origin");
  });

  it("leaves the flag off for a completed answer", () => {
    const [message] = toChatUiMessages([
      {
        id: "m1",
        conversation_id: "c1",
        role: "assistant",
        content: { text: "Done." },
        created_at: "2026-09-09T00:00:00Z",
      },
    ]);
    expect(message).not.toHaveProperty("truncated");
  });

  it("restores next steps so a reload shows the same chips as the stream", () => {
    const followUps = [
      { text: "Which tests cover a.py?", source: "followup" as const },
    ];
    const stored: ChatMessageResponse[] = [
      {
        id: "m1",
        conversation_id: "c1",
        role: "assistant",
        content: { text: "Risky.", follow_ups: followUps },
        created_at: "2026-08-28T00:00:00Z",
      },
    ];

    expect(toChatUiMessages(stored)[0]?.followUps).toEqual(followUps);
  });

  it("leaves a turn that proposed nothing without the field", () => {
    const stored: ChatMessageResponse[] = [
      {
        id: "m1",
        conversation_id: "c1",
        role: "assistant",
        content: { text: "From memory." },
        created_at: "2026-08-28T00:00:00Z",
      },
    ];

    expect(toChatUiMessages(stored)[0]).not.toHaveProperty("followUps");
  });
});
