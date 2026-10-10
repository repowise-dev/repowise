import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ReleaseNotice } from "../../src/shared/release-notice";

afterEach(() => window.localStorage.clear());

describe("ReleaseNotice", () => {
  it("reads as one line with the explanation behind a toggle", async () => {
    const { container } = render(
      <ReleaseNotice id="t" version="1.0.0" detail="Scores are not comparable.">
        Health scores changed in this release.
      </ReleaseNotice>,
    );
    expect(await screen.findByText("Health scores changed in this release.")).toBeTruthy();
    const details = container.querySelector("details")!;
    expect(details.open).toBe(false);
    expect(screen.getByText("What changed")).toBeTruthy();
  });

  it("stays dismissed for the same release", async () => {
    const { unmount } = render(<ReleaseNotice id="t" version="1.0.0">Changed.</ReleaseNotice>);
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("Changed.")).toBeNull();
    unmount();
    render(<ReleaseNotice id="t" version="1.0.0">Changed.</ReleaseNotice>);
    expect(screen.queryByText("Changed.")).toBeNull();
  });
});
