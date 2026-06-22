import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GraphFlow } from "../../src/graph/graph-flow.js";

// Minimal prop set — no graphs supplied, so the canvas renders its empty state
// while the toolbar (and its color-mode control) still mounts.
const baseProps = {
  moduleGraph: undefined,
  isLoadingModuleGraph: false,
  fullGraph: undefined,
  isLoadingFullGraph: false,
  architectureGraph: undefined,
  isLoadingArchitectureGraph: false,
  deadCodeGraph: undefined,
  isLoadingDeadCodeGraph: false,
  hotFilesGraph: undefined,
  isLoadingHotFilesGraph: false,
} as const;

describe("GraphFlow shell", () => {
  it("renders the empty state when no nodes are layouted", () => {
    render(<GraphFlow {...baseProps} />);
    expect(screen.getByText("No graph data")).toBeTruthy();
  });

  it("reflects a controlled colorMode and reports changes without self-updating", () => {
    const onColorModeChange = vi.fn();
    render(
      <GraphFlow
        {...baseProps}
        colorMode="risk"
        onColorModeChange={onColorModeChange}
      />,
    );

    // Controlled value wins: Risk is active, Community (the default) is not.
    expect(screen.getByRole("button", { name: "Risk" }).getAttribute("aria-pressed")).toBe("true");
    expect(
      screen.getByRole("button", { name: "Community" }).getAttribute("aria-pressed"),
    ).toBe("false");

    // Clicking another mode reports out but does NOT change the displayed mode —
    // the host owns the value and hasn't pushed a new prop yet.
    fireEvent.click(screen.getByRole("button", { name: "Language" }));
    expect(onColorModeChange).toHaveBeenCalledWith("language");
    expect(screen.getByRole("button", { name: "Risk" }).getAttribute("aria-pressed")).toBe("true");
    expect(
      screen.getByRole("button", { name: "Language" }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("tracks its own colorMode when uncontrolled (seeded by initialColorMode)", () => {
    render(<GraphFlow {...baseProps} initialColorMode="language" />);

    expect(
      screen.getByRole("button", { name: "Language" }).getAttribute("aria-pressed"),
    ).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: "Risk" }));
    expect(screen.getByRole("button", { name: "Risk" }).getAttribute("aria-pressed")).toBe("true");
    expect(
      screen.getByRole("button", { name: "Language" }).getAttribute("aria-pressed"),
    ).toBe("false");
  });
});
