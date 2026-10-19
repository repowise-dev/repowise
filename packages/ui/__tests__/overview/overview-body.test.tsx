import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";

import { AttentionAreas } from "../../src/overview/attention-areas.js";
import { HealthLede } from "../../src/overview/health-lede.js";
import { OverviewBody } from "../../src/overview/overview-body.js";
import { buildReads, inventoryRows } from "../../src/overview/overview-model.js";

function summary(): OverviewSummaryResponse {
  return {
    repo: {
      id: "r1",
      name: "acme",
      local_path: "/src/acme",
      default_branch: "main",
      head_commit: "abc1234",
      updated_at: null,
    },
    stats: {
      file_count: 6328,
      symbol_count: 40000,
      entry_point_count: 12,
      doc_page_count: 300,
      doc_prose_page_count: 200,
      doc_coverage_pct: 80,
      freshness_score: 90,
      dead_export_count: 42,
      hotspot_count: 1059,
      silo_count: 3,
      module_count: 20,
      total_nloc: 500000,
      deltas: { average_health: null, hotspot_health: null, file_count: null },
    },
    health: {
      average_health: 8.04,
      hotspot_health: 6.66,
      maintainability_average: 8.6,
      performance_average: 9.7,
      worst_performer_path: null,
      worst_performer_score: null,
      open_findings: 10,
      severity_breakdown: {},
      last_indexed_at: null,
      snapshot_count: 1,
      history: [],
    },
    languages: [{ language: "python", file_count: 6000 }],
    attention: [],
    onboarding_targets: [],
    top_hotspots: [],
    recent_decisions: [
      { id: "d1", title: "Use SQLite", status: "active", source: "adr", created_at: null },
    ],
    savings: { available: true, saved_input_tokens: 5_000_000, inferred_saved_input_tokens: 10 },
    sync: {
      last_sync_at: null,
      last_resync_at: null,
      last_sync_model: null,
      active_job_id: null,
      page_count: 300,
      index_storage_bytes: 1_000_000,
    },
  } as unknown as OverviewSummaryResponse;
}

describe("HealthLede", () => {
  it("leads with the score and one hotspot reading, without the other pillars", () => {
    render(
      <HealthLede
        score={8.04}
        maintainability={8.6}
        performance={9.7}
        hotspotHealth={6.66}
        hotspotCount={1059}
        fileCount={6328}
        href="/h"
      />,
    );
    expect(screen.getByText("8.0")).toBeInTheDocument();
    expect(screen.getByText("6.6")).toBeInTheDocument();
    expect(document.body.textContent).toMatch(/Hotspots average 6\.6, the place fixes pay off most\./);
    expect(document.body.textContent).not.toMatch(/maintainability|performance|8\.6|9\.7/i);
    expect(screen.getByRole("button", { name: "What Code health means" })).toBeInTheDocument();
  });

  it("names hotspots that hold up as a good state", () => {
    render(<HealthLede score={7} hotspotHealth={7.5} hotspotCount={3} fileCount={10} href="/h" />);
    expect(document.body.textContent).toMatch(/as healthy as the rest of the code/);
  });

  it("takes its words as props", () => {
    render(
      <HealthLede
        score={7}
        hotspotHealth={5}
        hotspotCount={3}
        fileCount={10}
        href="/h"
        copy={{ reportLink: "Rapport", hotspotReading: (f) => <>Points chauds {f}</> }}
      />,
    );
    expect(screen.getByText("Rapport")).toBeInTheDocument();
    expect(document.body.textContent).toMatch(/Points chauds 5\.0/);
  });
});

describe("buildReads", () => {
  it("keeps documentation and drops savings, dead code and index size", () => {
    const keys = buildReads(summary(), { base: "/r", costs: "/r/costs", settings: "/r/s" }).map(
      (r) => r.key,
    );
    expect(keys).toEqual(["docs"]);
  });
});

