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
    direct_risks: [],
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
    api: { riskRange, changeImpact } as WebviewHost["api"],
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

  it("shows a clean-tree empty state when nothing changed", async () => {
    const riskRange = vi.fn().mockResolvedValue(REPORT);
    render(<App host={makeHost(riskRange)} repo={REPO} params={{}} refreshToken={0} />);

    expect(await screen.findByText("No pending changes")).toBeTruthy();
  });
});
