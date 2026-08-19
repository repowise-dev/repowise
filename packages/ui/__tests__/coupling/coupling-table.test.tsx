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

  it("toggles the pin on row click", () => {
    const onPinToggle = vi.fn();
    render(
      <CouplingTable edges={[edge("a.py", "b.py", 4)]} pinnedPath={null} onPinToggle={onPinToggle} />,
    );
    fireEvent.click(screen.getByText("a.py"));
    expect(onPinToggle).toHaveBeenCalledWith("a.py");
  });

  it("renders file names as links when linkForPath is provided", () => {
    render(
      <CouplingTable
        edges={[edge("api/a.py", "core/b.py", 4)]}
        linkForPath={(p) => `/repos/r/files/${p}`}
      />,
    );
    const link = screen.getByText("a.py").closest("a");
    expect(link).not.toBeNull();
    expect(link).toHaveAttribute("href", "/repos/r/files/api/a.py");
  });

  it("sorts by strength ascending when the Strength header is toggled", () => {
    const edges = [edge("a.py", "b.py", 9), edge("c.py", "d.py", 1)];
    render(<CouplingTable edges={edges} />);
    // Default is strength desc → strongest (9) first. Toggle to ascending.
    fireEvent.click(screen.getByRole("button", { name: /strength/i }));
    const rows = screen.getAllByRole("row").filter((r) => r.querySelector("td"));
    // First data row should now be the weakest pair (c.py ↔ d.py).
    expect(rows[0]).toHaveTextContent("c.py");
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
