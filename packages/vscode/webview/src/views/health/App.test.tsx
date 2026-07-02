import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type {
  ChurnComplexityResponse,
  HealthFilesResponse,
  HealthOverviewResponse,
  HealthTrendResponse,
} from "@repowise-dev/types/health";
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

const files: HealthFilesResponse = {
  total: 128,
  offset: 0,
  limit: 2000,
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
      healthFiles: () => Promise.resolve(files),
      healthTrend: () => Promise.resolve(trend),
      churnComplexity: () => Promise.resolve(churn),
    },
    openFile,
  } as unknown as WebviewHost;
  return { host, openFile };
}

afterEach(cleanup);

describe("Health dashboard", () => {
  it("renders the KPI header and the map section from host data", async () => {
    const { host } = makeHost();
    render(
      <App
        host={host}
        repo={{ id: "r1", name: "demo-repo", headCommit: "abcdef1234567", defaultBranch: "main" }}
        params={{}}
        refreshToken={0}
      />,
    );

    // KPI header: the three co-equal signal tiles.
    expect(await screen.findByText("Defect risk")).toBeTruthy();
    expect(screen.getByText("Maintainability")).toBeTruthy();
    expect(screen.getByText("Performance")).toBeTruthy();

    // The map is the hero section.
    expect(screen.getByText("Code health map")).toBeTruthy();

    // Repo identity is surfaced in the header.
    expect(screen.getByText("demo-repo")).toBeTruthy();
  });

  it("shows an error panel when the host fails", async () => {
    const host = {
      api: {
        healthOverview: () => Promise.reject(new Error("server down")),
        healthFiles: () => Promise.resolve(files),
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
