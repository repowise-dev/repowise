/**
 * Shape-compat tests: recorded responses from the live hosted API, replayed
 * through the provider and pinned to the shared OSS types. The assignments
 * below are the contract; if a hosted route drifts from the OSS shape, the
 * re-recorded fixture stops satisfying the type or these key-field checks.
 *
 * Fixtures live in __fixtures__/hosted/, recorded from public snapshots
 * (pallets/flask for data domains, repowise for docs) and trimmed to a few
 * entries. Re-record with a fresh snapshot id when the contract changes.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { FileDetailResponse, FilesIndexResponse } from "@repowise-dev/types/files";
import type {
  ChurnComplexityResponse,
  HealthFilesResponse,
  HealthFinding,
  HealthOverviewResponse,
  HealthTrendResponse,
  RefactoringTargetsResponse,
} from "@repowise-dev/types/health";
import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import { createHostedProvider, type HostedProvider } from "./hosted";
import type {
  ArchitectureGraphResponse,
  HotspotResponse,
  ModuleGraphResponse,
  Paginated,
} from "./types";

const FIXTURES = path.join(path.dirname(fileURLToPath(import.meta.url)), "__fixtures__", "hosted");

function fixture(name: string): unknown {
  return JSON.parse(readFileSync(path.join(FIXTURES, `${name}.json`), "utf-8"));
}

const REPO = {
  repo: {
    id: "repo-1",
    canonical_url: "github.com/pallets/flask",
    owner: "pallets",
    name: "flask",
    default_branch: "main",
    latest_known_head_sha: null,
    is_public: true,
  },
  active_snapshot: { id: "snap-1", short_id: "snap-1", status: "ready" },
};

/** Provider whose fetch serves each snapshot route from its fixture. */
function fixtureProvider(routes: Array<[string, string]>): HostedProvider {
  const table: Array<[string, unknown]> = routes.map(([suffix, name]) => [suffix, fixture(name)]);
  return createHostedProvider({
    baseUrl: "https://api.example.dev",
    fetch: (async (url: string | URL) => {
      const u = String(url);
      if (u.includes("/repos/mine")) return json([REPO]);
      for (const [suffix, body] of table) {
        if (u.includes(suffix)) return json(body);
      }
      throw new Error(`no fixture route for ${u}`);
    }) as typeof fetch,
  });
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("hosted fixtures satisfy the OSS contracts", () => {
  it("health overview / files / findings / trend", async () => {
    const p = fixtureProvider([
      ["/health/overview", "health-overview"],
      ["/health/files/breakdown", "health-files"], // unused; ordering guard
      ["/health/findings", "health-findings"],
      ["/health/files", "health-files"],
      ["/health/trend", "health-trend"],
      ["/health/churn-complexity", "churn-complexity"],
      ["/health/refactoring-targets", "refactoring-targets"],
    ]);
    const overview: HealthOverviewResponse = await p.getHealthOverview("repo-1");
    expect(overview.summary.file_count).toBeGreaterThan(0);
    expect(overview.summary.average_health).toBeTypeOf("number");
    expect(typeof overview.summary.open_findings).toBe("number");
    expect(Array.isArray(overview.files)).toBe(true);
    expect(overview.top_findings[0]).toHaveProperty("biomarker_type");

    const files: HealthFilesResponse = await p.listHealthFiles("repo-1");
    expect(files.files[0]).toHaveProperty("file_path");
    expect(files.files[0]).toHaveProperty("score");
    expect(typeof files.total).toBe("number");

    const findings: HealthFinding[] = await p.listHealthFindings("repo-1");
    expect(findings[0]).toHaveProperty("biomarker_type");
    expect(findings[0]).toHaveProperty("severity");

    const trend: HealthTrendResponse = await p.getHealthTrend("repo-1");
    expect(Array.isArray(trend.history)).toBe(true);
    expect(trend.summary).toHaveProperty("current_hotspot_health");

    const churn: ChurnComplexityResponse = await p.getChurnComplexity("repo-1");
    expect(Array.isArray(churn.points)).toBe(true);

    const targets: RefactoringTargetsResponse = await p.getRefactoringTargets("repo-1");
    expect(Array.isArray(targets.targets)).toBe(true);
  });

  it("overview summary and stats highlights", async () => {
    const p = fixtureProvider([
      ["/overview-summary", "overview-summary"],
      ["/stats/highlights", "stats-highlights"],
    ]);
    const summary: OverviewSummaryResponse = await p.getOverviewSummary("repo-1");
    expect(summary.repo).toBeDefined();
    expect(summary.health).toBeDefined();
    expect(summary.sync).toBeDefined();

    const stats: StatsHighlights = await p.getStatsHighlights("repo-1");
    expect(stats.scale).toBeDefined();
    expect(stats.people).toBeDefined();
  });

  it("files index and file detail", async () => {
    const p = fixtureProvider([
      ["/files/detail", "file-detail"],
      ["/files", "files-index"],
    ]);
    const index: FilesIndexResponse = await p.getFilesIndex("repo-1");
    expect(index.files[0]).toHaveProperty("file_path");
    expect(index.files[0]).toHaveProperty("pagerank_pct");
    expect(typeof index.total).toBe("number");

    const detail: FileDetailResponse = await p.getFileDetail("repo-1", "src/flask/app.py");
    expect(detail).toHaveProperty("file_path");
  });

  it("hotspots page keeps the paginated envelope", async () => {
    const p = fixtureProvider([["/hotspots", "hotspots"]]);
    const page: Paginated<HotspotResponse> = await p.getHotspotsPage("repo-1", { limit: 2 });
    expect(typeof page.total).toBe("number");
    expect(page.items[0]).toHaveProperty("path");
    expect(page.items[0]).toHaveProperty("churn_percentile");
  });

  it("architecture graph and module graph", async () => {
    const p = fixtureProvider([
      ["/architecture", "architecture"],
      ["/module-graph", "module-graph"],
    ]);
    const arch: ArchitectureGraphResponse = await p.getArchitecture("repo-1");
    expect(Array.isArray(arch.nodes)).toBe(true);
    expect(Array.isArray(arch.edges)).toBe(true);

    const modules: ModuleGraphResponse = await p.getModuleGraph("repo-1");
    expect(Array.isArray(modules.nodes)).toBe(true);
  });

  it("decisions, dead code, search and docs map into the local shapes", async () => {
    const p = fixtureProvider([
      ["/decisions", "decisions"],
      ["/dead-code", "dead-code"],
      ["/search", "search"],
      ["/docs", "docs"],
    ]);
    const decisions = await p.listDecisions("repo-1");
    expect(decisions.length).toBeGreaterThan(0);
    expect(decisions[0]).toHaveProperty("title");

    const findings = await p.listDeadCode("repo-1");
    expect(findings[0]!.id).toBeTruthy();
    expect(findings[0]!.status).toBeTruthy();

    const results = await p.search("application factory", { repo_id: "repo-1" });
    expect(results[0]).toHaveProperty("title");
    expect(results[0]).toHaveProperty("target_path");
    expect(typeof results[0]!.page_id).toBe("string");

    const pages = await p.listAllPages("repo-1");
    expect(pages[0]!.id).toBeTruthy();
    expect(pages[0]!.repository_id).toBe("repo-1");
  });

  it("snapshot status carries the polling fields", async () => {
    const p = fixtureProvider([["/snapshots/snap-x", "snapshot-status"]]);
    const status = await p.getSnapshotStatus("snap-x");
    expect(["queued", "indexing", "ready", "failed"]).toContain(status.status);
    expect(status.short_id).toBeTruthy();
  });
});
