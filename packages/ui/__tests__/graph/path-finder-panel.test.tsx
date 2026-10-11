import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { PathFinderPanel } from "../../src/graph/path-finder-panel.js";

describe("PathFinderPanel", () => {
  it("renders title and disabled find button when inputs are empty", () => {
    render(
      <PathFinderPanel
        searchNodes={vi.fn().mockResolvedValue([])}
        findPath={vi.fn().mockResolvedValue({ path: [], distance: 0, explanation: "" })}
        onPathFound={vi.fn()}
        onClear={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getAllByText("Find Path").length).toBeGreaterThan(0);
    const findBtn = screen.getByRole("button", { name: /Find Path/i });
    expect((findBtn as HTMLButtonElement).disabled).toBe(true);
  });

  it("pre-fills inputs from initialFrom / initialTo", () => {
    render(
      <PathFinderPanel
        searchNodes={vi.fn().mockResolvedValue([])}
        findPath={vi.fn().mockResolvedValue({ path: [], distance: 0, explanation: "" })}
        onPathFound={vi.fn()}
        onClear={vi.fn()}
        onClose={vi.fn()}
        initialFrom="src/a.ts"
        initialTo="src/b.ts"
      />,
    );
    expect((screen.getByPlaceholderText("From file...") as HTMLInputElement).value).toBe("src/a.ts");
    expect((screen.getByPlaceholderText("To file...") as HTMLInputElement).value).toBe("src/b.ts");
  });
});
