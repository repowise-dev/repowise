import { describe, expect, it } from "vitest";
import type { CommitDetail } from "@repowise-dev/types/git";
import { commitFixPromptInput } from "./commit-prompt";

const finding = {
  change_kind: "introduced",
  dimension: "maintainability",
  biomarker_type: "large_method",
  severity: "high",
  path: "src/a.py",
  line_start: 4,
  attribution_basis: "added_lines",
  reason: "run grew",
};

function commit(o: Partial<CommitDetail>): CommitDetail {
  return {
    sha: "a".repeat(40),
    short_sha: "aaaaaaaa",
    subject: "feat: x",
    drivers: [],
    files: [{ path: "src/a.py" }],
    health: {
      status: "available",
      introduced_count: 1,
      worsened_count: 0,
      resolved_count: 2,
      files_analyzed: 1,
      files_skipped: 0,
      findings: [finding],
    },
    ...o,
  } as CommitDetail;
}

describe("commitFixPromptInput", () => {
  it("builds the commit-unit fix prompt from the stored findings", () => {
    expect(commitFixPromptInput(commit({}))).toMatchObject({
      unit: "commit",
      label: "aaaaaaaa",
      files: ["src/a.py"],
      resolved: 2,
      complete: true,
    });
  });

  it("offers no fix prompt without current findings", () => {
    expect(commitFixPromptInput(commit({ health: null }))).toBeNull();
    expect(
      commitFixPromptInput(
        commit({
          health_delta: { status: "available", kind: "worsened", introduced: 1, worsened: 0, resolved: 0, stale: true },
        }),
      ),
    ).toBeNull();
  });
});
