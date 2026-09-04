import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type {
  ChurnComplexityResponse,
  HealthMapFeed,
  HealthOverviewResponse,
  HealthTrendResponse,
} from "@repowise-dev/types/health";
import { OVERLAY_SPECS } from "@repowise-dev/ui/health/code-health-map";
import { scoreBand, scoreTextColor } from "@repowise-dev/ui/health/tokens";
import type { WebviewHost } from "../../runtime/rpc";
import { App } from "./App";

// jsdom has no ResizeObserver; the code map observes its container, so stub it.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;

const overview: HealthOverviewResponse = {
  summary: {
    file_count: 128,
    average_health: 7.4,
    hotspot_health: 5.1,
    worst_performer_path: "src/worst.py",
    worst_performer_score: 2.3,
    open_findings: 42,
    band: "warning",
    maintainability_average: 8.2,
    performance_average: 9.9,
    maintainability_findings: 6,
    performance_findings: 0,
  },
  distribution: {
    total_files: 128,
    total_nloc: 10000,
    bands: {
      healthy: { files: 80, nloc: 6000, pct: 60 },
      warning: { files: 40, nloc: 3000, pct: 30 },
      alert: { files: 8, nloc: 1000, pct: 10 },
    },
  },
  files: [],
  top_findings: [],
};

const files: HealthMapFeed = {
  cap: 2000,
  shown: 2,
  eligible_total: 128,
  repository_total: 130,
  selection: {
    basis: "active_then_performance_then_nloc",
    active_requested: [],
    active_shown: [],
    active_missing: [],
    performance_shown: 0,
    performance_eligible: 0,
    nloc_shown: 2,
  },
  omitted: { files: 126, performance_files: 0, opportunities: 0, observations: 0 },
  recovery: {},
  modules: [],
  performance: null,
  files: [
    {
      file_path: "src/worst.py",
      score: 2.3,
      max_ccn: 40,
      max_nesting: 6,
      nloc: 320,
      has_test_file: false,
      line_coverage_pct: 12,
      module: "core",
    },
    // 7.6 is the score the old local threshold got wrong: it called anything
    // at or above 7.5 healthy-green while the map beside it painted amber.
    {
      file_path: "src/mid.py",
      score: 7.6,
      max_ccn: 9,
      max_nesting: 2,
      nloc: 140,
      has_test_file: true,
      line_coverage_pct: 71,
      module: "core",
    },
  ],
};

const trend: HealthTrendResponse = {
  history: [
    {
      taken_at: "2026-06-01",
      hotspot_health: 5.0,
      average_health: 7.2,
      worst_performer_path: "src/worst.py",
      worst_performer_score: 2.1,
    },
    {
      taken_at: "2026-07-01",
      hotspot_health: 5.1,
      average_health: 7.4,
      worst_performer_path: "src/worst.py",
      worst_performer_score: 2.3,
    },
  ],
  summary: {
    current_hotspot_health: 5.1,
    current_average_health: 7.4,
    previous_hotspot_health: 5.0,
    previous_average_health: 7.2,
    hotspot_delta: 0.1,
    average_delta: 0.2,
  },
  alerts: [],
  file_deltas: [],
  snapshot_count: 2,
};

const churn: ChurnComplexityResponse = {
  total: 1,
  points: [
    {
      file_path: "src/worst.py",
      commit_count_90d: 12,
      max_ccn: 40,
      nloc: 320,
      score: 2.3,
      churn_percentile: 98,
    },
  ],
};

function makeHost(): { host: WebviewHost; openFile: ReturnType<typeof vi.fn> } {
  const openFile = vi.fn();
  const host = {
    api: {
      healthOverview: () => Promise.resolve(overview),
      healthMap: () => Promise.resolve(files),
      healthTrend: () => Promise.resolve(trend),
      churnComplexity: () => Promise.resolve(churn),
    },
    openFile,
  } as unknown as WebviewHost;
  return { host, openFile };
}

afterEach(cleanup);

describe("Health dashboard", () => {
  it("renders the lede, the map section and the trend from host data", async () => {
    const { host } = makeHost();
    render(
      <App
        host={host}
        repo={{ id: "r1", name: "demo-repo", headCommit: "abcdef1234567", defaultBranch: "main" }}
        params={{}}
        refreshToken={0}
      />,
    );

    // The lede leads with the defect score, as the web code-health page does.
    expect(await screen.findByText("Defect risk")).toBeTruthy();
    // The figure, and again inside the sentence that makes it mean something.
    expect(screen.getAllByText("7.4").length).toBeGreaterThan(0);

    // The map is the page spine; trend sits under it.
    expect(screen.getByText("Code health map")).toBeTruthy();
    expect(screen.getByText("Trend")).toBeTruthy();

    // Repo identity is surfaced in the header.
    expect(screen.getByText("demo-repo")).toBeTruthy();
  });

  it("bands a focused file's score the way the map bands the same file", async () => {
    const { host } = makeHost();
    render(
      <App
        host={host}
        repo={{ id: "r1", name: "demo-repo", headCommit: null, defaultBranch: "main" }}
        params={{ selectPath: "src/mid.py" }}
        refreshToken={0}
      />,
    );

    const figure = await screen.findByText("7.6");
    const file = files.files.find((f) => f.file_path === "src/mid.py")!;

    // The contradiction this replaced: the panel called 7.6 green while the
    // canvas beside it coloured the same node amber.
    //
    // The two are no longer the same colour value, and must not be asserted to
    // be. Inking text and filling a disc are different jobs, so the map paints
    // from the canvas ramp and the panel from the semantic one. What may never
    // differ is the band beneath both, which is what this pins: the map fills
    // the node token for this file's band, and the figure carries the ink that
    // the same band function gives the same score.
    const band = scoreBand(file.score);
    expect(band).toBe("fair");
    expect(OVERLAY_SPECS.health.fill(file)).toBe(`var(--color-node-${band})`);
    expect(figure.className).toContain(scoreTextColor(file.score));
  });

  it("shows an error panel when the host fails", async () => {
    const host = {
      api: {
        healthOverview: () => Promise.reject(new Error("server down")),
        healthMap: () => Promise.resolve(files),
        healthTrend: () => Promise.resolve(trend),
        churnComplexity: () => Promise.resolve(churn),
      },
      openFile: vi.fn(),
    } as unknown as WebviewHost;

    render(
      <App
        host={host}
        repo={{ id: "r1", name: "demo-repo", headCommit: null, defaultBranch: "main" }}
        params={{}}
        refreshToken={0}
      />,
    );

    expect(await screen.findByText("Health data is unavailable")).toBeTruthy();
    expect(screen.getByText("server down")).toBeTruthy();
  });
});
