import { describe, expect, it } from "vitest";
import { buildBlastRadiusPrompt, type BlastPromptInput } from "../../src/health/ai-prompts/blast-radius-prompt.js";

const INPUT: BlastPromptInput = {
  change: { kind: "branch", label: "feat/x", base: "origin/main", head_sha: "a".repeat(40) },
  changedFiles: ["packages/core/src/a.py", "packages/ui/src/b.tsx"],
  reach: { files: 120, modules: 4, direct: 9, band: "moderate" },
  lookFirst: [{ path: "packages/core/src/a.py", changed: true, signals: ["Needs work 5.4", "Hotspot"] }],
  tests: [{ name: "tests/test_a.py", basis: "measured" }],
  testsTotal: 14,
  testsLimited: false,
  missingPartners: [{ path: "packages/core/src/a_helpers.py", partnerOf: "packages/core/src/a.py" }],
  owners: [{ name: "Ann", email: "ann@example.com", files: 40 }],
};

describe("buildBlastRadiusPrompt", () => {
  it("carries every fact with full paths and the diff command", () => {
    const p = buildBlastRadiusPrompt({ input: INPUT });
    expect(p).toContain("git diff origin/main...feat/x");
    expect(p).toContain("`packages/core/src/a.py` (changed): Needs work 5.4, Hotspot");
    expect(p).toContain("`tests/test_a.py`");
    expect(p).toContain("`packages/core/src/a_helpers.py` (with `packages/core/src/a.py`)");
    expect(p).toContain("Ann <ann@example.com>, 40 files");
    expect(p).toContain("120 files in 4 modules");
    expect(p).toContain("Verdict:");
    // One test listed of 14: the rest are counted.
    expect(p).toContain("13 more of 14 in all");
    expect(p.split("\n").length).toBeLessThan(60);
  });

  it("names each kind of change and how to read it", () => {
    expect(buildBlastRadiusPrompt({ input: { ...INPUT, change: { kind: "working_tree", label: "wip" } } })).toContain(
      "git diff HEAD && git status --porcelain",
    );
    expect(
      buildBlastRadiusPrompt({ input: { ...INPUT, change: { kind: "commit", label: "abc", sha: "b".repeat(40) } } }),
    ).toContain(`git show ${"b".repeat(40)}`);
  });

  it("points the MCP flavor at repowise tools and flags a limited test list", () => {
    const p = buildBlastRadiusPrompt({ input: { ...INPUT, testsLimited: true }, flavor: "claude-code-mcp" });
    expect(p).toContain("get_change_risk");
    expect(p).toContain("get_risk(changed_files=[...])");
    expect(p).toContain("this list is a floor");
  });
});
