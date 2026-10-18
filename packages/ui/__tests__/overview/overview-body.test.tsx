import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";

import { HealthLede } from "../../src/overview/health-lede.js";
import { OverviewBody } from "../../src/overview/overview-body.js";
import { buildReads } from "../../src/overview/overview-model.js";

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

  it("gives each main door a live figure from the summary", () => {
    render(<OverviewBody summary={summary()} routes={{ base: "/r" }} />);
    const map = screen.getByRole("link", { name: /Code health map/ });
    expect(map).toHaveAttribute("href", "/r/code-health");
    expect(map).toHaveTextContent("1,059");
    expect(map).toHaveTextContent("hotspots across 6,328 files");
    expect(screen.getByRole("link", { name: /Ownership/ })).toHaveTextContent(
      "3top-level folders where one person owns over 80% of files",
    );
    expect(screen.getByRole("link", { name: /^Commits/ })).toHaveTextContent("abc1234");
    // Doors a card covers are not repeated in the line of further links.
    expect(screen.getAllByRole("link", { name: /^Docs/ })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Files" })).toHaveAttribute("href", "/r/files");
  });
});
