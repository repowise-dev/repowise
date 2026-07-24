---
name: change-review
description: Use when reviewing a set of changes before they merge in a Repowise-indexed repository, a PR, a branch diff, or the working-tree changes you just made. Activates for "review this PR", "is this safe to merge", "what is the blast radius of these changes", or "did I miss anything".
---

# Change Review With Repowise

When a diff is on the table, Repowise turns "what files changed" into "what does this change put at risk", fusing git history (churn, ownership, co-change) with graph topology (dependents, impact surface), test gaps, security signals, and the architectural decisions that govern the touched code. Use two complementary signals.

- **`get_change_risk(revspec=…)` (MCP)** scores the *whole change as one unit* (a
  commit or a `base..head` range) from its diff shape: a single 0-10 defect-risk
  score with drivers (lines added/deleted, files, directories, subsystems,
  change entropy, author familiarity). No LLM, no network. Prefer this in-MCP
  tool; it takes a revspec and diffs server-side, so you never shell out. Lead
  with `risk_percentile` (this change ranked against sampled recent commits),
  summarized by `review_priority` and `classification`; `score` / `level` are
  the corpus-calibrated fallback. This is the pre-merge gate: "how risky is this
  change overall?" The `repowise risk <revspec>` CLI is the identical scorer for
  when you are already in a terminal.
- **`get_risk(changed_files=…)` (MCP)** works *per file* and returns the
  `directive` block, the specific things to check inside the diff.

## Score The Whole Change First

```
get_change_risk(revspec="main..HEAD")   # HEAD, a commit SHA, or base..head
```

Read `risk_percentile` and the top drivers: a high score from large diffusion
(many dirs/subsystems) or low author familiarity tells you where to look
hardest. `extensions=[".py", ".ts"]` counts only certain file types;
`exclude_patterns=["tests/"]` omits paths. A `warning` field means the revspec
or filters matched no files, so an all-zero score there is not a clean bill of
health. The equivalent from a terminal is `repowise risk <revspec>` (add
`--ext .py,.ts` or `--format json`).

## Then Drill Into The Directive Block

Call `get_risk(targets=<changed files>, changed_files=<same files>)` in PR mode. The `directive` block is a few short lists, read it first:

- `will_break`: files or symbols that depend on what changed but are not in the diff. Likely breakages, check each one.
- `missing_cochanges`: files that historically change together with the changed files but were left untouched. Often a forgotten update.
- `missing_tests`: changed code with a test gap. Flag for new or updated tests.
- `tests_to_run`: the coverage-backed complement of `missing_tests`, the tests the per-test map proves execute the changed files (pytest-runnable ids). Empty means unknown (no coverage map ingested), never "no tests exist".

`pr_blast_radius` holds the fuller dossier behind those lists (including the
per-changed-file `guarding_tests` breakdown behind `tests_to_run`).

For the line-precise version from a terminal, `repowise impacted-tests <revspec>`
maps each changed line to the tests whose recorded coverage touches it, then
prints the ids (`--format list | xargs pytest` runs exactly them).

## Then Go Deeper Where It Matters

1. Call `get_why(query="<file>")` for any non-trivial changed file so the change does not silently contradict a recorded decision. Surface `conflicts_with` and `supersedes` hits.
2. Call `get_health(targets=<changed files>, include=["biomarkers"])` to catch new complexity, deep nesting, or duplication the diff introduced.
3. Use the `get_risk` ownership and co-change signals to suggest who should review.

## Getting The Diff

- A GitHub PR: `gh pr diff <number>` (or `gh pr view <number> --json files`).
- A branch: `git diff --name-only main...HEAD`.
- Working tree: `git status --porcelain`.
- CLI shortcut for a range: `repowise risk main..HEAD` scores a branch/PR range
  for defect risk directly.

## Write The Review Around Evidence

Lead with a risk level and the `directive` findings, each tied to a concrete file. Distinguish "will break" (a dependent outside the diff) from "worth a look" (a co-change or health regression). Do not pad with findings the tools did not support.

## Error Handling

If `get_risk` or `get_change_risk` errors or returns nothing, the MCP server may be down or the repo unindexed. Say so, review from the raw diff, and suggest running `repowise init` if the repo is not indexed.
