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

  it("keeps the explanation behind a toggle", async () => {
    const { container } = render(
      <ReleaseNotice id="t" version="1.0.0" detail="Scores are not comparable.">
        Health scores changed in this release.
      </ReleaseNotice>,
    );
    expect(await screen.findByText(/Health scores changed in this release/)).toBeTruthy();
    expect(container.querySelector("details")!.open).toBe(false);
    expect(screen.getByText("What changed")).toBeTruthy();
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
