import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { App } from "./App";
import type { WebviewHost } from "../../runtime/rpc";
import type {
  ChangeImpactReport,
  RepoInit,
  RiskRangeReport,
} from "../../../../src/shared/webviewMessages";

const REPORT: RiskRangeReport = {
  base: "main",
  branch: "feat/thing",
  result: {
    base: "main",
    head: "HEAD",
    fix_history: {
      available: true,
      density: 3.2,
      percentile: 74,
      files: [{ path: "src/core.ts", churn: 40, fix_pressure: 5.5 }],
    },
    risk_authority: {
      authoritative_for: "live_change_review",
      primary_fields: ["risk_percentile", "classification"],
      primary_basis: "benchmarked_population_relative",
      fallback_field: "fallback_band",
      fallback_basis: "absolute_model_score_band",
      score_role: "supporting_diff_shape_signal",
    },
    score: 7.4,
    score_measures: "diff size and spread; not where the change lands",
    score_unit: "per-commit",
    risk_percentile: 88,
    review_priority: "high",
    classification: "Elevated",
    fallback_band: null,
    is_fix: false,
    features: {
      la: 120,
      ld: 30,
      nf: 6,
      nd: 2,
      ns: 1,
      entropy: 2.31,
      exp: null,
    },
    drivers: [
      { feature: "la", value: 120, contribution: 1.8, label: "Lines added" },
      {
        feature: "exp",
        value: null,
        contribution: -0.5,
        label: "Author experience",
      },
    ],
  },
};

const REPO: RepoInit = {
  id: "r1",
  name: "repo",
  headCommit: null,
  defaultBranch: "main",
};

/** A clean tree: nothing changed, so no impact sections render. */
const CLEAN_IMPACT: ChangeImpactReport = {
  changed: [],
  stagedCount: 0,
  workingCount: 0,
  scope: "branch",
  blast: null,
  reviewers: [],
  gitUnavailable: false,
};

const IMPACT: ChangeImpactReport = {
  changed: ["src/a.ts", "src/b.ts"],
  stagedCount: 1,
  workingCount: 1,
  scope: "branch",
  blast: {
    direct_risks: [
      {
        path: "src/a.ts",
        structural_score: 0.3,
        risk_score: 0.3,
        temporal_hotspot: 0.1,
        centrality: 0.8,
      },
      {
        path: "src/b.ts",
        structural_score: 0.82,
        risk_score: 0.82,
        temporal_hotspot: 0.9,
        centrality: 0.1,
      },
    ],
    transitive_affected: [{ path: "src/consumer.ts", depth: 1 }],
    cochange_warnings: [
      { changed: "src/a.ts", missing_partner: "src/a.test.ts", score: 8 },
    ],
    recommended_reviewers: [],
    test_gaps: ["src/b.ts"],
    test_impact: {
      recommendations: [
        {
          test_id: "src/a.test.ts::covers_a",
          test_file: "src/a.test.ts",
          repository_id: "r1",
          repository: "repo",
          basis: "measured",
          bases: ["measured"],
          source_files: ["src/a.ts"],
          evidence: [
            {
              basis: "measured",
              source_file: "src/a.ts",
              via: "coverage-map",
              source_format: "istanbul",
            },
          ],
        },
      ],
      recommendations_total: 1,
      recommendations_emitted: 1,
      recommendations_truncated: false,
      recommendations_omitted: 0,
      recommendations_by_primary_basis: { measured: 1, inferred: 0 },
      files: [],
      files_total: 2,
      files_without_measured_tests: ["src/b.ts"],
      unknown_files: ["src/b.ts"],
      coverage: {
        status: "partial",
        reason: "coverage_map_matches_only_part_of_change",
        map_present: true,
        pair_count: 1,
        test_count: 1,
        source_file_count: 1,
        changed_files_total: 2,
        changed_files_with_measured_tests: 1,
        changed_files_without_measured_tests: 1,
        ingested_at: null,
        source_format: "istanbul",
        freshness: {
          status: "unknown",
          reason: "coverage_or_index_commit_unavailable",
          ingested_commit: null,
          indexed_commit: null,
        },
      },
      inference: {
        status: "available",
        reason: null,
        changed_files_total: 2,
        changed_files_with_candidates: 0,
        candidates_before_dedup: 0,
      },
      analysis: {
        status: "partial",
        stale: false,
        partial: true,
        degraded: false,
        basis_categories: ["measured"],
      },
    },
    structural_impact_score: 5.6,
    structural_impact_band: "moderate",
    structural_impact_scale: {
      field: "structural_impact_score",
      kind: "heuristic_structural_score",
      unit: "normalized_points",
      range: { minimum: 0, maximum: 10 },
      measures: "indexed structural exposure",
      deterministic: true,
      calibration: { status: "uncalibrated", source: null },
      authoritative_for_change_review: false,
      runtime_breakage_probability: false,
    },
    overall_risk_score: 5.6,
    overall_risk_score_compatibility: {
      deprecated: true,
      replacement: "structural_impact_score",
      equivalent_value: true,
      historical_meaning: "uncalibrated 0-10 structural blast-radius heuristic",
    },
  },
  reviewers: [
    {
      name: "Ada Lovelace",
      email: "ada@example.com",
      score: 0.9,
      recent_commits: 4,
      owned_paths: ["src/a.ts"],
      co_change_paths: [],
      reasons: ["owns src/a.ts"],
    },
  ],
  gitUnavailable: false,
};

