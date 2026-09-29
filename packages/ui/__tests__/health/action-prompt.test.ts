import { describe, expect, it } from "vitest";
import type { NextAction } from "@repowise-dev/types/actions";

import { buildActionPrompt } from "../../src/health/ai-prompts/action-prompt";

const rollup: NextAction = {
  id: "act_1",
  rule: "fresh_regressions",
  tier: "act_now",
  horizons: ["week"],
  severity: "critical",
  title: "Clean up what this week's commits left: 3 serious findings in 2 files",
  impact: "2 commits in the last 7 days added or worsened them.",
  why: [{ label: "critical", value: "1", basis: "measured" }],
  target: { kind: "repo", path: "", symbol: null },
  surface: "commits",
  effort: "M",
  confidence: "high",
  done_when: "The findings close on the next update.",
  command: null,
  marker: null,
  evidence_ids: ["abc1234def"],
  evidence_total: 3,
  includes: ["src/a.py", "src/b.py"],
  fingerprint: "fp",
  details: [
    {
      path: "src/a.py",
      line: 40,
      symbol: "src/a.py::build",
      marker: "complex_method",
      severity: "critical",
      reason: "build has cyclomatic complexity 31",
      ref: "abc1234def5678",
    },
  ],
  details_total: 3,
  commands: [
    {
      purpose: "Every open finding in these files",
      mcp: 'get_health(targets=["src/a.py", "src/b.py"], include=["biomarkers"])',
      cli: "repowise health --file src/a.py",
    },
  ],
};

describe("buildActionPrompt", () => {
  it("carries the findings themselves, not just file names", () => {
    const text = buildActionPrompt({ action: rollup });
    expect(text).toContain("`src/a.py:40`: `build`");
    expect(text).toContain("build has cyclomatic complexity 31");
    expect(text).toContain("commit abc1234def");
    expect(text).toContain("## Evidence (1 of 3, worst first)");
    expect(text).toContain("…and 2 more.");
  });

  it("gives an MCP agent the MCP call and anyone else the CLI line", () => {
    expect(buildActionPrompt({ action: rollup, flavor: "claude-code-mcp" })).toContain(
      'get_health(targets=["src/a.py", "src/b.py"], include=["biomarkers"])',
    );
    const generic = buildActionPrompt({ action: rollup, flavor: "generic" });
    expect(generic).toContain("repowise health --file src/a.py");
    expect(generic).not.toContain("working on one file");
  });
});
