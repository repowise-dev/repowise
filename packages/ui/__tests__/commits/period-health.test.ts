import { describe, expect, it } from "vitest";
import { readPeriodHealth } from "../../src/commits/period-health";
import { readCommitHealth, verdictSentence, worseSentence } from "../../src/commits/commit-health-copy";
import { winsReads } from "../../src/commits/recent-wins";
import { buildChangeAiPrompt } from "../../src/health/ai-prompt-builder";
import type { PeriodHealthSummaryResponse, WinsResponse } from "@repowise-dev/types/history";
import type { CommitHealth } from "@repowise-dev/types/git";

const summary = (o: Partial<PeriodHealthSummaryResponse>): PeriodHealthSummaryResponse => ({
  total: 0,
  improved: 0,
  mixed: 0,
  worsened: 0,
  neutral: 0,
  unscanned: 0,
  findings_resolved: 0,
  findings_introduced: 0,
  findings_worsened: 0,
  net_findings: 0,
  ...o,
});

describe("readPeriodHealth", () => {
  it("counts mixed on both sides, matching the better/worse filters", () => {
    const r = readPeriodHealth(summary({ total: 20, improved: 3, mixed: 2, worsened: 4, neutral: 6, unscanned: 5 }));
    expect(r).toMatchObject({ better: 5, worse: 6, both: 2, neutral: 6, analysed: 15, tone: "mixed" });
  });

  it("names a window with only improvements as good", () => {
    expect(readPeriodHealth(summary({ total: 4, improved: 2, neutral: 2 })).tone).toBe("good");
  });

  it("separates nothing-changed from nothing-analysed", () => {
    expect(readPeriodHealth(summary({ total: 3, neutral: 3 })).tone).toBe("quiet");
    expect(readPeriodHealth(summary({ total: 3, unscanned: 3 })).tone).toBe("empty");
  });
});

const health = (o: Partial<CommitHealth>): CommitHealth => ({
  status: "available",
  introduced_count: 0,
  worsened_count: 0,
  resolved_count: 0,
  files_analyzed: 1,
  files_skipped: 0,
  findings: [],
  ...o,
});

describe("commit health copy", () => {
  it("never reads a never-analysed commit as clean", () => {
    const r = readCommitHealth(null, null);
    expect(r.kind).toBeNull();
    expect(verdictSentence(r)).toContain("not been analysed");
  });

  it("drops counts for a stale delta", () => {
    const r = readCommitHealth(
      { status: "available", kind: "worsened", introduced: 4, worsened: 0, resolved: 0, stale: true },
      null,
    );
    expect(r.stale).toBe(true);
    expect(verdictSentence(r)).not.toMatch(/\d/);
  });

  it("names improvements before regressions", () => {
    const r = readCommitHealth(null, health({ introduced_count: 2, resolved_count: 3 }));
    expect(verdictSentence(r)).toBe("Made 3 findings better and 2 findings worse.");
  });

  it("says when the worse list is capped", () => {
    const h = health({ introduced_count: 5, findings: [] });
    expect(worseSentence(h)).toContain("Showing the 0 most severe of 5");
  });
});

describe("winsReads", () => {
  const routes = { commit: (s: string) => `/c/${s}`, file: (p: string) => `/f/${p}`, refactorings: "/r" };
  const wins: WinsResponse = {
    since: "2026-09-18T00:00:00Z",
    findings_resolved: {
      count: 12,
      commits: 4,
      top_commits: [{ sha: "abc", subject: "split x", author_name: "a", resolved: 7 }],
    },
    files_improved: { count: 0, top: [] },
    refactorings_landed: { count: 1, top: [{ file_path: "a.py", target_symbol: "run", refactoring_type: "extract_method", before_ccn: 40, after_ccn: 8 }] },
  };

  it("keeps sections with wins, links their top item, and marks them good", () => {
    const reads = winsReads(wins, routes);
    expect(reads.map((r) => r.key)).toEqual(["findings", "refactors"]);
    expect(reads[0]).toMatchObject({ value: "12", href: "/c/abc", good: true });
    expect(reads[1]?.why).toBe("run: complexity 40 to 8.");
  });
});

describe("buildChangeAiPrompt for one commit", () => {
  it("names the commit, not a merged change", () => {
    const text = buildChangeAiPrompt({
      change: {
        unit: "commit",
        label: "abc1234",
        title: "feat: x",
        head_sha: "a".repeat(40),
        commits: ["a".repeat(40)],
        files: ["src/a.py"],
        findings: [{ change_kind: "introduced", severity: "high", path: "src/a.py", line_start: 3, reason: "grew" }],
        resolved: 0,
        complete: true,
        attention: [],
      },
    });
    expect(text).toContain("## What this commit made worse");
    expect(text).toContain("Commit: abc1234");
    expect(text).not.toContain("merged change");
  });
});
