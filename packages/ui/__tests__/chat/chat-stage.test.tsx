import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ChatMessage } from "../../src/chat/chat-message.js";
import { CHAT_STAGES, chatStage, chatToolKind } from "../../src/chat/chat-stage.js";
import { ORB_STATE } from "../../src/shared/orb-loader.js";
import type { ChatUIMessage, ChatUIToolCall } from "@repowise-dev/types/chat";

const BASE: ChatUIMessage = {
  id: "a1",
  role: "assistant",
  text: "",
  toolCalls: [],
  isStreaming: true,
};

function tool(name: string, status: ChatUIToolCall["status"]): ChatUIToolCall {
  return { id: `${name}-${status}`, name, arguments: {}, status };
}

describe("chatToolKind", () => {
  it("maps tools to the kind of wait they cause, unknown to working", () => {
    expect(chatToolKind("search_codebase")).toBe("searching");
    expect(chatToolKind("get_answer")).toBe("searching");
    expect(chatToolKind("get_context")).toBe("tracing");
    expect(chatToolKind("get_symbol")).toBe("tracing");
    expect(chatToolKind("get_health")).toBe("working");
    expect(chatToolKind("something_new")).toBe("working");
  });
});

describe("chatStage", () => {
  it("walks thinking, then the running tool's kind, then writing", () => {
    expect(chatStage(BASE)).toBe("thinking");
    expect(chatStage({ ...BASE, toolCalls: [tool("search_codebase", "running")] })).toBe("searching");
    expect(chatStage({ ...BASE, toolCalls: [tool("get_context", "running")] })).toBe("tracing");
    expect(chatStage({ ...BASE, toolCalls: [tool("get_risk", "running")] })).toBe("working");
    expect(chatStage({ ...BASE, toolCalls: [tool("get_risk", "done")] })).toBe("thinking");
    expect(chatStage({ ...BASE, text: "Partial" })).toBe("writing");
    expect(chatStage({ ...BASE, isStreaming: false, text: "Done" })).toBeNull();
  });

  it("uses the shared orb vocabulary", () => {
    expect(CHAT_STAGES.thinking.state).toBe(ORB_STATE.thinking);
    expect(CHAT_STAGES.searching.state).toBe(ORB_STATE.searching);
    expect(CHAT_STAGES.tracing.state).toBe(ORB_STATE.tracing);
    expect(CHAT_STAGES.working.state).toBe(ORB_STATE.working);
    expect(CHAT_STAGES.writing.state).toBe(ORB_STATE.writing);
  });
});

describe("ChatMessage stage marker", () => {
  it("names the stage beside one orb and drops it once the answer completes", () => {
    const view = render(
      <ChatMessage message={{ ...BASE, toolCalls: [tool("search_codebase", "running")] }} repoId="r1" />,
    );
    const marker = view.container.querySelector("[data-chat-stage]");
    expect(marker).toHaveAttribute("data-chat-stage", "searching");
    expect(marker).toHaveTextContent("Searching the index");
    expect(view.container.querySelectorAll('[data-working-orb="true"]')).toHaveLength(1);

    view.rerender(<ChatMessage message={{ ...BASE, text: "Partial" }} repoId="r1" />);
    expect(view.container.querySelector("[data-chat-stage]")).toHaveTextContent("Writing");

    view.rerender(<ChatMessage message={{ ...BASE, text: "Done", isStreaming: false }} repoId="r1" />);
    expect(view.container.querySelector("[data-chat-stage]")).toBeNull();
  });
});
