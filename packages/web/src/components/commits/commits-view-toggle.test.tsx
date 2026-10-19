// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import { CommitsViewToggle } from "./commits-view-toggle";

afterEach(() => {
  cleanup();
  push.mockReset();
});

describe("CommitsViewToggle", () => {
  it("marks the current view and switches through the URL", () => {
    render(<CommitsViewToggle repoId="r1" view="commits" />);
    expect(screen.getByRole("radiogroup", { name: "History view" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "By commit" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("radio", { name: "By change" }).getAttribute("aria-checked")).toBe("false");

    fireEvent.click(screen.getByRole("radio", { name: "By change" }));
    expect(push).toHaveBeenLastCalledWith("/repos/r1/commits?view=changes");

    fireEvent.click(screen.getByRole("radio", { name: "Events" }));
    expect(push).toHaveBeenLastCalledWith("/repos/r1/commits?view=events");
  });

  it("goes back to the default commit view without a view param", () => {
    render(<CommitsViewToggle repoId="r1" view="changes" />);
    fireEvent.click(screen.getByRole("radio", { name: "By commit" }));
    expect(push).toHaveBeenLastCalledWith("/repos/r1/commits");
  });
});
