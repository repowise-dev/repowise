import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useChatDraft } from "../../src/chat/use-chat-draft.js";

describe("useChatDraft", () => {
  it("restores a distinct draft for each conversation", () => {
    localStorage.setItem("draft:c1", "first draft");
    localStorage.setItem("draft:c2", "second draft");
    const view = renderHook(({ keyName }) => useChatDraft(keyName), {
      initialProps: { keyName: "draft:c1" },
    });
    expect(view.result.current[0]).toBe("first draft");
    view.rerender({ keyName: "draft:c2" });
    expect(view.result.current[0]).toBe("second draft");
    act(() => view.result.current[1]("updated"));
    expect(localStorage.getItem("draft:c2")).toBe("updated");
    expect(localStorage.getItem("draft:c1")).toBe("first draft");
  });
});
