import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type {
  ConformanceReport,
  DsmCell,
  SystemEdge,
  SystemGraph,
  SystemNode,
} from "@repowise-dev/types";
import { buildDsm } from "../../src/workspace/dsm/dsm";
import { DsmMatrixView } from "../../src/workspace/dsm/dsm-matrix";
import { SystemMapConformancePanel } from "../../src/workspace/system-map/system-map-conformance-panel";

function node(id: string, over: Partial<SystemNode> = {}): SystemNode {
  return {
    id,
    repo: id.split("::")[0] ?? id,
    service_path: null,
    name: id,
    kind: "service",
    provider_count: 0,
    consumer_count: 0,
    contract_types: [],
    is_orphan_provider: false,
    is_orphan_consumer: false,
    is_isolated: false,
    ...over,
  };
}

function edge(source: string, target: string, over: Partial<SystemEdge> = {}): SystemEdge {
  return {
    id: `${source}->${target}`,
    source,
    target,
    kind: "http",
    match_type: "exact",
    confidence: 0.9,
    weight: 1,
    structural: true,
    contract_refs: [],
    ...over,
  };
}

function graph(nodes: SystemNode[], edges: SystemEdge[]): SystemGraph {
  return { version: 1, generated_at: "t", nodes, edges, diagnostics: {} as never };
}

const g = graph([node("api"), node("db"), node("web")], [edge("web", "api"), edge("api", "db")]);

describe("DsmMatrixView", () => {
  it("renders a grid with the service axis labels", () => {
    render(<DsmMatrixView matrix={buildDsm(g)} />);
    expect(screen.getByRole("grid", { name: /dependency-structure matrix/i })).toBeInTheDocument();
    // each service appears as a row + column header
    expect(screen.getAllByText("api").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/3/)).toBeInTheDocument(); // 3 services summary
  });

  it("shows the empty state for an empty graph", () => {
    render(<DsmMatrixView matrix={buildDsm(null)} />);
    expect(screen.getByText(/no services to chart/i)).toBeInTheDocument();
  });

  it("fires onSelectCell for a present cell", () => {
    const onSelect = vi.fn();
    render(<DsmMatrixView matrix={buildDsm(g)} onSelectCell={onSelect} />);
    const cells = screen.getAllByRole("gridcell");
    // Click the web->api dependency cell (present) by finding one with a title.
    const present = cells.find((c) => c.getAttribute("title")?.includes("→"));
    expect(present).toBeDefined();
    fireEvent.click(present!);
    expect(onSelect).toHaveBeenCalled();
    const arg = onSelect.mock.calls[0]![0] as DsmCell;
    expect(arg.present).toBe(true);
  });
});

function report(over: Partial<ConformanceReport> = {}): ConformanceReport {
  const violations = over.violations ?? [];
  const cycles = over.cycles ?? [];
  return {
    version: 1,
    generated_at: "t",
    rules_evaluated: 1,
    violations,
    cycles,
    violation_count: violations.length,
    cycle_count: cycles.length,
    violating_repos: [],
    ...over,
  };
}

describe("SystemMapConformancePanel", () => {
  it("lists a violation and its rule", () => {
    render(
      <SystemMapConformancePanel
        report={report({
          violations: [
            {
              rule_source: "frontend",
              rule_target: "db",
              rule_description: "no direct db",
              source: "frontend",
              source_name: "frontend",
              target: "db",
              target_name: "db",
              edge_id: "frontend->db",
              edge_kind: "http",
              severity: "violation",
            },
          ],
        })}
        onClear={() => {}}
      />,
    );
    expect(screen.getByText(/architecture conformance/i)).toBeInTheDocument();
    expect(screen.getByText(/frontend !-> db/)).toBeInTheDocument();
    expect(screen.getByText(/no direct db/)).toBeInTheDocument();
  });

  it("shows the clean state when there are no findings", () => {
    render(<SystemMapConformancePanel report={report()} onClear={() => {}} />);
    expect(screen.getByText(/no rule violations or dependency cycles/i)).toBeInTheDocument();
  });

  it("renders nothing when the report is null", () => {
    const { container } = render(
      <SystemMapConformancePanel report={null} onClear={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
