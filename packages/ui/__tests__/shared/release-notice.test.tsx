import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ReleaseNotice } from "../../src/shared/release-notice";

describe("ReleaseNotice", () => {
  beforeEach(() => window.localStorage.clear());

  it("renders title, body and action", () => {
    render(
      <ReleaseNotice id="a" version="1" title="Changed." action={<a href="/x">See what changed</a>}>
        Body copy.
      </ReleaseNotice>,
    );
    expect(screen.getByText("Changed.")).toBeInTheDocument();
    expect(screen.getByText(/Body copy/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See what changed" })).toBeInTheDocument();
  });

  it("keeps the explanation behind a toggle on the same line", async () => {
    render(
      <ReleaseNotice id="t" version="1.0.0" detail="Scores are not comparable." action={<a href="/x">Docs</a>}>
        Health scores changed in this release.
      </ReleaseNotice>,
    );
    expect(await screen.findByText(/Health scores changed in this release/)).toBeTruthy();
    const toggle = screen.getByRole("button", { name: "What changed" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Scores are not comparable.")).not.toBeVisible();
    // The link sits in the line itself, not on a row of its own.
    expect(screen.getByRole("link", { name: "Docs" }).parentElement!.tagName).toBe("SPAN");

    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Hide" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Scores are not comparable.")).toBeVisible();
  });

  it("remembers dismissal per version", () => {
    const { unmount } = render(
      <ReleaseNotice id="a" version="1">
        Body
      </ReleaseNotice>,
    );
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByRole("status")).toBeNull();
    unmount();
    render(
      <ReleaseNotice id="a" version="1">
        Body
      </ReleaseNotice>,
    );
    expect(screen.queryByRole("status")).toBeNull();
    render(
      <ReleaseNotice id="a" version="2">
        Newer
      </ReleaseNotice>,
    );
    expect(screen.getByText("Newer")).toBeInTheDocument();
  });

  it("hands dismissal to the host when onDismiss is given", () => {
    const onDismiss = vi.fn();
    render(<ReleaseNotice onDismiss={onDismiss}>Body</ReleaseNotice>);
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
