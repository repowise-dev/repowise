// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import { HistoryGroupToggle } from "./commits-view-toggle";

afterEach(() => {
  cleanup();
  push.mockReset();
});

describe("HistoryGroupToggle", () => {
  it("switches the history between commits and merged changes", () => {
    render(<HistoryGroupToggle repoId="r1" view="commits" />);
    expect(screen.getByRole("radio", { name: "By commit" }).getAttribute("aria-checked")).toBe("true");

    fireEvent.click(screen.getByRole("radio", { name: "By change" }));
    expect(push).toHaveBeenLastCalledWith("/repos/r1/commits?view=changes");
  });

  it("goes back to the default commit view without a view param", () => {
    render(<HistoryGroupToggle repoId="r1" view="changes" />);
    fireEvent.click(screen.getByRole("radio", { name: "By commit" }));
    expect(push).toHaveBeenLastCalledWith("/repos/r1/commits");
  });
});
