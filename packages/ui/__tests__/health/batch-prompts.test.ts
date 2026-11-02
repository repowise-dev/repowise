import { describe, it, expect } from "vitest";

import {
  buildCoverageAiPrompt,
  buildCoverageBatchAiPrompt,
  buildHotspotAiPrompt,
  buildHotspotBatchAiPrompt,
  buildSecurityAiPrompt,
  buildSecurityBatchAiPrompt,
} from "../../src/health/ai-prompt-builder";

const finding = (i: number, kind = "weak_hash") => ({
  file_path: `src/f${i}.py`,
  kind,
  severity: "low",
  line_number: i,
});
const row = (i: number) => ({ file_path: `src/f${i}.py`, line_coverage_pct: i, health_score: 5 });

describe("batch prompts", () => {
  it("fall back to the single prompt for one row", () => {
    expect(buildSecurityBatchAiPrompt({ findings: [finding(1)] })).toBe(
      buildSecurityAiPrompt({ finding: finding(1) }),
    );
    expect(buildCoverageBatchAiPrompt({ rows: [row(1)] })).toBe(buildCoverageAiPrompt({ row: row(1) }));
  });

  it("list every row up to the cap and count the rest", () => {
    const security = buildSecurityBatchAiPrompt({
      findings: Array.from({ length: 45 }, (_, i) => finding(i)),
    });
    expect(security).toContain("Findings: **45**");
    expect(security).toContain("`src/f3.py:3` — **weak_hash** (LOW)");
    expect(security).toContain("…and 5 more");

    const coverage = buildCoverageBatchAiPrompt({ rows: Array.from({ length: 3 }, (_, i) => row(i)) });
    expect(coverage).toContain("`src/f2.py` — line coverage 2.0%, health 5.0/10");
    expect(coverage).not.toContain("more in the same list");
  });

  it("keeps the rotation note when any finding in the batch is a secret", () => {
    const prompt = buildSecurityBatchAiPrompt({
      findings: [finding(1), finding(2, "public_env_secret")],
    });
    expect(prompt).toContain("**rotated**");
  });

  it("hotspot batch lists the loaded rows and counts the rest of the filtered total", () => {
    const hot = (i: number) => ({ file_path: `src/h${i}.ts`, commit_count_90d: 9, bus_factor: 1 });
    expect(buildHotspotBatchAiPrompt({ rows: [hot(1)] })).toBe(buildHotspotAiPrompt({ hotspot: hot(1) }));
    const prompt = buildHotspotBatchAiPrompt({ rows: Array.from({ length: 30 }, (_, i) => hot(i)), total: 672 });
    expect(prompt).toContain("`src/h24.ts` — 9 commits in 90 days, one person holds the knowledge");
    expect(prompt).not.toContain("`src/h25.ts`");
    expect(prompt).toContain("…and 647 more");
  });
});
