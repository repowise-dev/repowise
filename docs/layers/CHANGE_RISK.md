# Change risk (`repowise risk`)

`repowise risk` reports on a **change**: a commit, a `base..head` range, or
your uncommitted work. It tells you two things. Where the change sits in the
repository's own distribution of recent commits, by size and spread. And
whether the files it touches have broken before. It scores changes; for scores
on files, use `repowise health`.

It needs no LLM key, no network and no index: it runs on `git` plus constants
learned offline. A typical run is one `git log` walk and a sample of recent
commits. Branch overlap and the independent-changes report use the index when
there is one.

Lead with `risk_percentile` and `classification`. They are the
population-relative signal for review. `fix_history` is separate evidence about
where the change lands. The 0-10 score is a supporting measure of diff size and
spread, not a probability (see [Accuracy and limits](#accuracy-and-limits)).

## Quick start

```bash
repowise risk                    # uncommitted work, else HEAD
repowise risk HEAD               # the last commit
repowise risk main..HEAD         # a branch or PR range, as one change
repowise risk main..HEAD --ext .py              # count only .py files
repowise risk main..HEAD -x 'tests/' -x '*.spec.ts'
repowise risk --format json
repowise overlap                 # other branches editing the same files
```

From an agent:

```python
get_change_risk()                          # the working tree or HEAD
get_change_risk(revspec="main..HEAD")
get_risk(changed_files=["src/api/routes.ts", "src/middleware/cors.ts"])  # PR mode
```

`get_change_risk` leads with what the change newly made worse: it compares the
health of both revisions and keeps diff shape as supporting context. In the
dashboard, the Commits page leads its table with review priority.

### What gets scored

With no revspec the subject is your uncommitted work (staged, unstaged and
untracked) when the tree is dirty, otherwise `HEAD`. The payload sets
`working_tree: true` when it took that path. Naming a revspec, `HEAD`
included, always means committed refs. A merge commit is scored for the diff it
brought onto its first parent, so a merged PR reads as its own content.

## Reading the results

Read the result in this order:

| Field | What it is | Act on it |
|---|---|---|
| `review_priority` / `classification` | Tercile of the percentile: `low` / `moderate` / `high`, labelled Below typical / Typical / Elevated | Yes: the review signal |
| `risk_percentile` | Where this change's diff shape ranks among the repo's recent commits (0-100) | Yes |
| `fix_history` | Prior bug fixes on the touched files, ranked against recent commits | Yes: where to look |
| `score` | 0-10 diff size and spread, on a single-commit scale | Supporting only |
| `fallback_band` | `low` / `moderate` / `high` from the score | Only present when there was nothing to rank against |
| `independent_changes` | Groups of changed files that nothing links | Consider splitting |

### Fix history: where the change lands

`fix_history` answers a question diff shape cannot: have these files broken
before?

```
These files have broken before · 82nd percentile of this repo's recent commits
File                                 Lines   Prior fixes
core/pipeline/persist.py                40          21.6
cli/commands/update_cmd/command.py       6          19.3
```

- **Prior fixes** counts bug-fix commits that touched the file before the
  change, weighted by age against the change's own date: a fix one year earlier
  counts a half, two years a quarter. The same commit scores the same on every
  re-run.
- **`density`** is the churn-weighted mean of those per-file numbers. It is a
  ratio, so it does not grow with diff size: one line in a file fixed twenty
  times outranks a thousand lines in files never fixed.
- **`percentile`** ranks that density against the same measure over the repo's
  recent commits. It is `null` when the change touches no fix history, or when
  fewer than eight sampled commits are available.

History is read from before the change: a commit is never ranked against fixes
it caused. For a range, the record is read at the fork point. Up to 20,000
commits are walked, once per repository state.

A bug fix is a commit whose subject matches `fix`, `bug`, `patch`, `resolves`,
`closes #N` or `fixes #N`, and does not contain `docs`, `typo`, `bump`, `deps`,
`chore`, `lint`, `format` or `style`. The same rule feeds the health layer and
[bug-fix history](BUG_HISTORY.md).

### The diff-shape score

The score uses Kamei-style change metrics: lines added and deleted (`la`,
`ld`), files touched (`nf`), directories and top-level subsystems touched
(`nd`, `ns`), the entropy of churn across files, and the author's prior commit
count (`exp`). `exp` is `null` when the author cannot be resolved and then
contributes nothing. The model is a plain logistic over standardized features,
so each driver's push is exact.

Drivers are stated relative to the model's baseline commit (10.5 lines added,
1.7 files), not to this repo. A small change can read "more lines added than
baseline" and still rank below typical in a repo of large commits. `nf`, `nd`
and `ns` enter the model but are not reported as drivers: their small negative
weights are collinearity with size, not evidence that touching more files is
safer.

The absolute score is calibrated on single commits. A squash-merged PR or a
`base..head` range is several commits' worth of diff and skews high on it; the
percentile does not have that problem. `score_unit` states this in every
payload.

### What the sample is anchored to

`repowise risk` samples recent commits live (`--baseline`, default 200). The
web UI uses the indexed commit history.

| Subject | Sample runs back from |
|---|---|
| A commit in `HEAD`'s history | `HEAD` |
| A commit on another branch | that commit |
| A `base..head` range | the merge-base of the two sides |
| Uncommitted work | `HEAD` |

A change never ranks against itself. One walk is reused for every change scored
against the same anchor and filters in a process, so a long-running MCP server
pays for it once.

### PR mode (`get_risk` with `changed_files`)

`get_risk(changed_files=[...])`, or `repowise risk --target FILE
--changed-file FILE`, answers a different question: what does this set of
files reach, and what is missing from it. The response opens with a
`directive` block:

| Field | Meaning |
|---|---|
| `may_break` | Production files in structural reverse-import reach of the diff. Candidates for review, not proven breakage |
| `missing_cochanges` | Files that historically change with these but are not in the diff |
| `tests_to_run`, `tests_to_run_basis` | Which tests to run, `measured` (coverage) or `inferred` (graph reach). With coverage the list is the measured one; `include=["tests"]` adds the typed `test_recommendations` rows, including reached tests the measured list lacks (`reason: structural_reach`) |
| `missing_tests` | Changed files with a test gap; present only when coverage can back it |
| `coverage` | `{status, reason}` when there is no per-test coverage map |
| `tests_to_update` | Up to three test files the change will probably need edited, with why: `name_pair`, `imports` or `co_change`; empty when none qualify |
| `reach` | `localized`, `moderate` or `broad`: the band of the structural heuristic below; `null` when no score was computed |
| `next_calls` | What to call next |
| `summary` | One sentence over all of the above |

In a workspace the directive also carries cross-repo fields, dropped when no workspace is loaded:
`will_break_consumers` (repos that structurally depend on this one; structural
reach, not a runtime claim), `missing_cross_repo_cochanges`,
`breaking_changes` (incompatible provider contract changes since the last
index), `conformance_violations` and `dependency_cycles`. See
[Cross-Repo Blast Radius](../scale/WORKSPACES.md#cross-repo-blast-radius) and
[Breaking-Change Guard](../scale/WORKSPACES.md#breaking-change-guard).

The directive also names `recommended_reviewers`. With `include=["blast"]`,
PR mode adds `pr_blast_radius`, whose `structural_impact_score` is a deterministic, uncalibrated
0-10 heuristic over PageRank, churn and transitive dependents, banded
`localized` (below 4), `moderate` (4 to below 7) and `broad` (7 and up). It is
not a probability and does not decide review. The MCP reply carries only its
band, as `directive.reach`; `include=["scales"]` adds the score and its scale.
The REST blast-radius response and the CLI keep the score with its exact
deprecated alias `overall_risk_score`.

## Independent changes

This answers whether the files in front of you are one change or several. It
is a structural property of the diff, with no score.

A changed file is grouped only when it has a file node in the index, is not a
test file, and is in a language whose resolver can emit an import edge. Docs,
config, data and test files are never grouped and never bridge two groups.
Three kinds of link join eligible files:

- **Index edges** between two different changed files: imports, calls, type
  references, framework and dynamic edges, reads.
- **Stored co-change pairs**: files history moves together.
- **Shared commits**, for a `base..head` range only: files one commit touched
  belong together.

Files left out are listed as `ungrouped_files` and never counted as an
independent change. `bridging_files` names the files that alone hold a group of
three or more together. The `basis` field states in words exactly what was
checked; a missing edge is a claim about this index, not about the code.

The report is silent unless there are at least two groups. `repowise risk`
prints the groups under the driver table; `--format json` and
`get_change_risk` carry them as `independent_changes` (absent without an
index).

## Branch overlap

Branch overlap answers one question: which other open branches edit the files
this change edits. It is git first, so it works in a fresh clone with no index.

```bash
repowise overlap                          # HEAD against the trunk
repowise overlap --base main --branch feature/x --limit 100
```

It scans local and remote branches, newest committer date first, up to
`--limit` (default 50). It drops the base, the change's own ref, refs pointing
at either tip, and branches stacked on or under this one. For each branch it
takes `git diff --name-only base...branch` (what that branch did since it
forked) and intersects it with this change's files. Noise paths (workflows,
lockfiles, generated, vendored and localization files) and dependency
manifests (`package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml` and
similar) are removed from both sides first. A branch with no shared file after
filtering produces no entry.

Every row states its basis, one of two:

- **`same file`**: both branches change that path.
- **`co-change pair, N of M commits`**: shown only under a branch that already
  has a direct hit. A file the other branch edits that history pairs with one
  of yours at least half the time. At most three per branch. Needs an index.

Each entry carries `ahead`, `behind` and the date of its last commit. Branches
sort by shared files, then recency, then name. There is no score.

It compares paths, not hunks: a shared file is a reason to talk to the other
author, not a predicted merge conflict. `repowise overlap` prints one line when
nothing overlaps, naming how many branches were scanned of how many exist.
`get_change_risk` omits the `branch_overlap` block when nothing overlaps or the
branch scan times out.

## Tuning and suppressing

- **`--exclude` / `-x PATTERN`** (repeatable, gitignore-style) omits files. The
  same filter applies to the sampled commits, so the comparison stays like for
  like.
- **`.riskignore`** at the repository root holds project-wide, risk-only
  patterns. They apply automatically and combine with `-x`.
- **`--ext`** counts only the listed suffixes.
- **`--baseline N`** sets the sample size; `0` turns ranking off and leaves only
  `fallback_band`.

## In CI

`--fail-above-percentile P` turns the command into a gate on `risk_percentile`.
The absolute 0-10 value never decides.

```bash
repowise risk --fail-above-percentile 95 --format github
repowise risk origin/main...HEAD --fail-above-percentile 95 --format markdown
```

With the flag, or with `--format markdown` or `github`, and no revspec, the
subject is the CI change: the pull request's target branch (from CI variables,
else the remote's default branch) `...HEAD`. A CI checkout of a PR is a merge
commit, so `HEAD` alone would be the wrong change.

Exit codes are the shared CI ones: `0` at or below the percentile, `1` above,
`2` when the gate cannot evaluate. A change with no percentile cannot be gated:
`--baseline 0` turns ranking off, and a shallow clone with fewer than 8 recent
commits has nothing to rank against, so fetch full history. A revspec git
cannot read also exits `2`. At percentile P roughly (100 - P)% of changes fail
by construction; a failure asks for a split or a second reviewer, not a code
fix.

`json` adds a `gate` object with the unrounded percentile it compared.
`markdown` leads with the verdict, then the range, sample, size and fix-history
rank. `github` writes an `::error::` (or `::notice::`) and puts the markdown in
the job summary.

The GitHub Action runs it as the `risk` gate (input
`risk-fail-above-percentile`; empty reports the rank without gating), and the
GitLab template as the `repowise-risk` job when
`REPOWISE_RISK_FAIL_ABOVE_PERCENTILE` is set. See [CI](../start/CI.md).

## Accuracy and limits

- **The score is a diff-size statistic.** Lines added carries a weight 7.6x the
  next feature, and `la` alone reproduces the full score within 0.12-0.16
  points on every repo tried.
- **Against churn-only it barely moves.** Leave-one-repo-out AUC on 4,102
  commits across 7 repositories (AG-SZZ labels): 0.772 for the model, 0.766 for
  churn alone (95% CI of the gap includes zero).
- **The score cannot rank danger.** On 47 hand-picked within-repo pairs of a
  small dangerous change and a large boring one, the score picks the dangerous
  one in 0 of 47. Fix density picks it in 46 of 47. The set is a falsification
  test, built so size alone scores 0; it is not an accuracy estimate.
- **`fix_history` has no AUC of its own.** It scores near chance against SZZ
  labels, which are size-biased; quoting that number would mislead.
- **The fix classifier is keyword based.** It misses conventions outside its
  list (Django's `Fixed #N` is the notable one) and drops genuine fixes whose
  subject says `docs` or `style`. Where a project's convention falls outside
  it, `fix_history` reads low.
- **Absolute scores skew high on ranges and squash merges**: the scale is one
  commit. Use the percentile.
- **Overlap compares paths, not hunks.**

Method, the refit that was measured and rejected, and every public scale are in
[architecture/change-risk.md](../architecture/change-risk.md).

## Where it shows up

- **CLI**: `repowise risk`, `repowise overlap`.
- **MCP**: `get_change_risk` for a diff, `get_risk` for files and PR mode.
- **Dashboard**: the Commits page, led by review priority.
- **REST**: `GET /api/repos/{repo_id}/risk/range`.
- **CI**: the GitHub Action `risk` gate and the GitLab `repowise-risk` job.

## Reference

### `repowise risk [REVSPEC]`

| Flag | Default | Meaning |
|---|---|---|
| `--path DIR` | `.` | Repository path |
| `--ext SUFFIXES` | all | Comma-separated suffixes to count (`.py` or `.ts,.tsx`) |
| `--baseline N` | 200 | Recent commits to rank against; `0` disables ranking |
| `--exclude`, `-x PATTERN` | none | Gitignore-style exclusion, repeatable |
| `--target`, `-t PATH` | none | Report history for these files instead of a change (needs an index) |
| `--changed-file PATH` | none | With `--target`: PR mode, leads with the directive |
| `--fail-above-percentile P` | none | CI gate on `risk_percentile` |
| `--format` | `table` | `table`, `json`, `markdown`, `github` |
| `--full` | off | Emit the complete payload as JSON |

### `repowise overlap`

| Flag | Default | Meaning |
|---|---|---|
| `--base REF` | trunk | Base ref |
| `--branch REF` | `HEAD` | The change to compare |
| `--path DIR` | cwd | Repository path |
| `--limit N` | 50 | Branches to diff, newest first |
| `--format` | `table` | `table` or `json` |

Full CLI entries: [`repowise risk`](../reference/CLI_REFERENCE.md#repowise-risk-revspec),
[`repowise overlap`](../reference/CLI_REFERENCE.md#repowise-overlap). MCP
schemas: [`get_change_risk`](../agent/MCP_TOOLS.md#get_change_risk),
[`get_risk`](../agent/MCP_TOOLS.md#get_risk).

## See also

- [architecture/change-risk.md](../architecture/change-risk.md): calibration,
  the scale inventory, and PR structural-impact internals.
- [BUG_HISTORY.md](BUG_HISTORY.md): per-file and per-symbol fix counts.
- [CODE_HEALTH.md](CODE_HEALTH.md): file-level scores.
- [Workspaces](../scale/WORKSPACES.md): cross-repo blast radius and contracts.
