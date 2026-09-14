import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AiPromptModal } from "../../src/health/ai-prompt-modal.js";

describe("AiPromptModal overflow containment", () => {
  it("contains long unbroken prompt text and keeps its actions inside the dialog", () => {
    render(
      <AiPromptModal
        open
        onOpenChange={() => undefined}
        getPrompt={() => `Fix this value:\n${"x".repeat(9_000)}`}
        filePath={`src/${"deep/".repeat(100)}module.ts`}
      />,
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("min-w-0");
    expect(dialog.className).toContain("w-[calc(100vw-2rem)]");
    expect(dialog.className).toContain("overflow-hidden");

    const prompt = dialog.querySelector("pre");
    expect(prompt).not.toBeNull();
    expect(prompt?.className).toContain("max-w-full");
    expect(prompt?.className).toContain("[overflow-wrap:anywhere]");
    expect(prompt?.parentElement?.className).toContain("overflow-x-hidden");

    expect(screen.getByRole("button", { name: "Copy prompt" }).className).toContain("shrink-0");
  });
});
