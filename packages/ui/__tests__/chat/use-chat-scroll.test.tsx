import { fireEvent, render, screen } from "@testing-library/react";
import { act, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChatScroll } from "../../src/chat/use-chat-scroll.js";

function ScrollHarness() {
  const [turns, setTurns] = useState(1);
  const scroll = useChatScroll();
  return (
    <>
      <div ref={scroll.viewportRef} data-testid="viewport">
        <div ref={scroll.contentRef}>
          {Array.from({ length: turns }, (_, index) => (
            <article key={index} data-chat-role="user">Turn {index + 1}</article>
          ))}
        </div>
      </div>
      <button
        type="button"
        onClick={() => {
          scroll.revealNewTurn();
          setTurns(2);
        }}
      >
        Send
      </button>
      <button type="button" onClick={scroll.jumpToLatest}>Jump</button>
      <output>{scroll.hasContentBelow ? "below" : "latest"}</output>
      <output>{scroll.isFollowingLive ? "following" : "stable"}</output>
    </>
  );
}

function LateViewportHarness() {
  const [visible, setVisible] = useState(false);
  const scroll = useChatScroll();
  return (
    <>
      {visible && <div ref={scroll.viewportRef} data-testid="late-viewport"><div ref={scroll.contentRef}>Answer</div></div>}
      <button type="button" onClick={() => setVisible(true)}>Show transcript</button>
      <output>{scroll.hasContentBelow ? "late-below" : "late-latest"}</output>
    </>
  );
}

describe("useChatScroll", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("follows only after Jump to latest and stops when the reader scrolls away", () => {
    render(<ScrollHarness />);
    const viewport = screen.getByTestId("viewport");
    Object.defineProperties(viewport, {
      clientHeight: { configurable: true, value: 300 },
      scrollHeight: { configurable: true, value: 1000 },
      scrollTop: { configurable: true, writable: true, value: 100 },
    });

    fireEvent.scroll(viewport);
    expect(screen.getByText("below")).toBeInTheDocument();
    expect(screen.getByText("stable")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Jump" }));
    expect(viewport.scrollTop).toBe(1000);
    expect(screen.getByText("following")).toBeInTheDocument();

    viewport.scrollTop = 200;
    fireEvent.scroll(viewport);
    expect(screen.getByText("stable")).toBeInTheDocument();
    expect(screen.getByText("below")).toBeInTheDocument();
  });

  it("reveals a newly sent user turn once without enabling follow mode", () => {
    let frame: FrameRequestCallback | undefined;
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn((callback: FrameRequestCallback) => {
        frame = callback;
        return 1;
      }),
    );
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    render(<ScrollHarness />);
    const viewport = screen.getByTestId("viewport");
    Object.defineProperties(viewport, {
      clientHeight: { configurable: true, value: 300 },
      scrollHeight: { configurable: true, value: 1000 },
      scrollTop: { configurable: true, writable: true, value: 0 },
    });

    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const secondTurn = screen.getByText("Turn 2");
    Object.defineProperty(secondTurn, "offsetTop", { configurable: true, value: 520 });
    act(() => frame?.(16));

    expect(viewport.scrollTop).toBe(504);
    expect(screen.getByText("stable")).toBeInTheDocument();
  });

  it("attaches scroll intent after an empty view mounts its transcript", () => {
    render(<LateViewportHarness />);
    fireEvent.click(screen.getByRole("button", { name: "Show transcript" }));
    const viewport = screen.getByTestId("late-viewport");
    Object.defineProperties(viewport, {
      clientHeight: { configurable: true, value: 200 },
      scrollHeight: { configurable: true, value: 800 },
      scrollTop: { configurable: true, writable: true, value: 100 },
    });
    fireEvent.scroll(viewport);
    expect(screen.getByText("late-below")).toBeInTheDocument();
  });
});
