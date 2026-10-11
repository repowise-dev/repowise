// @vitest-environment jsdom

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AskAboutThis } from "@repowise-dev/ui/chat";
import { config } from "@/lib/config";
import {
  RepositoryChatProvider,
  useRepositoryChat,
} from "./repository-chat-provider";

const sendMessage = vi.fn();
let pathname = "/repos/r1/code-health";
let search = new URLSearchParams();

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useSearchParams: () => search,
}));

vi.mock("@/lib/hooks/use-chat", () => ({
  useChat: () => ({
    messages: [],
    conversationId: null,
    isStreaming: false,
    error: null,
    sendMessage,
    loadConversation: vi.fn(),
    cancel: vi.fn(),
    reset: vi.fn(),
    artifactOverrides: {},
    replaceArtifact: vi.fn(),
  }),
}));

/** Stands in for the dock, which owns mode and draft inside packages/ui. */
function CommandProbe() {
  const { dockCommand, pageContext, clearDockCommand } = useRepositoryChat();
  return (
    <div>
      <span data-testid="context">{`${pageContext.kind}:${pageContext.target ?? ""}`}</span>
      <span data-testid="command">
        {dockCommand ? `${dockCommand.type}:${dockCommand.handoff?.question ?? ""}` : "none"}
      </span>
      <button type="button" onClick={clearDockCommand}>
        clear
      </button>
    </div>
  );
}

function renderShell() {
  return render(
    <RepositoryChatProvider repoId="r1" repoName="acme">
      <AskAboutThis
        context={{ kind: "file", label: "a.py", target: "a.py", targetKind: "path" }}
        question="What is a.py for?"
      />
      <CommandProbe />
    </RepositoryChatProvider>,
  );
}

describe("repository chat handoff receiver", () => {
  beforeEach(() => {
    localStorage.clear();
    pathname = "/repos/r1/code-health";
    search = new URLSearchParams();
    sendMessage.mockClear();
  });
  afterEach(cleanup);

  it("starts on the route's own context with nothing pending", () => {
    renderShell();
    expect(screen.getByTestId("context").textContent).toBe("health:");
    expect(screen.getByTestId("command").textContent).toBe("none");
  });

  it("takes the handoff's context and queues one command", () => {
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Ask about a.py" }));

    expect(screen.getByTestId("context").textContent).toBe("file:a.py");
    expect(screen.getByTestId("command").textContent).toBe(
      "handoff:What is a.py for?",
    );
  });

  it("clears the command once the dock reports it applied", () => {
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Ask about a.py" }));
    fireEvent.click(screen.getByRole("button", { name: "clear" }));
    expect(screen.getByTestId("command").textContent).toBe("none");
  });

  it("reveals the dock a reader had hidden, because the gesture was deliberate", async () => {
    config.setChatDockHidden(true);
    renderShell();
    // Hidden means the control is gone, so drive the shortcut instead.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Ask about a.py" })).toBeNull(),
    );

    act(() => {
      fireEvent.keyDown(window, { key: "?" });
    });

    expect(config.getChatDockHidden()).toBe(false);
    // "open", not "toggle": the stored mode predates the hide, so toggling it
    // could minimize the thing the keystroke asked to open.
    expect(screen.getByTestId("command").textContent).toBe("open:");
  });

  it("toggles rather than forces open when the dock is already available", () => {
    renderShell();
    act(() => {
      fireEvent.keyDown(window, { key: "?" });
    });
    expect(screen.getByTestId("command").textContent).toBe("toggle:");
  });

  it("hides the ask control when the reader switched it off", async () => {
    config.setChatAskControlsHidden(true);
    renderShell();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Ask about a.py" })).toBeNull(),
    );
  });

  it("offers no page control on the full chat page, which mounts no dock", async () => {
    pathname = "/repos/r1/chat";
    renderShell();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Ask about a.py" })).toBeNull(),
    );
  });

  it("drops the handoff when the query changes what the page is about", () => {
    // Docs, dead code, blast radius and risk all change identity through the
    // query alone, so keying the handoff on the pathname would leave the
    // composer asking about a file the reader has already navigated past.
    pathname = "/repos/r1/dead-code";
    const view = renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Ask about a.py" }));
    expect(screen.getByTestId("context").textContent).toBe("file:a.py");

    search = new URLSearchParams("file=b.py");
    view.rerender(
      <RepositoryChatProvider repoId="r1" repoName="acme">
        <CommandProbe />
      </RepositoryChatProvider>,
    );

    expect(screen.getByTestId("context").textContent).toBe("dead-code:b.py");
  });

  it("returns to the route's context after navigating away", () => {
    const view = renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Ask about a.py" }));
    expect(screen.getByTestId("context").textContent).toBe("file:a.py");

    pathname = "/repos/r1/decisions";
    view.rerender(
      <RepositoryChatProvider repoId="r1" repoName="acme">
        <CommandProbe />
      </RepositoryChatProvider>,
    );

    expect(screen.getByTestId("context").textContent).toBe("decision:");
  });
});
