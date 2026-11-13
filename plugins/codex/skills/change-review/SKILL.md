---
name: change-review
description: Use when reviewing a set of changes before they merge in a Repowise-indexed repository, a PR, a branch diff, or the working-tree changes you just made. Activates for "review this PR", "is this safe to merge", "what is the blast radius of these changes", or "did I miss anything".
---

# Change Review with Repowise

When a diff is on the table, Repowise turns "what files changed" into "what does
this change put at risk" — fusing git history (churn, ownership, co-change) with
graph topology (dependents, impact surface), test gaps, security signals, and the
architectural decisions that govern the touched code.

Two complementary risk signals, use both:

- **`repowise risk <revspec>`** scores the *whole change as one unit* (a commit
  or a `base..head` range). No LLM, no network. It reports `risk_percentile`
  (this change ranked against sampled recent commits by diff size and spread,
  not a bug predictor) and the drivers behind it. Omit the revspec to score
  uncommitted work. **`repowise health check <revspec>`** says what the change
  actually made worse: the health findings it wrote, and the ones it worsened.
  Together they are the pre-merge gate: "how risky is this change overall?"
- **`repowise risk --target <path> --changed-file <path>`** works *per file*
  and returns the `directive` block, the specific things to check inside the
  diff.

## Score the whole change first

```
repowise risk main..HEAD            # HEAD, a commit SHA, or base..head
repowise health check main...HEAD   # the findings the change introduced
```

Read the health check first: a finding the change wrote fails it, one it made
worse warns. Then `risk_percentile` and the top drivers: a high score from large
diffusion (many dirs/subsystems) or low author familiarity tells you where to
look hardest. `--ext .py,.ts` counts only certain file types; `-x tests/` omits
paths; `--format json` gives machine output. A revspec or filter that matched no
files scores zero, and that is not a clean bill of health.

## Then drill into the per-file directive

Run the per-file report in **PR mode** by passing each changed file twice:

```
repowise risk --target <file> --changed-file <file> --format json   # repeat both per changed file
```

The response carries its own `directive` block, per file rather than per
change — read it first, it's a few short lists:

- **`may_break`** — files/symbols that import their way to what changed but are
  *not* in the diff. Reachability, not a diff: it says these depend on the
  changed files, not that the thing they use changed. Check each one.
- **`missing_cochanges`** — files that historically change together with the
  changed files but were left untouched. Often a forgotten update.
- **`missing_tests`** — changed code with a test gap. Flag for new/updated tests.
- **`tests_to_run`**: the tests the change needs, from the same selection
  `repowise impacted-tests` makes, with **`tests_to_run_why`** giving each one's
  reason (the changed file and its evidence, or "runs with every subset").
  Check **`tests_run_all`** first: when it is true no smaller set can be
  vouched for, **`tests_run_all_reasons`** says why, and the list is only what
  to run first; recommend the whole suite. When it is false the list is the
  set to run. **`tests_to_run_basis`** is `measured` when stored per-test
  coverage decided a changed file and `inferred` when only the dependency graph
  or a rule did; say which. **`tests_status`** appears only when no selection
  could be made (`timeout`, `config_invalid`, `unknown`, ...), and then every
  test must run.

The same JSON carries `pr_blast_radius`, the fuller dossier behind the
directive's lists.

For the line-precise version from a terminal, `repowise impacted-tests <revspec>`
maps each changed line to the tests whose recorded coverage touches it, then
prints the ids (`--format list | xargs pytest` runs exactly them). It is honest
about gaps: a changed file with no coverage rows is a labelled filename guess,
and a brand-new file is "unknown, run the full suite", never "no tests needed".

## Then go deeper where it matters

1. **Why does this code exist?** For any non-trivial changed file, call
   `get_why(query="<file>")` — don't let a change silently contradict a recorded
   architectural decision. Surface `conflicts_with` / `supersedes` hits.
2. **Did the change make health worse?** `repowise health --score <changed file>
   --against main` (repeat `--score` per file) scores each file now and at the
   base — call out new complexity, deep nesting, or duplication the diff
   introduced.
3. **Who should review?** The ownership + co-change signals in
   `repowise risk --target` suggest the people with the most context on the
   touched code.

## Getting the diff

- A GitHub PR: `gh pr diff <number>` (or `gh pr view <number> --json files`).
- A branch: `git diff --name-only main...HEAD`.
- Working tree: `git status --porcelain`.
- CLI shortcut for a range: `repowise risk main..HEAD` scores a branch/PR range
  for defect risk directly.

## Write the review around evidence

Lead with a risk level and the `directive` findings, each tied to a concrete
file. Distinguish **"may break"** (a dependent outside the diff) from **"worth a
look"** (a co-change or health regression). Don't pad with findings the tools
didn't support.

## Error handling

If `repowise risk --target` errors or returns nothing, the repo may be
unindexed — say so and review from the raw diff, noting that Repowise context was
unavailable. Suggest `/prompts:repowise-init` if the repo isn't indexed.
