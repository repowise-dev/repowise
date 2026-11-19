/**
 * The commit sheet's agent prompts. A commit that made health worse gets the
 * shared fix prompt (the change prompt, worded for one commit); any other gets
 * the review prompt. Pure, so the choice is tested once.
 */
import type { CommitDetail } from "@repowise-dev/types/git";
import type { ChangePromptInput, CommitPromptInput } from "@repowise-dev/ui/health/ai-prompt-builder";
import { readCommitHealth } from "@repowise-dev/ui/commits/commit-health-copy";

/** The fix prompt's input, or null when there is nothing current to fix:
 *  no findings stored, or only an earlier analysis's (direction only). */
export function commitFixPromptInput(c: CommitDetail): ChangePromptInput | null {
  const read = readCommitHealth(c.health_delta, c.health);
  const findings = c.health?.findings ?? [];
  if (read.stale || findings.length === 0) return null;
  return {
    unit: "commit",
    label: c.short_sha,
    title: c.subject,
    head_sha: c.sha,
    commits: [c.sha],
    files: (c.files ?? []).map((f) => f.path),
    findings,
    resolved: c.health?.resolved_count ?? 0,
    complete: c.health?.status !== "partial",
    attention: [],
  };
}

/** The review prompt's input: the diff-shape context. */
export function commitReviewPromptInput(c: CommitDetail): CommitPromptInput {
  return {
    sha: c.sha,
    subject: c.subject,
    review_priority: c.review_priority,
    risk_percentile: c.risk_percentile,
    change_risk_score: c.change_risk_score,
    is_fix: c.is_fix,
    files_changed: c.files_changed,
    lines_added: c.lines_added,
    lines_deleted: c.lines_deleted,
    entropy: c.entropy,
    top_drivers: c.drivers.filter((d) => d.contribution > 0).map((d) => d.label),
    author_name: c.author_name,
  };
}
