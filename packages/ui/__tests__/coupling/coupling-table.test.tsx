import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { CouplingEdge } from "@repowise-dev/types/coupling";
import { CouplingTable } from "../../src/coupling/coupling-table.js";

function edge(
  s: string,
  t: string,
  strength = 3,
  last: string | null = "2026-06-01",
): CouplingEdge {
  return { source: s, target: t, strength, last_co_change: last };
}

describe("CouplingTable (virtualized)", () => {
  it("renders a row per coupling with the file basenames", () => {
    const edges = [edge("api/a.py", "core/b.py", 4), edge("core/c.py", "ui/d.py", 1)];
    render(<CouplingTable edges={edges} />);
    expect(screen.getByText("a.py")).toBeInTheDocument();
    expect(screen.getByText("↔ b.py")).toBeInTheDocument();
    expect(screen.getByText("c.py")).toBeInTheDocument();
    expect(screen.getByText("↔ d.py")).toBeInTheDocument();
  });

  it("shows the strength value cell", () => {
    render(<CouplingTable edges={[edge("a.py", "b.py", 7)]} />);
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("toggles focus on row click", () => {
    let focus: string | null = null;
    render(
      <CouplingTable
        edges={[edge("a.py", "b.py", 4)]}
        focusedPath={focus}
        onFocusChange={(p) => (focus = p)}
      />,
    );
    fireEvent.click(screen.getByText("a.py"));
    expect(focus).toBe("a.py");
  });

  it("shows the empty state when there are no couplings", () => {
    render(<CouplingTable edges={[]} />);
    expect(screen.getByText(/no couplings detected/i)).toBeInTheDocument();
  });

  it("renders the AI decouple action when onGeneratePrompt is provided", () => {
    const onGenerate = vi.fn();
    render(<CouplingTable edges={[edge("a.py", "b.py")]} onGeneratePrompt={onGenerate} />);
    const btn = screen.getByRole("button", { name: /ai decouple prompt/i });
    fireEvent.click(btn);
    expect(onGenerate).toHaveBeenCalledTimes(1);
  });
});
