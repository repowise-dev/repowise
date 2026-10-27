import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { HostedNudge } from "../../src/shared/hosted-nudge";

const KEY = "repowise:hosted-nudge-dismissed:mcp";

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

function renderNudge() {
  return render(
    <HostedNudge
      id="mcp"
      text="Use this repo inside Claude.ai."
      href="https://repowise.dev/hosted?src=x#mcp"
      action={<button type="button">Publish it free</button>}
    />,
  );
}

describe("HostedNudge", () => {
  it("shows the sentence, the action and a See how link that opens in a new tab", async () => {
    renderNudge();
    expect(await screen.findByText("Use this repo inside Claude.ai.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish it free" })).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "See how" });
    expect(link).toHaveAttribute("href", "https://repowise.dev/hosted?src=x#mcp");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("remembers a dismissal across mounts", async () => {
    const { unmount } = renderNudge();
    fireEvent.click(await screen.findByRole("button", { name: /dismiss this repowise\.dev tip/i }));
    expect(screen.queryByText("Use this repo inside Claude.ai.")).not.toBeInTheDocument();
    expect(window.localStorage.getItem(KEY)).toBe("1");
    unmount();

    renderNudge();
    expect(screen.queryByText("Use this repo inside Claude.ai.")).not.toBeInTheDocument();
  });

  it("renders and dismisses when storage throws", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    renderNudge();
    fireEvent.click(await screen.findByRole("button", { name: /dismiss this repowise\.dev tip/i }));
    expect(screen.queryByText("Use this repo inside Claude.ai.")).not.toBeInTheDocument();
  });
});
