import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  DecisionConflicts,
  GovernedFiles,
  summarizeGovernance,
} from "../../src/decisions/decision-governance.js";
import type { DecisionGraph } from "@repowise-dev/types/decisions";

function graph(over: Partial<DecisionGraph> = {}): DecisionGraph {
  return {
    nodes: [
      { id: "a", title: "Use SWR", status: "active", source: "cli", confidence: 0.9, staleness_score: 0, verification: "exact" },
      { id: "b", title: "Use React Query", status: "proposed", source: "cli", confidence: 0.8, staleness_score: 0, verification: "exact" },
      { id: "c", title: "Vendor the client", status: "proposed", source: "cli", confidence: 0.7, staleness_score: 0, verification: "fuzzy" },
    ],
    decision_edges: [],
    code_edges: [],
    ...over,
  };
}

describe("summarizeGovernance", () => {
  it("returns empty lists for a missing graph rather than throwing", () => {
    expect(summarizeGovernance(undefined)).toEqual({
      conflicts: [],
      governedFiles: [],
      governedFileTotal: 0,
    });
  });

  it("keeps conflicts and drops every other edge kind", () => {
    // On a real index 373 of 376 edges were `supersedes` and 3 were conflicts;
    // `refines` and `relates_to` never occurred at all. A supersession is
    // lineage, which DecisionLineage already renders linearly.
    const { conflicts } = summarizeGovernance(
      graph({
        decision_edges: [
          { src: "a", dst: "b", kind: "conflicts_with", confidence: 0.7, evidence: "sim=0.68" },
          { src: "a", dst: "c", kind: "supersedes", confidence: 0.9, evidence: "" },
          { src: "b", dst: "c", kind: "relates_to", confidence: 0.5, evidence: "" },
        ],
      }),
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0]).toMatchObject({
      aTitle: "Use SWR",
      bTitle: "Use React Query",
      evidence: "sim=0.68",
    });
  });

  it("lists a conflicting pair once, whichever direction it arrives in", () => {
    const { conflicts } = summarizeGovernance(
      graph({
        decision_edges: [
          { src: "a", dst: "b", kind: "conflicts_with", confidence: 0.7, evidence: "" },
          { src: "b", dst: "a", kind: "conflicts_with", confidence: 0.7, evidence: "" },
        ],
      }),
    );
    expect(conflicts).toHaveLength(1);
  });

  it("leaves a title undefined when an edge points outside the node payload", () => {
    // 321 of 387 edge endpoints referenced decisions the graph never carried,
    // and on a live index *every* conflict endpoint did. The first cut fell
    // back to the id, which rendered a 32-character hash where a sentence
    // belongs; the caller resolves or drops it instead.
    const { conflicts } = summarizeGovernance(
      graph({
        decision_edges: [
          { src: "a", dst: "ghost", kind: "conflicts_with", confidence: 0.7, evidence: "" },
        ],
      }),
    );
    expect(conflicts[0]?.aTitle).toBe("Use SWR");
    expect(conflicts[0]?.bTitle).toBeUndefined();
  });

  it("accepts caller-supplied titles for ids the graph does not carry", () => {
    const { conflicts } = summarizeGovernance(
      graph({
        decision_edges: [
          { src: "a", dst: "ghost", kind: "conflicts_with", confidence: 0.7, evidence: "" },
        ],
      }),
      { titles: new Map([["ghost", "Recorded elsewhere"]]) },
    );
    expect(conflicts[0]?.bTitle).toBe("Recorded elsewhere");
  });

  it("ranks files by how many distinct decisions govern them", () => {
    const { governedFiles, governedFileTotal } = summarizeGovernance(
      graph({
        code_edges: [
          { decision_id: "a", node_id: "src/api.ts", link_type: "file" },
          { decision_id: "b", node_id: "src/api.ts", link_type: "file" },
          { decision_id: "a", node_id: "src/api.ts", link_type: "file" },
          { decision_id: "a", node_id: "src/one.ts", link_type: "file" },
          { decision_id: "c", node_id: "src/mod", link_type: "module" },
        ],
      }),
    );
    // The duplicate (a, src/api.ts) counts once; the module link is not a file.
    expect(governedFiles).toEqual([
      { path: "src/api.ts", decisionCount: 2 },
      { path: "src/one.ts", decisionCount: 1 },
    ]);
    expect(governedFileTotal).toBe(2);
  });

  it("caps the file list without hiding the true total", () => {
    // The canvas silently dropped 6,400 of 9,221 code links to a per-decision
    // cap. The count it truncated to is reported, so the page can say so.
    const code_edges = Array.from({ length: 30 }, (_, i) => ({
      decision_id: "a",
      node_id: `src/f${i}.ts`,
      link_type: "file" as const,
    }));
    const { governedFiles, governedFileTotal } = summarizeGovernance(
      graph({ code_edges }),
      { topFiles: 5 },
    );
    expect(governedFiles).toHaveLength(5);
    expect(governedFileTotal).toBe(30);
  });
});

describe("DecisionConflicts", () => {
  it("renders nothing when there are none", () => {
    const { container } = render(
      <DecisionConflicts conflicts={[]} decisionHref={(id) => `/d/${id}`} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("drops a pair it cannot name rather than printing a hash", () => {
    const { container } = render(
      <DecisionConflicts
        conflicts={[
          { aId: "a", aTitle: "Use SWR", bId: "e71b45c7eaaa4798a745f59ae80118fc" },
        ]}
        decisionHref={(id) => `/d/${id}`}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("links both sides of a pair", () => {
    render(
      <DecisionConflicts
        conflicts={[
          { aId: "a", aTitle: "Use SWR", bId: "b", bTitle: "Use React Query" },
        ]}
        decisionHref={(id) => `/d/${id}`}
      />,
    );
    expect(screen.getByText("Use SWR").closest("a")).toHaveAttribute("href", "/d/a");
    expect(screen.getByText("Use React Query").closest("a")).toHaveAttribute("href", "/d/b");
  });
});

describe("GovernedFiles", () => {
  it("renders paths in mono with tabular counts", () => {
    const { container } = render(
      <GovernedFiles
        files={[
          { path: "src/api.ts", decisionCount: 12 },
          { path: "src/one.ts", decisionCount: 3 },
        ]}
      />,
    );
    expect(screen.getByText("src/api.ts").className).toContain("font-mono");
    expect(screen.getByText("12").className).toContain("tabular-nums");
    // The bar is proportional to the leader, not to an arbitrary ceiling.
    const bars = container.querySelectorAll('[style*="width"]');
    expect((bars[0] as HTMLElement).style.width).toBe("100%");
    expect((bars[1] as HTMLElement).style.width).toBe("25%");
  });
});
