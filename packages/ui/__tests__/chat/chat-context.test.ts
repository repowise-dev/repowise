import { describe, expect, it } from "vitest";
import {
  getLegacyChatArtifactType,
  getChatContextPresentation,
} from "../../src/chat/chat-context.js";

describe("getChatContextPresentation", () => {
  it("uses repository copy when no context is supplied", () => {
    expect(getChatContextPresentation().placeholder).toBe(
      "Ask about this repository, or paste a file path",
    );
  });

  it.each([
    ["documentation", "Ask about the documentation on this page"],
    ["architecture", "Ask about this architecture view"],
    ["health", "Ask about these code health findings"],
    ["file", "Ask about this file"],
    ["decision", "Ask about this architectural decision"],
  ] as const)("returns focused copy for %s context", (kind, placeholder) => {
    expect(
      getChatContextPresentation({
        kind,
        label: "Current view",
        target: "selected-item",
      }).placeholder,
    ).toBe(placeholder);
  });

  it("returns suggestions that match the active context", () => {
    const presentation = getChatContextPresentation({
      kind: "symbol",
      label: "Symbols",
      target: "useChat",
    });
    expect(presentation.suggestions).toContain("Explain what this symbol does");
  });

  it("distinguishes a collection route from a selected entity", () => {
    expect(
      getChatContextPresentation({ kind: "file", label: "Files" }).placeholder,
    ).toBe("Ask about files in this repository");
    expect(
      getChatContextPresentation({
        kind: "file",
        label: "Files",
        target: "src/index.ts",
      }).placeholder,
    ).toBe("Ask about this file");
  });
});

describe("getLegacyChatArtifactType", () => {
  it("restores historical server artifact types for legacy rows", () => {
    expect(getLegacyChatArtifactType("get_context")).toBe("wiki_page");
    expect(getLegacyChatArtifactType("get_change_risk")).toBe("risk_report");
  });

  it("keeps unknown tool evidence inspectable through the generic renderer", () => {
    expect(getLegacyChatArtifactType("future_tool")).toBe("generic");
  });
});
