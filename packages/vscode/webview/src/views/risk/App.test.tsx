import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
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
    score: 7.4,
    probability: 0.63,
    level: "high",
    risk_percentile: 88,
    review_priority: "high",
    is_fix: false,
    features: { la: 120, ld: 30, nf: 6, nd: 2, ns: 1, entropy: 2.31, exp: null },
    drivers: [
      { feature: "la", value: 120, contribution: 1.8, label: "Lines added" },
      { feature: "exp", value: null, contribution: -0.5, label: "Author experience" },
    ],
  },
};

const REPO: RepoInit = { id: "r1", name: "repo", headCommit: null, defaultBranch: "main" };

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
      { path: "src/a.ts", risk_score: 0.3, temporal_hotspot: 0.1, centrality: 0.8 },
      { path: "src/b.ts", risk_score: 0.82, temporal_hotspot: 0.9, centrality: 0.1 },
    ],
    transitive_affected: [{ path: "src/consumer.ts", depth: 1 }],
    cochange_warnings: [
      { changed: "src/a.ts", missing_partner: "src/a.test.ts", score: 8 },
    ],
    recommended_reviewers: [],
    test_gaps: ["src/b.ts"],
    overall_risk_score: 5.6,
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
      getSettings: vi.fn().mockResolvedValue({ "changeIntel.cochangeMinScore": 4 }),
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

  it("renders the score, level, and drivers from a fixture", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    render(<App host={makeHost(riskRange)} repo={REPO} params={{}} refreshToken={0} />);

    expect(await screen.findByText("7.4")).toBeTruthy();
    expect(screen.getByText("high risk")).toBeTruthy();
    expect(screen.getByText("+1.80")).toBeTruthy();
    expect(screen.getByText("−0.50")).toBeTruthy();
    // The change-shape table renders labelled features and skips null ones.
    expect(screen.getByText("Change entropy")).toBeTruthy();
    expect(screen.getByText("120")).toBeTruthy();
  });

  it("refetches when Run again is clicked", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    render(<App host={makeHost(riskRange)} repo={REPO} params={{}} refreshToken={0} />);

    await screen.findByText("7.4");
    expect(riskRange).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /run again/i }));
    await waitFor(() => expect(riskRange).toHaveBeenCalledTimes(2));
  });

  it("renders change-impact sections from the blast payload", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    const changeImpact = vi.fn().mockResolvedValue(IMPACT);
    render(
      <App host={makeHost(riskRange, changeImpact)} repo={REPO} params={{}} refreshToken={0} />,
    );

    expect(await screen.findByText("Downstream of your changes")).toBeTruthy();
    // Paths render as split dir/name spans, so match on the file name.
    expect(screen.getByText("consumer.ts")).toBeTruthy();
    expect(screen.getByText("Usually changes together")).toBeTruthy();
    expect(screen.getByText("a.test.ts")).toBeTruthy();
    expect(screen.getByText("Changed without a test")).toBeTruthy();
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

    expect(await screen.findByText("Riskiest files in this change")).toBeTruthy();
    expect(screen.getByText("hotspot")).toBeTruthy();
    const bars = screen.getAllByTitle("Risk relative to the riskiest file in this change");
    expect(bars).toHaveLength(2);
    // Sorted riskiest first: b.ts (0.82) gets the full bar, a.ts a partial one.
    expect((bars[0]!.firstElementChild as HTMLElement).style.width).toBe("100%");

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
      <App host={makeHost(riskRange, changeImpact)} repo={REPO} params={{}} refreshToken={0} />,
    );

    expect(await screen.findByText("may affect 1 downstream file")).toBeTruthy();
    expect(screen.getByText("1 co-change partner untouched")).toBeTruthy();
    expect(screen.getByText("1 changed file has no associated test")).toBeTruthy();

    fireEvent.click(screen.getByText("may affect 1 downstream file"));
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("renders no verdict chips on a clean tree", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    render(<App host={makeHost(riskRange)} repo={REPO} params={{}} refreshToken={0} />);

    await screen.findByText("7.4");
    expect(screen.queryByText(/downstream file/)).toBeNull();
    expect(screen.queryByText(/untouched/)).toBeNull();
    expect(screen.queryByText(/associated test/)).toBeNull();
  });

  it("shows a clean-tree empty state when nothing changed", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    render(<App host={makeHost(riskRange)} repo={REPO} params={{}} refreshToken={0} />);

    expect(await screen.findByText("No pending changes")).toBeTruthy();
  });
});
