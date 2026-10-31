// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import { CommitsViewToggle, HistoryGroupToggle } from "./commits-view-toggle";

afterEach(() => {
  cleanup();
  push.mockReset();
});

describe("CommitsViewToggle", () => {
  it("offers two lenses and marks History for either grouping", () => {
    render(<CommitsViewToggle repoId="r1" view="changes" />);
    expect(screen.getAllByRole("radio").map((r) => r.textContent)).toEqual(["History", "Review a change"]);
    expect(screen.getByRole("radio", { name: "History" }).getAttribute("aria-checked")).toBe("true");

    fireEvent.click(screen.getByRole("radio", { name: "Review a change" }));
    expect(push).toHaveBeenLastCalledWith("/repos/r1/commits?view=review");
  });

  it("goes back to the default commit view without a view param", () => {
    render(<CommitsViewToggle repoId="r1" view="review" />);
    fireEvent.click(screen.getByRole("radio", { name: "History" }));
    expect(push).toHaveBeenLastCalledWith("/repos/r1/commits");
  });
});

describe("HistoryGroupToggle", () => {
  it("switches the history between commits and merged changes", () => {
    render(<HistoryGroupToggle repoId="r1" view="commits" />);
    expect(screen.getByRole("radio", { name: "By commit" }).getAttribute("aria-checked")).toBe("true");

    fireEvent.click(screen.getByRole("radio", { name: "By change" }));
    expect(push).toHaveBeenLastCalledWith("/repos/r1/commits?view=changes");
  });
});