function makeHost(
  riskRange: WebviewHost["api"]["riskRange"],
  changeImpact: WebviewHost["api"]["changeImpact"] = vi
    .fn()
    .mockResolvedValue(CLEAN_IMPACT),
  overrides: Partial<WebviewHost> = {},
): WebviewHost {
  return {
    api: {
      riskRange,
      changeImpact,
      getSettings: vi
        .fn()
        .mockResolvedValue({ "changeIntel.cochangeMinScore": 4 }),
    } as unknown as WebviewHost["api"],
    onInit: () => () => {},
    onRefresh: () => () => {},
    onUpdateDone: () => () => {},
    onThemeChanged: () => () => {},
    ready: () => {},
    openFile: () => {},
    copyText: () => {},
    openExternal: () => {},
    openView: () => {},
    focusHome: () => {},
    openNativeSettings: () => {},
    updateIndex: () => {},
    setTheme: () => {},
    ...overrides,
  };
}

describe("risk App", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("leads with the repo-relative ranking and keeps the raw score secondary", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    render(
      <App
        host={makeHost(riskRange)}
        repo={REPO}
        params={{}}
        refreshToken={0}
      />,
    );

    expect(await screen.findByText("Elevated")).toBeTruthy();
    expect(screen.getByText("88th percentile")).toBeTruthy();
    expect(screen.getByText("7.4/10")).toBeTruthy();
    expect(screen.getByText("+1.80")).toBeTruthy();
    expect(screen.getByText("−0.50")).toBeTruthy();
    // The change-shape table renders labelled features and skips null ones.
    expect(screen.getByText("Change entropy")).toBeTruthy();
    expect(screen.getByText("120")).toBeTruthy();
  });

  it("refetches when Run again is clicked", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    render(
      <App
        host={makeHost(riskRange)}
        repo={REPO}
        params={{}}
        refreshToken={0}
      />,
    );

    await screen.findByText("Elevated");
    expect(riskRange).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /run again/i }));
    await waitFor(() => expect(riskRange).toHaveBeenCalledTimes(2));
  });

  it("renders change-impact sections from the blast payload", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    const changeImpact = vi.fn().mockResolvedValue(IMPACT);
    render(
      <App
        host={makeHost(riskRange, changeImpact)}
        repo={REPO}
        params={{}}
        refreshToken={0}
      />,
    );

    expect(await screen.findByText("Downstream of your changes")).toBeTruthy();
    // Paths render as split dir/name spans, so match on the file name.
    expect(screen.getByText("consumer.ts")).toBeTruthy();
    expect(screen.getByText("Usually changes together")).toBeTruthy();
    expect(screen.getAllByText("a.test.ts").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Test impact")).toBeTruthy();
    expect(screen.getByText("measured · repo")).toBeTruthy();
    expect(screen.getByText("no test evidence")).toBeTruthy();
    expect(screen.getByText("Ada Lovelace")).toBeTruthy();
  });

  it("copies suggested reviewers to the clipboard", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    const changeImpact = vi.fn().mockResolvedValue(IMPACT);
    const copyText = vi.fn();
    render(
      <App
        host={makeHost(riskRange, changeImpact, { copyText })}
        repo={REPO}
        params={{}}
        refreshToken={0}
      />,
    );

    await screen.findByText("Ada Lovelace");
    fireEvent.click(screen.getByRole("button", { name: /copy/i }));
    expect(copyText).toHaveBeenCalledWith(
      "Suggested reviewers: Ada Lovelace <ada@example.com>",
      expect.any(String),
    );
  });

  it("ranks the riskiest files with markers and opens a file on click", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    const changeImpact = vi.fn().mockResolvedValue(IMPACT);
    const openFile = vi.fn();
    render(
      <App
        host={makeHost(riskRange, changeImpact, { openFile })}
        repo={REPO}
        params={{}}
        refreshToken={0}
      />,
    );

    expect(
      await screen.findByText("Highest structural weight in this change"),
    ).toBeTruthy();
    expect(screen.getByText("hotspot")).toBeTruthy();
    const bars = screen.getAllByTitle(
      "Structural weight relative to the strongest file in this change",
    );
    expect(bars).toHaveLength(2);
    // Sorted riskiest first: b.ts (0.82) gets the full bar, a.ts a partial one.
    expect((bars[0]!.firstElementChild as HTMLElement).style.width).toBe(
      "100%",
    );

    // b.ts also appears in the test-gap card; the riskiest row renders first.
    fireEvent.click(screen.getAllByTitle("Open src/b.ts")[0]!);
    expect(openFile).toHaveBeenCalledWith("src/b.ts");
  });

  it("renders verdict chips that scroll to their sections", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    const changeImpact = vi.fn().mockResolvedValue(IMPACT);
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    render(
      <App
        host={makeHost(riskRange, changeImpact)}
        repo={REPO}
        params={{}}
        refreshToken={0}
      />,
    );

    expect(
      await screen.findByText("may affect 1 downstream file"),
    ).toBeTruthy();
    expect(screen.getByText("1 co-change partner untouched")).toBeTruthy();
    expect(screen.getByText("test analysis partial")).toBeTruthy();
    expect(screen.getByText("1 test recommendation")).toBeTruthy();

    fireEvent.click(screen.getByText("may affect 1 downstream file"));
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("renders no verdict chips on a clean tree", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    render(
      <App
        host={makeHost(riskRange)}
        repo={REPO}
        params={{}}
        refreshToken={0}
      />,
    );

    await screen.findByText("Elevated");
    expect(screen.queryByText(/downstream file/)).toBeNull();
    expect(screen.queryByText(/untouched/)).toBeNull();
    expect(screen.queryByText(/test recommendation/)).toBeNull();
  });

  it("shows a clean-tree empty state when nothing changed", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    render(
      <App
        host={makeHost(riskRange)}
        repo={REPO}
        params={{}}
        refreshToken={0}
      />,
    );

    expect(await screen.findByText("No pending changes")).toBeTruthy();
  });
});
