/**
 * An Extract Method span is found by the text core serves (`plan.anchors`), not
 * by its line numbers, and the plan says to make the edit by hand. The prompts
 * name capabilities, never a harness's tools.
 */
import { describe, expect, it } from "vitest";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import { buildRefactoringPlanPrompt } from "../../src/health/ai-prompt-builder";
import {
  LINE_RULE_TEXT,
  SCRIPT_RULE_TEXT,
} from "../../src/health/ai-prompts/refactoring-plan-steps";

const TOOLS = /\b(Read|Grep|Glob|Edit|Write|TodoWrite|Bash|str_replace)\b/;

function plan(extra: Record<string, unknown> = {}): RefactoringPlan {
  return {
    id: "refac4_walk",
    refactoring_type: "extract_method",
    file_path: "pkg/walker.py",
    target_symbol: "walk_file",
    line_start: 94,
    line_end: 208,
    plan: {
      span: { start: 120, end: 140 },
      params: ["rows"],
      returns: ["total"],
      suggested_name: "_sum_rows",
      new_symbol: { kind: "function", signature_text: "def _sum_rows(rows):" },
      call_site: { replace_span: { start: 120, end: 140 }, new_text: "total = _sum_rows(rows)" },
      ...extra,
    },
    evidence: {},
    impact_delta: 1.2,
    effort_bucket: "M",
    blast_radius: {},
    confidence: "high",
    source_biomarker: "complex_method",
    rank_score: 1,
  };
}

describe("Extract Method anchors", () => {
  it("shows the span's first and last lines exactly, fenced at column 0", () => {
    const prompt = buildRefactoringPlanPrompt({
      plan: plan({
        anchors: { first: "\tfor row in rows:", last: '        out.append(f"`{row}`")\n    return out' },
      }),
    });
    expect(prompt).toContain(
      "Extract the span of `walk_file` that starts and ends with the lines shown (lines 120–140 when planned)",
    );
    expect(prompt).toContain("- **The span starts with:**\n```\n\tfor row in rows:\n```");
    expect(prompt).toContain(
      '- **The span ends with:**\n```\n        out.append(f"`{row}`")\n    return out\n```',
    );
    expect(prompt).toContain("- **Replace the span with:** `total = _sum_rows(rows)`");
    expect(prompt).not.toContain("Replace lines");
    expect(prompt).toContain(`${SCRIPT_RULE_TEXT} ${LINE_RULE_TEXT}`);
    expect(prompt).not.toContain("Text repeats in the file");
  });

  it("says when the anchor text repeats elsewhere in the file", () => {
    const prompt = buildRefactoringPlanPrompt({
      plan: plan({ anchors: { first: "    a = 1", last: "    b = 2", unique_in: "function" } }),
    });
    expect(prompt).toContain(
      "```\n    b = 2\n```\n- Text repeats in the file: edit only inside this function.",
    );
  });

  it("widens the fence past a backtick run in the anchor", () => {
    const prompt = buildRefactoringPlanPrompt({
      plan: plan({ anchors: { first: "    s = ```x```", last: "    return s" } }),
    });
    expect(prompt).toContain("````\n    s = ```x```\n````");
  });

  it("keeps line numbers on a plan stored before anchors", () => {
    const prompt = buildRefactoringPlanPrompt({ plan: plan() });
    expect(prompt).toContain("Extract lines 120–140 of `walk_file`");
    expect(prompt).toContain("- **Replace lines 120–140 with:** `total = _sum_rows(rows)`");
    expect(prompt).toContain(SCRIPT_RULE_TEXT);
    expect(prompt).not.toContain(LINE_RULE_TEXT);
  });

  it("names no harness tool in the steps it renders", () => {
    for (const flavor of ["generic", "claude-code", "claude-code-mcp", "cursor"] as const) {
      const prompt = buildRefactoringPlanPrompt({
        plan: plan({ anchors: { first: "    a = 1", last: "    b = 2" } }),
        flavor,
      });
      const steps = prompt.slice(prompt.indexOf("## The plan"), prompt.indexOf("## Hard constraints"));
      expect(steps).toContain("The span starts with");
      expect(steps).not.toMatch(TOOLS);
      expect(steps).not.toContain("@codebase");
    }
  });
});
