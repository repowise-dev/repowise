import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChatDock, type ChatDockProps } from "../../src/chat/chat-dock.js";

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

const composer = () => screen.getByRole("textbox") as HTMLTextAreaElement;

describe("ChatDock handoff receiver", () => {
  beforeEach(() => window.localStorage.clear());

  it("opens compact, seeds the question and focuses the composer", async () => {
    render(
      <ChatDock
        {...props({
          command: {
            id: 1,
            type: "handoff",
            handoff: {
              context: { kind: "file", label: "a.py", target: "a.py", targetKind: "path" },
              question: "What is a.py for?",
            },
          },
        })}
      />,
    );

    expect(
      await screen.findByRole("complementary", { name: "Repository chat" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(composer().value).toBe("What is a.py for?"));
    await waitFor(() => expect(document.activeElement).toBe(composer()));
  });

  it("sends the handoff's own context, not the route's, when autoSend is set", async () => {
    const onSend = vi.fn();
    const context = {
      kind: "file" as const,
      label: "a.py",
      target: "a.py",
      targetKind: "path" as const,
    };
    render(
      <ChatDock
        {...props({
          onSend,
          command: {
            id: 1,
            type: "handoff",
            handoff: { context, question: "Explain a.py.", autoSend: true },
          },
        })}
      />,
    );

    await waitFor(() =>
      expect(onSend).toHaveBeenCalledWith("Explain a.py.", context),
    );
    await waitFor(() => expect(composer().value).toBe(""));
  });

  it("reports the command back so a remount does not replay it", async () => {
    const onCommandHandled = vi.fn();
    render(
      <ChatDock
        {...props({
          onCommandHandled,
          command: { id: 1, type: "toggle" },
        })}
      />,
    );
    await waitFor(() => expect(onCommandHandled).toHaveBeenCalledTimes(1));
  });

  it("holds a command issued while suppressed and applies it on return", async () => {
    const onCommandHandled = vi.fn();
    const command = { id: 1, type: "toggle" as const };
    const view = render(
      <ChatDock {...props({ suppressed: true, command, onCommandHandled })} />,
    );
    // Nothing is rendered, so consuming the command here would lose it.
    expect(onCommandHandled).not.toHaveBeenCalled();

    view.rerender(<ChatDock {...props({ command, onCommandHandled })} />);

    expect(
      await screen.findByRole("complementary", { name: "Repository chat" }),
    ).toBeInTheDocument();
    expect(onCommandHandled).toHaveBeenCalledTimes(1);
  });

  it("toggles open from minimized and back again", async () => {
    const view = render(<ChatDock {...props({ command: { id: 1, type: "toggle" } })} />);
    expect(
      await screen.findByRole("complementary", { name: "Repository chat" }),
    ).toBeInTheDocument();

    view.rerender(<ChatDock {...props({ command: { id: 2, type: "toggle" } })} />);
    expect(
      await screen.findByRole("button", { name: "Open repository chat" }),
    ).toBeInTheDocument();
  });

  it("shows every suggestion for the page, not only the first", async () => {
    render(<ChatDock {...props({ command: { id: 1, type: "toggle" } })} />);
    await screen.findByRole("complementary", { name: "Repository chat" });

    expect(
      screen.getByRole("button", { name: "Explain the main architectural boundaries" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Where should a new contributor start?" }),
    ).toBeInTheDocument();
  });

  it("introduces itself once and then returns to rest", async () => {
    vi.useFakeTimers();
    try {
      const onFirstVisitHintShown = vi.fn();
      render(
        <ChatDock
          {...props({ firstVisitHint: "Press ? to open chat", onFirstVisitHintShown })}
        />,
      );
      expect(screen.getByText("Press ? to open chat")).toBeInTheDocument();
      expect(onFirstVisitHintShown).toHaveBeenCalledTimes(1);

      act(() => void vi.advanceTimersByTime(8000));
      expect(screen.queryByText("Press ? to open chat")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
