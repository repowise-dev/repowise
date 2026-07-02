import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { App } from "./App";
import type { WebviewHost } from "../../runtime/rpc";
import type { RepoInit } from "../../../../src/shared/webviewMessages";
import type { RiskRangeReport } from "../../../../src/shared/webviewMessages";

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

function makeHost(riskRange: WebviewHost["api"]["riskRange"]): WebviewHost {
  return {
    api: { riskRange } as WebviewHost["api"],
    onInit: () => () => {},
    onRefresh: () => () => {},
    onUpdateDone: () => () => {},
    onThemeChanged: () => () => {},
    ready: () => {},
    openFile: () => {},
    copyText: () => {},
    openExternal: () => {},
    openView: () => {},
    updateIndex: () => {},
    setTheme: () => {},
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
});
