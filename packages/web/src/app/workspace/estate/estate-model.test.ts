import { describe, expect, it } from "vitest";
import type { EstateCheck, EstateReport } from "@repowise-dev/types/estate";
import { graphEvidence, missingGraph, repoRows } from "./estate-model";

function check(name: string, data?: Record<string, unknown>): EstateCheck {
  return { name, score: null, reason: "", details: [], method: "", basis: "measured", data: data ?? null };
}

function report(sections: { id: string; checks: EstateCheck[] }[], repos: EstateReport["repos"]): EstateReport {
  return {
    schema_version: 1,
    kind: "estate",
    generated_at: "2026-10-24T00:00:00+00:00",
    generator: { name: "repowise", version: "0", stamp: "s" },
    scope: { kind: "workspace", name: "ws" },
    notice: "",
    repos,
    sections: sections.map((s) => ({ ...s, title: s.id, summary: "", repo: null })),
  };
}

describe("repoRows", () => {
  it("joins health, churn and magnets per configured repo, lowest health first", () => {
    const rows = repoRows(
      report(
        [
          {
            id: "estate-health",
            checks: [
              check("health", {
                repositories: [
                  { repo: "api", average_health: 5.2 },
                  { repo: "web", average_health: 8.4 },
                ],
              }),
              check("churn-hotspots", { repositories: { web: { commits: 9, hotspots: 2 } } }),
              check("bug-magnets", { repositories: { api: { commits: 9, bug_magnets: 1 } } }),
            ],
          },
        ],
        [
          { alias: "docs", name: "docs", indexed: false },
          { alias: "web", name: "web" },
          { alias: "api", name: "api" },
        ],
      ),
    );
    expect(rows.map((r) => r.repo)).toEqual(["api", "web", "docs"]);
    expect(rows[0]).toMatchObject({ indexed: true, churn: null, magnets: { bug_magnets: 1 } });
    expect(rows[1]).toMatchObject({ churn: { hotspots: 2 }, magnets: null });
    expect(rows[2]).toMatchObject({ indexed: false, health: null });
  });
});

describe("graph state", () => {
  it("reads the missing-graph item and the evidence block from the summary", () => {
    const none = report([{ id: "estate-summary", checks: [check("estate")] }], []);
    expect(missingGraph(none)?.name).toBe("estate");
    expect(graphEvidence(none)).toBeNull();

    const partial = report(
      [{ id: "estate-summary", checks: [check("evidence", { state: "partial", summary: "x" })] }],
      [],
    );
    expect(missingGraph(partial)).toBeNull();
    expect(graphEvidence(partial)?.state).toBe("partial");
  });
});
