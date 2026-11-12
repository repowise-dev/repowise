import { describe, expect, it } from "vitest";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { buildBlastRadiusPrompt, type BlastPromptInput } from "../../src/health/ai-prompts/blast-radius-prompt.js";
import { blastPromptInput } from "../../src/blast-radius/blast-prompt-input.js";
import { readReach } from "../../src/blast-radius/blast-map-model.js";
import { makeReview } from "./review-fixture.js";

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
    expect(p).toContain("120 source files in 4 modules");
    expect(p).toContain("Verdict:");
    // One test listed of 14: the rest are counted.
    expect(p).toContain("13 more of 14 in all");
    expect(p.split("\n").length).toBeLessThan(60);
  });

  it("states the verdict, counts tests apart, gives the commands, and names the changed files' owners", () => {
    const p = buildBlastRadiusPrompt({
      input: {
        ...INPUT,
        status: "Look first: 1 changed file changes often or has poor health.",
        reach: { ...INPUT.reach, tests: 30 },
        testCommands: ["pytest tests/test_a.py", "npm --prefix packages/ui test -- b.test.tsx"],
        owners: [{ name: "Ann", email: "ann@example.com", files: 1, of: 2 }],
      },
    });
    expect(p).toContain("Screen verdict: Look first: 1 changed file changes often or has poor health.");
    expect(p).toContain("30 test files also import it (tests to run, not reach)");
    expect(p).toContain(["pytest tests/test_a.py", "npm --prefix packages/ui test -- b.test.tsx"].join("\n"));
    expect(p).toContain("## Owners of the changed files");
    expect(p).toContain("Ann <ann@example.com>, owns 1 of the 2 changed files");
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

describe("buildBlastRadiusPrompt with a health check", () => {
  it("adds the check's verdict and findings once one has run", () => {
    const p = buildBlastRadiusPrompt({
      input: {
        ...INPUT,
        healthCheck: {
          revspec: "origin/main...feat/x",
          verdict: "fail",
          findings: [{ group: "introduced", severity: "high", path: "packages/core/src/a.py", line: 12, message: "Deep nesting (introduced, high)" }],
        },
      },
    });
    expect(p).toContain("## Health check: fail (`repowise health check origin/main...feat/x`)");
    expect(p).toContain("introduced high: `packages/core/src/a.py:12` Deep nesting");
    expect(buildBlastRadiusPrompt({ input: INPUT })).not.toContain("## Health check");
  });
});

describe("buildBlastRadiusPrompt for a pull request", () => {
  it("names the PR and diffs base against head", () => {
    const p = buildBlastRadiusPrompt({
      input: {
        ...INPUT,
        change: { kind: "pull_request", label: "#123", number: 123, title: "Fix the cache", base: "main", head_sha: "b".repeat(40) },
      },
    });
    expect(p).toContain("Pull request #123: Fix the cache");
    expect(p).toContain(`git diff main...${"b".repeat(40)}`);
  });
});

describe("the review prompt for a scored change", () => {
  const result: BlastRadiusResponse = {
    structural_impact_score: 1,
    structural_impact_band: "localized",
    structural_impact_scale: {} as BlastRadiusResponse["structural_impact_scale"],
    overall_risk_score: 1,
    overall_risk_score_compatibility: {} as BlastRadiusResponse["overall_risk_score_compatibility"],
    direct_risks: [],
    transitive_affected: [],
    cochange_warnings: [],
    recommended_reviewers: [],
    test_gaps: [],
    test_commands: ["pytest tests/test_blast.py"],
  };
  const status = { tone: "calm", word: "Small change", reason: "1 file changed.", next: "" } as const;
  const prompt = (review: ReturnType<typeof makeReview>) =>
    buildBlastRadiusPrompt({
      input: blastPromptInput(result, readReach(result, ["src/a.py"]), status, ["src/a.py"], undefined, undefined, undefined, review),
    });

  it("carries the rank and the review's tests, not the blast radius's", () => {
    const p = prompt(makeReview());
    expect(p).toContain("Change risk: Riskier than 82% of this repo's recent commits, by size and spread. Mostly 400 lines added and 6 files touched.");
    expect(p).toContain("`tests/test_a.py`");
    expect(p).toContain("pytest tests/test_a.py tests/test_b.py");
    expect(p).not.toContain("tests/test_blast.py");
  });

  it("says to run the whole suite, with why, when the selection could not narrow it", () => {
    const p = prompt(makeReview({ tests: { status: "run_all", run_all: true, reasons: ["conftest.py changed"], tests_to_run: [] }, commands: [] }));
    expect(p).toContain("Run the whole test suite: nothing narrower is known to cover this change (conftest.py changed).");
  });
});
