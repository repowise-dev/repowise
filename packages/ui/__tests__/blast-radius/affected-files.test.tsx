import { describe, expect, it } from "vitest";
import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { AffectedByModule, ChangedFilesList } from "../../src/blast-radius/affected-files.js";
import { readReach } from "../../src/blast-radius/blast-map-model.js";

const BASE = {
  cochange_warnings: [],
  recommended_reviewers: [],
  structural_impact_score: 1,
  structural_impact_band: "localized",
  structural_impact_scale: {},
  overall_risk_score: 1,
  overall_risk_score_compatibility: {},
} as unknown as BlastRadiusResponse;

const RESULT: BlastRadiusResponse = {
  ...BASE,
  direct_risks: [
    { path: "src/core.py", structural_score: 0.2, risk_score: 0.2, temporal_hotspot: 13.6, churn_percentile: 0.97, is_hotspot: true, centrality: 0.004 },
  ],
  transitive_affected: [
    { path: "src/api/a.py", depth: 1 },
    { path: "src/api/b.py", depth: 2 },
    { path: "web/x.ts", depth: 2 },
    { path: "tests/test_core.py", depth: 1 },
  ],
  test_gaps: ["src/api/b.py"],
  affected_signals: [
    { path: "src/api/a.py", is_hotspot: true, health_score: 4.0, module: "src/api" },
    { path: "src/api/b.py", is_hotspot: false, health_score: null, module: "src/api" },
  ],
};

function Dependents() {
  const [q, setQ] = useState("");
  return <AffectedByModule reach={readReach(RESULT, ["src/core.py"])} fileHref={undefined} query={q} onQueryChange={setQ} />;
}

describe("ChangedFilesList", () => {
  it("says changed often in words, never raw pagerank as a percent", () => {
    render(<ChangedFilesList result={RESULT} reach={readReach(RESULT, ["src/core.py"])} fileHref={undefined} />);
    expect(screen.getByText("changed often")).toBeInTheDocument();
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("groups many changed files by folder", () => {
    const files = Array.from({ length: 9 }, (_, i) => `${i < 5 ? "src/a" : "src/b"}/f${i}.py`);
    render(<ChangedFilesList result={RESULT} reach={readReach(RESULT, files)} fileHref={undefined} />);
    expect(screen.getByText((_, el) => el?.tagName === "P" && el.textContent === "src/a/ 5")).toBeInTheDocument();
    expect(screen.getByText((_, el) => el?.tagName === "P" && el.textContent === "src/b/ 4")).toBeInTheDocument();
  });
});

describe("AffectedByModule", () => {
  it("groups source files by module, nearest first, leaves tests out, and filters", () => {
    render(<Dependents />);
    const rows = screen.getAllByRole("button", { expanded: false }).map((b) => b.textContent ?? "");
    expect(rows[0]).toContain("src/api");
    expect(rows[0]).toContain("2 files");
    expect(rows[0]).toContain("direct");
    expect(rows.some((r) => r.includes("tests"))).toBe(false);

    fireEvent.click(screen.getAllByRole("button", { expanded: false })[0]!);
    expect(screen.getByText("a.py")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: "Filter files that depend on the change" }), { target: { value: "web" } });
    expect(screen.queryByText("src/api")).toBeNull();
    expect(screen.getByText("x.ts")).toBeInTheDocument();
  });

  it("shows five files per module, the rest one click away", () => {
    const many: BlastRadiusResponse = {
      ...RESULT,
      transitive_affected: Array.from({ length: 8 }, (_, i) => ({ path: `src/m/f${i}.py`, depth: 1 })),
    };
    render(<AffectedByModule reach={readReach(many, ["src/core.py"])} fileHref={undefined} query="" onQueryChange={() => {}} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getAllByRole("listitem").filter((li) => li.textContent?.includes(".py")).length).toBeGreaterThanOrEqual(5);
    expect(screen.queryByText("f7.py")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "+3 more" }));
    expect(screen.getByText("f7.py")).toBeInTheDocument();
  });
});
