import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
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

// The table collapses to stacked cards below `md`, and ResponsiveTable keeps
// both trees in the DOM so CSS can pick one. jsdom applies no CSS, so every
// query must be scoped to one tree or it matches twice.
const inTable = () => within(screen.getByRole("table"));

describe("CouplingTable (virtualized)", () => {
  it("renders a row per coupling with the file basenames", () => {
    const edges = [edge("api/a.py", "core/b.py", 4), edge("core/c.py", "ui/d.py", 1)];
    render(<CouplingTable edges={edges} />);
    expect(inTable().getByText("a.py")).toBeInTheDocument();
    expect(inTable().getByText("↔ b.py")).toBeInTheDocument();
    expect(inTable().getByText("c.py")).toBeInTheDocument();
    expect(inTable().getByText("↔ d.py")).toBeInTheDocument();
  });

  it("shows the strength value cell", () => {
    render(<CouplingTable edges={[edge("a.py", "b.py", 7)]} />);
    expect(inTable().getByText("7")).toBeInTheDocument();
  });

  it("toggles the pin on row click", () => {
    const onPinToggle = vi.fn();
    render(
      <CouplingTable edges={[edge("a.py", "b.py", 4)]} pinnedPath={null} onPinToggle={onPinToggle} />,
    );
    fireEvent.click(inTable().getByText("a.py"));
    expect(onPinToggle).toHaveBeenCalledWith("a.py");
  });

  it("renders file names as links when linkForPath is provided", () => {
    render(
      <CouplingTable
        edges={[edge("api/a.py", "core/b.py", 4)]}
        linkForPath={(p) => `/repos/r/files/${p}`}
      />,
    );
    const link = inTable().getByText("a.py").closest("a");
    expect(link).not.toBeNull();
    expect(link).toHaveAttribute("href", "/repos/r/files/api/a.py");
  });

  it("sorts by strength ascending when the Strength header is toggled", () => {
    const edges = [edge("a.py", "b.py", 9), edge("c.py", "d.py", 1)];
    render(<CouplingTable edges={edges} />);
    // Default is strength desc → strongest (9) first. Toggle to ascending.
    fireEvent.click(inTable().getByRole("button", { name: /strength/i }));
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
    const btn = inTable().getByRole("button", { name: /ai decouple prompt/i });
    fireEvent.click(btn);
    expect(onGenerate).toHaveBeenCalledTimes(1);
  });
});
