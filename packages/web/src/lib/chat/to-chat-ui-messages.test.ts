import { describe, expect, it } from "vitest";
import type { ChatMessageResponse } from "@/lib/api/types";
import { toChatUiMessages } from "./to-chat-ui-messages";
import fixture from "../../../../types/fixtures/views.conformance.json";

describe("toChatUiMessages", () => {
  it("keeps a stored show_view call as a step with no artifact, and restores valid views", () => {
    const stored: ChatMessageResponse[] = [
      {
        id: "m1",
        conversation_id: "c1",
        role: "assistant",
        content: {
          tool_calls: [
            { id: "t1", name: "get_risk", result: { score: 1 } },
            { id: "t2", name: "show_view", result: { view_id: "v1" } },
          ],
          views: [fixture.valid.c1, { id: "v1", kind: "table" }],
        },
        created_at: "2026-10-14T00:00:00Z",
      },
    ];

    const [message] = toChatUiMessages(stored);
    expect(message?.toolCalls.map((call) => call.name)).toEqual(["get_risk", "show_view"]);
    expect(message?.toolCalls[1]).not.toHaveProperty("artifact");
    expect(message?.toolCalls[0]?.artifact).toBeDefined();
    expect(message?.views?.map((view) => view.id)).toEqual(["c1"]);
  });

  it("leaves a turn with no valid views without the field", () => {
    const stored: ChatMessageResponse[] = [
      {
        id: "m1",
        conversation_id: "c1",
        role: "assistant",
        content: { text: "x", views: [{ id: "v1", kind: "table" }] },
        created_at: "2026-10-14T00:00:00Z",
      },
    ];

    expect(toChatUiMessages(stored)[0]).not.toHaveProperty("views");
  });

  it.each([
    ["an errored read", { error: "no git metadata available" }, "error"],
    ["a good read", { score: 8.4 }, "done"],
  ])("reloads %s with the right status", (_label, result, expected) => {
    // The wire persists no status. Hardcoding "done" here laundered a failed
    // read back into a successful one on every reload, and it was cited as
    // evidence again.
    const stored: ChatMessageResponse[] = [
      {
        id: "m1",
        conversation_id: "c1",
        role: "assistant",
        content: {
          tool_calls: [{ id: "t1", name: "get_risk", result }],
        },
        created_at: "2026-08-28T00:00:00Z",
      },
    ];

    expect(toChatUiMessages(stored)[0]?.toolCalls[0]?.status).toBe(expected);
  });

  it("reloads only the unverified figures as flags", () => {
    const stored: ChatMessageResponse[] = [
      {
        id: "m1",
        conversation_id: "c1",
        role: "assistant",
        content: {
          text: "3 fixes and 17 callers.",
          figures: [
            { text: "3", start: 0, end: 1, verified: true, artifact_id: "a".repeat(32) },
            { text: "17", start: 12, end: 14, verified: false },
          ],
          diagnostics: { figures_checked: 2, figures_unverified: 1 },
        },
        created_at: "2026-10-14T00:00:00Z",
      },
    ];

    expect(toChatUiMessages(stored)[0]?.unverifiedFigures).toEqual([
      { text: "17", start: 12, end: 14 },
    ]);
  });

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

  it("keeps the stored cards that validate and drops the rest", () => {
    const card = {
      schema_version: 1,
      kind: "answer",
      title: "Where sessions live",
      question: "Where are sessions stored?",
      facts: [],
      evidence: [
        {
          statement: "Store owns session state",
          basis: { kind: "inferred", source: "import graph" },
        },
      ],
      links: [],
      generated_at: "2026-10-14T00:00:00Z",
    };
    const stored: ChatMessageResponse[] = [
      {
        id: "m1",
        conversation_id: "c1",
        role: "assistant",
        content: { text: "x", cards: [card, { kind: "answer", author: "someone" }] },
        created_at: "2026-10-14T00:00:00Z",
      },
      {
        id: "m2",
        conversation_id: "c1",
        role: "assistant",
        content: { text: "y", cards: [{ kind: "unknown" }] },
        created_at: "2026-10-14T00:00:00Z",
      },
    ];

    const [withCards, withoutCards] = toChatUiMessages(stored);
    expect(withCards?.cards).toEqual([card]);
    expect(withoutCards).not.toHaveProperty("cards");
  });
});
