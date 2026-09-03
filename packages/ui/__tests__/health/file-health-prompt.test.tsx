/**
 * The file-level agent prompt, and the drawer chrome that hands it over.
 *
 * The prompt is the whole drawer written down, so the things worth pinning are
 * the ones a reader of the prompt cannot recover if they go missing: the
 * signals that disagree with the score, the ceilings that make a category
 * understate itself, and the instruction not to trust any of it blindly.
 */
import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import {
  buildFileHealthAiPrompt,
  type FileHealthPromptFinding,
} from "../../src/health/ai-prompt-builder.js";
import {
  HealthFileDrawer,
  type HealthDrawerMetric,
} from "../../src/health/health-file-drawer.js";

const file = {
  file_path: "packages/cli/doctor_cmd.py",
  score: 1.0,
  nloc: 800,
  module: "cli",
  defect_score: 1.0,
  maintainability_score: 4.2,
  performance_score: 8.1,
  has_test_file: false,
  primary_biomarker: "brain_method",
  primary_reason: "One function carries most of the command.",
  total_deduction: 9.0,
};

function finding(p: Partial<FileHealthPromptFinding> = {}): FileHealthPromptFinding {
  return {
    id: "f1",
    biomarker_type: "brain_method",
    severity: "high",
    function_name: "_run_repo_checks",
    line_start: 120,
    line_end: 487,
    health_impact: 3.2,
    reason: "Oversized, deeply-nested function.",
    ...p,
  };
}

describe("buildFileHealthAiPrompt", () => {
  it("leads with the file and all three scored dimensions", () => {
    const out = buildFileHealthAiPrompt({ file, findings: [finding()] });
    expect(out).toContain("packages/cli/doctor_cmd.py");
    expect(out).toContain("Defect risk: **1.0/10**");
    expect(out).toContain("Maintainability: **4.2/10**");
    expect(out).toContain("Performance: **8.1/10**");
  });

  it("tells the agent the findings are leads, not instructions", () => {
    // The whole point of handing over a static report. A prompt that reads as
    // a work order gets a file edited to satisfy an analyzer.
    const out = buildFileHealthAiPrompt({ file, findings: [finding()] });
    expect(out).toMatch(/leads/i);
    expect(out).toMatch(/false positive/i);
    expect(out).toMatch(/which findings share a root cause/i);
  });

  it("says when a category is pinned at its ceiling", () => {
    // A capped category is a floor on how bad it is, not a measurement, and an
    // agent that reads the number as exact will under-scope the work.
    const out = buildFileHealthAiPrompt({
      file,
      findings: [finding()],
      categories: [
        {
          category: "complexity",
          cap: 4,
          applied_deduction: 4,
          capped: true,
          finding_count: 9,
        },
      ],
    });
    expect(out).toContain("at its ceiling");
  });

  it("leaves out findings the reader already triaged away", () => {
    const out = buildFileHealthAiPrompt({
      file,
      findings: [
        finding({ id: "keep", reason: "Still open." }),
        finding({ id: "gone", status: "false_positive", reason: "Dismissed already." }),
        finding({ id: "fixed", status: "resolved", reason: "Fixed already." }),
      ],
    });
    expect(out).toContain("Still open.");
    expect(out).not.toContain("Dismissed already.");
    expect(out).not.toContain("Fixed already.");
  });

  it("carries the change signals, which is what the score cannot say", () => {
    const out = buildFileHealthAiPrompt({
      file,
      findings: [finding()],
      signals: {
        commit_count_90d: 31,
        prior_defect_count: 21,
        bug_magnet: true,
        last_fix_at: "2026-08-26T10:00:00Z",
        in_degree: 14,
      },
    });
    expect(out).toContain("31 commits in the last 90 days");
    expect(out).toContain("bug magnet");
    expect(out).toContain("2026-08-26");
    expect(out).toContain("14 files import this one");
  });

  it("names an untested file as untested", () => {
    const out = buildFileHealthAiPrompt({ file, findings: [finding()] });
    expect(out).toMatch(/No test file/i);
  });

  it("rolls up the long tail instead of printing every finding", () => {
    const many = Array.from({ length: 18 }, (_, i) =>
      finding({ id: `f${i}`, health_impact: 3 - i * 0.1, reason: `Reason ${i}` }),
    );
    const out = buildFileHealthAiPrompt({ file, findings: many });
    expect(out).toMatch(/and 8 more lower-impact findings/);
    // The highest-impact finding is spelled out; the lowest is not.
    expect(out).toContain("Reason 0");
    expect(out).not.toContain("Reason 17");
  });

  it("points the MCP flavor at the tools instead of at a re-read", () => {
    const generic = buildFileHealthAiPrompt({ file, findings: [finding()] });
    const mcp = buildFileHealthAiPrompt({
      file,
      findings: [finding()],
      flavor: "claude-code-mcp",
    });
    expect(mcp).toContain("get_health(['packages/cli/doctor_cmd.py'])");
    expect(mcp).toContain("get_risk(['packages/cli/doctor_cmd.py'])");
    expect(generic).not.toContain("get_health(");
  });
});

describe("the drawer's prompt affordance", () => {
  const m: HealthDrawerMetric = {
    file_path: "packages/cli/doctor_cmd.py",
    score: 1.0,
    max_ccn: 40,
    max_nesting: 6,
    nloc: 800,
    module: "cli",
    has_test_file: false,
  };

  it("opens a prompt built from the file the drawer is showing", () => {
    render(<HealthFileDrawer open onClose={() => {}} metric={m} findings={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /AI prompt/i }));
    expect(screen.getByText(/AI prompt for this file/i)).toBeInTheDocument();
  });

  it("keeps the score breakdown collapsed until asked for", () => {
    // It is the audit trail for a number stated at the top of the drawer, so
    // it should not stand between the reader and the findings.
    render(
      <HealthFileDrawer
        open
        onClose={() => {}}
        metric={m}
        findings={[]}
        breakdown={{
          score: 1,
          total_deduction: 9,
          categories: [
            {
              category: "complexity",
              cap: 4,
              raw_deduction: 6,
              applied_deduction: 4,
              capped: true,
              finding_count: 9,
              findings: [],
            },
          ],
        }}
      />,
    );
    const toggle = screen.getByRole("button", { name: /Why this score/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // The hint says what is inside, so collapsing costs no information.
    expect(toggle.textContent).toContain("−9.00");
    expect(toggle.textContent).toMatch(/1 category/);

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/10\.0 − 9\.00 = 1\.0/)).toBeInTheDocument();
  });
});
