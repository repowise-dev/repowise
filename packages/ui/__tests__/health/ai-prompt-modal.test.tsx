import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ChatHandoff } from "@repowise-dev/types/chat";
import { AiPromptModal, fileChatContext } from "../../src/health/ai-prompt-modal.js";
import { ChatHandoffProvider } from "../../src/chat/chat-handoff.js";

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

describe("AiPromptModal Ask in chat", () => {
  const CONTEXT = fileChatContext("src/a.py")!;

  function renderModal({
    onHandoff = vi.fn(),
    onOpenChange = vi.fn(),
    askEnabled = true,
    withContext = true,
  }: {
    onHandoff?: (handoff: ChatHandoff) => void;
    onOpenChange?: (open: boolean) => void;
    askEnabled?: boolean;
    withContext?: boolean;
  } = {}) {
    render(
      <ChatHandoffProvider onHandoff={onHandoff} askEnabled={askEnabled}>
        <AiPromptModal
          open
          onOpenChange={onOpenChange}
          getPrompt={(flavor) => `Prompt for ${flavor}`}
          {...(withContext ? { chatContext: CONTEXT } : {})}
        />
      </ChatHandoffProvider>,
    );
  }

  it("seeds chat with the prompt on screen and closes the modal", () => {
    const onHandoff = vi.fn();
    const onOpenChange = vi.fn();
    renderModal({ onHandoff, onOpenChange });

    const shown = screen.getByRole("dialog").querySelector("pre")!.textContent;
    fireEvent.click(screen.getByRole("button", { name: "Ask in chat" }));

    expect(onHandoff).toHaveBeenCalledWith({ context: CONTEXT, question: shown, autoSend: false });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("is absent for a prompt with no single subject", () => {
    renderModal({ withContext: false });
    expect(screen.queryByRole("button", { name: "Ask in chat" })).toBeNull();
    expect(screen.getByRole("button", { name: "Copy prompt" })).toBeTruthy();
  });

  it("is absent when the host has chat controls off", () => {
    renderModal({ askEnabled: false });
    expect(screen.queryByRole("button", { name: "Ask in chat" })).toBeNull();
  });

  // The chat dock focuses its composer a frame after the handoff; a focus
  // restore to the opener on close would race it. The modal is opened by host
  // state, not a Radix Dialog.Trigger, so Radix restores nothing; pin that.
  it("does not hand focus back to the opener after Ask in chat", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <ChatHandoffProvider onHandoff={() => undefined}>
          <button type="button" onClick={() => setOpen(true)}>
            Open prompt
          </button>
          <AiPromptModal open={open} onOpenChange={setOpen} getPrompt={() => "p"} chatContext={CONTEXT} />
        </ChatHandoffProvider>
      );
    }
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open prompt" });
    opener.focus();
    fireEvent.click(opener);
    fireEvent.click(await screen.findByRole("button", { name: "Ask in chat" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await new Promise((r) => setTimeout(r, 0));
    expect(document.activeElement).not.toBe(opener);
  });

  it("is absent outside a chat host", () => {
    render(
      <AiPromptModal open onOpenChange={() => undefined} getPrompt={() => "p"} chatContext={CONTEXT} />,
    );
    expect(screen.queryByRole("button", { name: "Ask in chat" })).toBeNull();
  });
});

describe("AiPromptModal with a built prompt", () => {
  it("shows the text at once with its size, and no loading state", () => {
    render(<AiPromptModal open onOpenChange={() => undefined} getPrompt={(f) => `Built for ${f}`} />);
    expect(screen.getByRole("dialog").querySelector("pre")?.textContent).toMatch(/^Built for /);
    expect(screen.getByText(/chars, approx/)).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("button", { name: "Copy prompt" })).toBeEnabled();
  });
});

describe("AiPromptModal with a fetched prompt", () => {
  const CONTEXT = fileChatContext("src/a.py")!;

  function renderFetched(promptSource: (flavor: string) => Promise<string>, onHandoff = vi.fn()) {
    render(
      <ChatHandoffProvider onHandoff={onHandoff}>
        <AiPromptModal
          open
          onOpenChange={() => undefined}
          getPrompt={null}
          promptSource={promptSource}
          chatContext={CONTEXT}
        />
      </ChatHandoffProvider>,
    );
  }

  it("shows a skeleton with Copy and Ask disabled until the prompt arrives", async () => {
    let resolve!: (text: string) => void;
    renderFetched(() => new Promise((r) => (resolve = r)));
    expect(screen.getByRole("status").textContent).toBe("Loading the prompt");
    expect(screen.getByRole("button", { name: "Copy prompt" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Ask in chat" })).toBeDisabled();
    expect(screen.getByRole("dialog").querySelector("pre")).toBeNull();

    resolve("Fetched prompt");
    expect(await screen.findByText("Fetched prompt")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy prompt" })).toBeEnabled();
  });

  it("copies and asks in chat with exactly the text on screen", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const onHandoff = vi.fn();
    renderFetched(async (flavor) => `Fetched for ${flavor}`, onHandoff);
    const shown = (await screen.findByText(/^Fetched for /)).textContent;

    fireEvent.click(screen.getByRole("button", { name: "Copy prompt" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(shown));
    fireEvent.click(screen.getByRole("button", { name: "Ask in chat" }));
    expect(onHandoff).toHaveBeenCalledWith({ context: CONTEXT, question: shown, autoSend: false });
  });

  it("says when the prompt failed to load and fetches again on Retry", async () => {
    const source = vi
      .fn()
      .mockRejectedValueOnce(new Error("down"))
      .mockResolvedValueOnce("Second try");
    renderFetched(source);
    expect((await screen.findByRole("alert")).textContent).toContain("Couldn't load the prompt.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(document.activeElement).not.toBe(document.body);
    expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(true);
    expect(await screen.findByText("Second try")).toBeTruthy();
    expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(true);
    expect(source).toHaveBeenCalledTimes(2);
  });

  it("fetches again for another target agent", async () => {
    const source = vi.fn(async (flavor: string) => `Fetched for ${flavor}`);
    renderFetched(source);
    await screen.findByText(/^Fetched for /);
    fireEvent.click(screen.getByRole("button", { name: "Cursor" }));
    expect(await screen.findByText("Fetched for cursor")).toBeTruthy();
    expect(source).toHaveBeenLastCalledWith("cursor");
  });
});
