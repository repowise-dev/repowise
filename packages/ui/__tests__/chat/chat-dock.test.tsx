import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { hydrateRoot, type Root } from "react-dom/client";
import { ChatDock, type ChatDockProps } from "../../src/chat/chat-dock.js";
import type { ChatUIMessage } from "@repowise-dev/types/chat";

const MESSAGE: ChatUIMessage = {
  id: "assistant-1",
  role: "assistant",
  text: "The parser lives in packages/core.",
  toolCalls: [],
  isStreaming: false,
};

function props(overrides: Partial<ChatDockProps> = {}): ChatDockProps {
  return {
    storageKey: "dock:r1",
    repoId: "r1",
    repoName: "acme",
    context: { kind: "overview", label: "Overview" },
    messages: [],
    isStreaming: false,
    onSend: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  };
}

describe("ChatDock", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("moves between minimized, compact, and expanded states", async () => {
    render(<ChatDock {...props()} />);

    expect(screen.getByText("Ask Repowise")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Open repository chat" }));
    expect(screen.getByRole("complementary", { name: "Repository chat" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Expand repository chat" }));
    expect(await screen.findByRole("dialog", { name: "Repository chat" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse repository chat" }));
    expect(screen.getByRole("complementary", { name: "Repository chat" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Minimize repository chat" }));
    expect(screen.getByRole("button", { name: "Open repository chat" })).toBeInTheDocument();
  });

  it("keeps compact discovery focused on one contextual suggestion", () => {
    render(<ChatDock {...props()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open repository chat" }));

    expect(screen.getAllByRole("button", { name: /^Try / })).toHaveLength(1);
  });

  it("updates context copy on navigation without losing the conversation", () => {
    const view = render(<ChatDock {...props({ messages: [MESSAGE] })} />);
    fireEvent.click(screen.getByRole("button", { name: "Open repository chat" }));
    expect(screen.getByLabelText("Chat message")).toHaveAttribute(
      "placeholder",
      "Ask about this repository overview",
    );

    view.rerender(
      <ChatDock
        {...props({
          messages: [MESSAGE],
          context: {
            kind: "file",
            label: "Files",
            target: "src/parser.ts",
            targetKind: "path",
          },
        })}
      />,
    );
    expect(screen.getByLabelText("Chat message")).toHaveAttribute(
      "placeholder",
      "Ask about this file",
    );

    fireEvent.click(screen.getByRole("button", { name: "Expand repository chat" }));
    expect(screen.getByText("The parser lives in packages/core.")).toBeInTheDocument();
    expect(screen.getByText("src/parser.ts")).toBeInTheDocument();
  });

  it("opens the full chat through the host navigation callback", async () => {
    const onOpenFullChat = vi.fn();
    window.localStorage.setItem(
      "dock:r1",
      JSON.stringify({ mode: "expanded", draft: "" }),
    );
    render(<ChatDock {...props({ messages: [MESSAGE], onOpenFullChat })} />);
    fireEvent.click(await screen.findByRole("button", { name: "Full chat" }));
    expect(onOpenFullChat).toHaveBeenCalledOnce();
  });

  it("removes context, restores composer focus, and resets for a new page", async () => {
    const view = render(
      <ChatDock
        {...props({
          context: { kind: "health", label: "Code Health" },
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open repository chat" }));
    const remove = screen.getByRole("button", {
      name: "Remove current view from this message",
    });
    remove.focus();
    fireEvent.click(remove);
    expect(screen.queryByText("Current view")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Chat message")).toHaveFocus());

    view.rerender(
      <ChatDock
        {...props({ context: { kind: "architecture", label: "Architecture" } })}
      />,
    );
    expect(screen.getByText("Current view")).toBeInTheDocument();
  });

  it("restores focus across compact and expanded dismiss transitions", async () => {
    render(<ChatDock {...props()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open repository chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Minimize repository chat" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Open repository chat" })).toHaveFocus(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Open repository chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Expand repository chat" }));
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.getByLabelText("Chat message")).toHaveFocus());

    fireEvent.click(screen.getByRole("button", { name: "Expand repository chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Minimize repository chat" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Open repository chat" })).toHaveFocus(),
    );
  });

  it("restores the expanded composer focus after removing page context", async () => {
    window.localStorage.setItem(
      "dock:r1",
      JSON.stringify({ mode: "expanded", draft: "" }),
    );
    render(
      <ChatDock
        {...props({
          context: {
            kind: "file",
            label: "Files",
            target: "src/parser.ts",
            targetKind: "path",
          },
        })}
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Remove current view from this message",
      }),
    );
    await waitFor(() => expect(screen.getByLabelText("Chat message")).toHaveFocus());
  });

  it("keeps the expanded desktop dock non-modal while the page remains interactive", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    );
    window.localStorage.setItem(
      "dock:r1",
      JSON.stringify({ mode: "expanded", draft: "" }),
    );
    const onPageAction = vi.fn();
    render(
      <>
        <button type="button" onClick={onPageAction}>Select page entity</button>
        <ChatDock {...props()} />
      </>,
    );
    expect(await screen.findByRole("dialog", { name: "Repository chat" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Select page entity" }));
    expect(onPageAction).toHaveBeenCalledOnce();
    expect(screen.getByRole("dialog", { name: "Repository chat" })).toBeInTheDocument();
  });

  it("exposes a host collision inset for bottom-anchored page controls", async () => {
    window.localStorage.setItem(
      "dock:r1",
      JSON.stringify({ mode: "compact", draft: "" }),
    );
    render(<ChatDock {...props({ collisionInset: "5rem" })} />);
    expect(await screen.findByRole("complementary", { name: "Repository chat" })).toHaveStyle({
      "--chat-dock-bottom-offset": "5rem",
    });
  });

  it("subtracts a desktop collision inset from expanded height", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    );
    window.localStorage.setItem(
      "dock:r1",
      JSON.stringify({ mode: "expanded", draft: "" }),
    );
    render(<ChatDock {...props({ collisionInset: "11rem" })} />);
    expect(await screen.findByRole("dialog", { name: "Repository chat" })).toHaveStyle({
      "--chat-dock-bottom-offset": "11rem",
      height: "min(760px, calc(100dvh - var(--chat-dock-bottom-offset) - 1rem))",
    });
  });

  it("server-renders a stable minimized shell before hydrating persisted state", async () => {
    window.localStorage.setItem(
      "dock:r1",
      JSON.stringify({ mode: "expanded", draft: "saved question" }),
    );
    const html = renderToString(<ChatDock {...props()} />);
    expect(html).toContain("Open repository chat");
    expect(html).not.toContain("Repository chat</h2>");

    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.appendChild(container);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    let root: Root | undefined;
    await act(async () => {
      root = hydrateRoot(container, <ChatDock {...props()} />);
    });
    await waitFor(() =>
      expect(screen.getByRole("dialog", { name: "Repository chat" })).toBeInTheDocument(),
    );
    expect(
      consoleError.mock.calls.some((call) => String(call[0]).includes("hydration")),
    ).toBe(false);
    await act(async () => root?.unmount());
    container.remove();
    consoleError.mockRestore();
  });

  it("continues to report streaming and announces quiet completion while minimized", () => {
    const view = render(<ChatDock {...props({ isStreaming: true })} />);
    expect(
      screen.getByRole("button", { name: "Open repository chat, response in progress" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Working");

    view.rerender(<ChatDock {...props({ isStreaming: false, messages: [MESSAGE] })} />);
    expect(
      screen.getByRole("button", { name: "Open repository chat, answer ready" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Answer ready");
  });

  it("suppresses every dock state when the host is the full chat page", () => {
    window.localStorage.setItem(
      "dock:r1",
      JSON.stringify({ mode: "expanded", draft: "still here" }),
    );
    const { container } = render(<ChatDock {...props({ suppressed: true })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("persists drafts by repository and never leaks them across repositories", async () => {
    window.localStorage.setItem(
      "dock:r1",
      JSON.stringify({ mode: "compact", draft: "question for one" }),
    );
    window.localStorage.setItem(
      "dock:r2",
      JSON.stringify({ mode: "compact", draft: "question for two" }),
    );
    const view = render(<ChatDock {...props()} />);
    expect(screen.getByLabelText("Chat message")).toHaveValue("question for one");

    view.rerender(
      <ChatDock {...props({ storageKey: "dock:r2", repoId: "r2", repoName: "beta" })} />,
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Chat message")).toHaveValue("question for two"),
    );
    expect(JSON.parse(window.localStorage.getItem("dock:r1") ?? "{}").draft).toBe(
      "question for one",
    );
  });
});
