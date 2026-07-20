---
name: change-review
description: >
  Use when reviewing a set of changes before they merge — a PR, a branch diff, or the working-tree
  changes you just made — in a Repowise-indexed codebase (.repowise/ directory exists). Activates
  for "review this PR", "is this safe to merge", "what's the blast radius of these changes", "did I
  miss anything", or "what else should change with this".
user-invocable: false
---

# Change Review with Repowise

When a diff is on the table, Repowise turns "what files changed" into "what does
this change put at risk" — fusing git history (churn, ownership, co-change) with
graph topology (dependents, impact surface), test gaps, security signals, and the
architectural decisions that govern the touched code.

Two complementary risk signals, use both:

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

## Score the whole change first

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

## Then drill into the directive block

Call `get_risk` in **PR mode** by passing the changed files:

```
get_risk(targets=<changed files>, changed_files=<same changed files>)
```

The response carries a `directive` block — read it first, it's a few short lists:

- **`will_break`** — files/symbols that depend on what changed but are *not* in
  the diff. These are the likely breakages. Check each one.
- **`missing_cochanges`** — files that historically change together with the
  changed files but were left untouched. Often a forgotten update.
- **`missing_tests`** — changed code with a test gap. Flag for new/updated tests.
- **`tests_to_run`** — the positive complement of `missing_tests`: the tests the
  per-test coverage map proves execute the changed files (pytest-runnable ids).
  Recommend running these to validate the change. Empty until a coverage map is
  ingested (`repowise coverage add`); empty is "unknown", never "no tests exist".

`pr_blast_radius` holds the fuller dossier behind those lists (including the
per-changed-file `guarding_tests` breakdown behind `tests_to_run`).

For the line-precise version from a terminal, `repowise impacted-tests <revspec>`
maps each changed line to the tests whose recorded coverage touches it, then
prints the ids (`--format list | xargs pytest` runs exactly them). It is honest
about gaps: a changed file with no coverage rows is a labelled filename guess,
and a brand-new file is "unknown, run the full suite", never "no tests needed".

## Then go deeper where it matters

1. **Why does this code exist?** For any non-trivial changed file, call
   `get_why(query="<file>")` — don't let a change silently contradict a recorded
   architectural decision. Surface `conflicts_with` / `supersedes` hits.
2. **Did the change make health worse?** `get_health(targets=<changed files>,
   include=["biomarkers"])` — call out new complexity, deep nesting, or
   duplication the diff introduced.
3. **Who should review?** `get_risk` ownership + co-change signals suggest the
   people with the most context on the touched code.

## Getting the diff

- A GitHub PR: `gh pr diff <number>` (or `gh pr view <number> --json files`).
- A branch: `git diff --name-only main...HEAD`.
- Working tree: `git status --porcelain`.
- CLI shortcut for a range: `repowise risk main..HEAD` scores a branch/PR range
  for defect risk directly.

## Write the review around evidence

Lead with a risk level and the `directive` findings, each tied to a concrete
file. Distinguish **"will break"** (a dependent outside the diff) from **"worth a
look"** (a co-change or health regression). Don't pad with findings the tools
didn't support.

## Error handling

If `get_risk` errors or returns nothing, the MCP server may be down or the repo
unindexed — say so and review from the raw diff, noting that Repowise context was
unavailable. Suggest `/repowise:init` if the repo isn't indexed.