describe("OverviewBody", () => {
  it("is short: no ribbon, recent activity or composition, and renders the wins slot", () => {
    render(
      <OverviewBody
        summary={summary()}
        routes={{ base: "/r" }}
        commits={[]}
        totalNloc={500000}
        slots={{ wins: <p>Recent wins here</p> }}
      />,
    );
    expect(screen.getByText("Recent wins here")).toBeInTheDocument();
    for (const gone of ["Recent activity", "Composition", "Lines of code", "Estimated agent savings", "Dead code"]) {
      expect(screen.queryByText(gone)).toBeNull();
    }
    expect(screen.getByRole("link", { name: /Decisions/ })).toHaveAttribute("href", "/r/decisions");
  });

  it("orders ask, explore cards, Do next, wins, publish, then the folded inventory", () => {
    const { container } = render(
      <OverviewBody
        summary={{
          ...summary(),
          attention_summary: { total: 1, areas: [] },
          attention: [{ type: "hotspot", title: "x", target: "a.py", severity: "high", score: 1 }],
        } as unknown as OverviewSummaryResponse}
        routes={{ base: "/r" }}
        slots={{
          ask: <p>ASK</p>,
          actions: <p>DO NEXT</p>,
          wins: <p>WINS</p>,
          beforeExplore: <p>PUBLISH</p>,
        }}
      />,
    );
    const text = container.textContent ?? "";
    const order = ["ASK", "Explore this codebase", "DO NEXT", "WINS", "PUBLISH", "Everything we found"].map(
      (s) => text.indexOf(s),
    );
    expect(order.every((i) => i >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });

  it("gives each main door a live figure read through its page's basis", () => {
    const withCounts = {
      ...summary(),
      explore: {
        decisions: { total: 12, accepted: 7, candidates: 4, needs_review: 1 },
        refactoring: { opportunities_total: 9, files_total: 9 },
        ownership: { sole_owned_files: 41 },
        security: { total: 10, source: 1, source_high: 1, elsewhere: 9 },
        coverage: { line_coverage_pct: 81.25, ingested_at: null },
      },
    } as unknown as OverviewSummaryResponse;
    render(<OverviewBody summary={withCounts} routes={{ base: "/r" }} />);
    const map = screen.getByRole("link", { name: /Code health map/ });
    expect(map).toHaveAttribute("href", "/r/code-health");
    expect(map).toHaveTextContent("1,059");
    expect(screen.getByRole("link", { name: /^Ownership/ })).toHaveTextContent("41sole-owned files");
    expect(screen.getByRole("link", { name: /^Decisions/ })).toHaveTextContent(
      "12recorded, of which 7 accepted and 4 awaiting review",
    );
    expect(screen.getByRole("link", { name: /^Refactoring/ })).toHaveTextContent("9open opportunities");
    expect(screen.getByRole("link", { name: /^Tests/ })).toHaveTextContent("81.3%line coverage");
    // Docs and Commits are further doors now, once each.
    expect(screen.getAllByRole("link", { name: /^Docs/ })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Commits" })).toHaveAttribute("href", "/r/commits");
    expect(screen.getAllByRole("link", { name: /^Decisions/ })).toHaveLength(1);
  });

  it("offers no command where the server cannot say whether a report exists", () => {
    render(<OverviewBody summary={summary()} routes={{ base: "/r" }} />);
    expect(screen.getByRole("link", { name: /^Tests/ })).toHaveTextContent("Coverage and the tests");
    expect(screen.queryByText("repowise coverage add")).toBeNull();
  });

  it("turns the Tests card into the ingest command when no report exists", () => {
    const noReport = {
      ...summary(),
      explore: { decisions: null, refactoring: null, ownership: null, security: null, coverage: null },
    } as unknown as OverviewSummaryResponse;
    render(<OverviewBody summary={noReport} routes={{ base: "/r" }} />);
    expect(screen.getByRole("link", { name: /^Tests/ })).toHaveTextContent("No coverage report yet");
    expect(screen.getByText("repowise coverage add")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy the command" })).toBeInTheDocument();
  });
});

describe("inventoryRows", () => {
  it("counts each area on the same basis as its card and page", () => {
    const rows = inventoryRows({
      ...summary(),
      health: { ...summary().health, finding_counts: { inventory: 321 } },
      attention_summary: {
        total: 99,
        by_source: {},
        areas: ["health", "security", "refactoring", "decisions", "ownership", "doc_drift"].map((key) => ({
          key,
          total: 5,
          severity: "low",
          detail: "server detail",
          lead: null,
        })),
      },
      explore: {
        decisions: { total: 12, accepted: 7, candidates: 4, needs_review: 1 },
        refactoring: { opportunities_total: 9, files_total: 9 },
        ownership: { sole_owned_files: 41 },
        security: { total: 10, source: 1, source_high: 1, elsewhere: 9 },
        coverage: null,
      },
    } as unknown as OverviewSummaryResponse);
    expect(rows.map((r) => [r.key, r.total, r.unit ?? null, r.detail])).toEqual([
      ["health", 321, "open findings", "server detail"],
      ["security", 1, "in shipped source", "9 more in tests and docs"],
      ["refactoring", 9, "open opportunities", "server detail"],
      ["decisions", 5, "to review", "4 awaiting review, 1 drifting from the code"],
      ["ownership", 41, "sole-owned files", "One author, no second reader"],
      ["doc_drift", 5, null, "server detail"],
    ]);
  });
});

describe("AttentionAreas", () => {
  it("shows the worst item's severity once, as a dot and a word, with no hidden copy", () => {
    render(
      <AttentionAreas
        prefix="/r"
        areas={[
          {
            key: "refactoring",
            total: 9,
            unit: "open opportunities",
            severity: "low",
            detail: "",
            lead: { title: "Split parse()", description: "+1.2 health", type: "refactoring", severity: "low" },
          },
        ]}
      />,
    );
    const row = screen.getByRole("link", { name: /Refactoring/ });
    expect(row).toHaveTextContent("9 open opportunities");
    expect(row.textContent!.match(/Low/g)).toHaveLength(1);
    expect(row.textContent).not.toMatch(/worst item is/);
  });
});

describe("AttentionAreas paths", () => {
  it("names a lead that is a path by its file first", () => {
    render(
      <AttentionAreas
        prefix="/r"
        areas={[
          {
            key: "health",
            total: 3,
            severity: "high",
            detail: "",
            lead: {
              title: "packages/core/src/deep/engine.py",
              target_id: "packages/core/src/deep/engine.py",
              description: "3 findings",
              type: "health_finding",
              severity: "high",
            },
          },
        ]}
      />,
    );
    expect(screen.getByText("engine.py")).toBeInTheDocument();
  });
});
