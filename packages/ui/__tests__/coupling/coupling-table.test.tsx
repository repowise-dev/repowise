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
  return {
    source: s,
    target: t,
    strength,
    last_co_change: last,
    support: 0,
    confidence_ab: null,
    confidence_ba: null,
    structural: null,
    dependency_kind: null,
  };
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

  it("sorts by strength ascending when the Strength header is toggled twice", () => {
    const edges = [edge("a.py", "b.py", 9), edge("c.py", "d.py", 1)];
    render(<CouplingTable edges={edges} />);
    // Default is confidence, not strength: the first click switches key and
    // lands on desc (strongest first), the second flips it.
    const strength = inTable().getByRole("button", { name: /strength/i });
    fireEvent.click(strength);
    fireEvent.click(strength);
    const rows = screen.getAllByRole("row").filter((r) => r.querySelector("td"));
    // First data row should now be the weakest pair (c.py ↔ d.py).
    expect(rows[0]).toHaveTextContent("c.py");
  });

  it("shows the empty state when there are no couplings", () => {
    render(<CouplingTable edges={[]} />);
    expect(screen.getByText(/no couplings detected/i)).toBeInTheDocument();
  });

  it("keeps per-row actions out of the table", () => {
    // The AI prompt lives in the pair panel, where it can carry a label.
    render(<CouplingTable edges={[edge("a.py", "b.py")]} hasDetails />);
    expect(inTable().queryByRole("button", { name: /ai decouple prompt/i })).not.toBeInTheDocument();
  });

  it("names each side's module so a cross-boundary pair is visible", () => {
    const moduleFor = (p: string) => (p.startsWith("ui/") ? "ui" : "server");
    render(<CouplingTable edges={[edge("ui/a.py", "server/b.py")]} moduleFor={moduleFor} />);
    expect(inTable().getByText(/ui/)).toBeInTheDocument();
  });

  it("says so when both files sit in the same module", () => {
    render(<CouplingTable edges={[edge("ui/a.py", "ui/b.py")]} moduleFor={() => "ui"} />);
    expect(inTable().getByText("within ui")).toBeInTheDocument();
  });
});

describe("CouplingTable together column", () => {
  const withSupport = (
    s: string,
    t: string,
    support: number,
    ab: number | null,
    ba: number | null,
  ): CouplingEdge => ({
    ...edge(s, t),
    support,
    confidence_ab: ab,
    confidence_ba: ba,
  });

  it("shows the shared commit count and the stronger of the two directions", () => {
    render(<CouplingTable edges={[withSupport("a/README.md", "b/BENCH.md", 11, 0.11, 0.92)]} />);
    expect(inTable().getByText("11 commits")).toBeInTheDocument();
    // 0.92 is the claim the pair actually supports; 0.11 would undersell it.
    expect(inTable().getByText("up to 92% of one side")).toBeInTheDocument();
  });

  it("renders a dash when the index recorded no support", () => {
    render(<CouplingTable edges={[edge("a.py", "b.py")]} />);
    expect(inTable().queryByText(/commits$/)).not.toBeInTheDocument();
  });

  it("omits the confidence line when neither side has a commit total", () => {
    render(<CouplingTable edges={[withSupport("a.py", "b.py", 4, null, null)]} />);
    expect(inTable().getByText("4 commits")).toBeInTheDocument();
    expect(inTable().queryByText(/of one side/)).not.toBeInTheDocument();
  });

  it("opens ordered by the stronger direction, not by strength or date", () => {
    const edges = [
      // Strongest and most recent, but only ever half one-sided.
      { ...withSupport("a.py", "b.py", 90, 0.5, 0.5), strength: 40, last_co_change: "2026-06-09" },
      // Weaker and older, but one side has never changed alone.
      { ...withSupport("c.py", "d.py", 4, 0.02, 1.0), strength: 2, last_co_change: "2026-06-01" },
    ];
    render(<CouplingTable edges={edges} />);
    const rows = inTable().getAllByRole("row").slice(1);
    expect(within(rows[0]!).getByText("4 commits")).toBeInTheDocument();
  });

  it("breaks a confidence tie with the shared-commit count", () => {
    const edges = [
      withSupport("a.py", "b.py", 2, 0.9, 0.9),
      withSupport("c.py", "d.py", 40, 0.9, 0.9),
    ];
    render(<CouplingTable edges={edges} />);
    const rows = inTable().getAllByRole("row").slice(1);
    expect(within(rows[0]!).getByText("40 commits")).toBeInTheDocument();
  });

  it("states the asymmetry and the structural verdict as a sentence", () => {
    const edges: CouplingEdge[] = [
      {
        ...withSupport("core/answer.py", "core/config.py", 27, 0.47, 1.0),
        structural: "unexplained",
      },
    ];
    render(<CouplingTable edges={edges} />);
    expect(
      inTable().getByText(
        /config\.py has never changed without answer\.py \(27 shared commits\), while answer\.py changed without it 53% of the time\. No dependency in the graph connects them\./,
      ),
    ).toBeInTheDocument();
  });

  it("pins the whole pair on row click when the host understands pairs", () => {
    const onPinPair = vi.fn();
    const e = edge("a.py", "b.py", 4);
    render(<CouplingTable edges={[e]} onPinPair={onPinPair} />);
    fireEvent.click(inTable().getByText("a.py"));
    expect(onPinPair).toHaveBeenCalledWith(e);
  });

  it("selects a row from either end of the pair, not only the smaller path", () => {
    // `b.py` is the target: a pin on either end selects the row.
    const { container } = render(
      <CouplingTable edges={[edge("a.py", "b.py", 4)]} pinnedPath="b.py" />,
    );
    expect(container.querySelector("tr.bg-\\[var\\(--color-accent-muted\\)\\]\\/30")).not.toBeNull();
  });
});
