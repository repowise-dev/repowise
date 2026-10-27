# Change risk (`repowise risk`)

`repowise risk` reports on a **change**: a commit, a `base..head` range, or your
uncommitted work. It tells you where the change sits in the repository's own
distribution of recent commits, by size and spread, and whether the files it touches
have broken before. For scores on files, use `repowise health`.

It needs no LLM key, no network and no index: it runs on `git` plus constants learned
offline. A typical run is one `git log` walk and a sample of recent commits. Branch
overlap and the independent-changes report use the index when there is one.

Lead with `risk_percentile`: the change's rank among recent commits by diff size and
spread, not a bug predictor. `fix_history` is separate evidence about where the change
lands. The 0-10 score is a supporting measure of diff size and spread, not a probability
(see [Accuracy and limits](#accuracy-and-limits)).

## Quick start

```bash
repowise risk                    # uncommitted work, else HEAD
repowise risk HEAD               # the last commit
repowise risk main..HEAD         # a branch or PR range, as one change
repowise risk main..HEAD --ext .py              # count only .py files
repowise risk main..HEAD -x 'tests/' -x '*.spec.ts'
repowise overlap                 # other branches editing the same files
```

From an agent: `get_change_risk()` scores the working tree or HEAD,
`get_change_risk(revspec="main..HEAD")` a range, and
`get_risk(changed_files=["src/api/routes.ts", "src/middleware/cors.ts"])` runs PR mode.
`get_change_risk` leads with what the change newly made worse: it compares the health of
both revisions and keeps diff shape as supporting context. In the dashboard, the Commits
page leads its table with review priority.

### What gets scored

With no revspec the subject is your uncommitted work (staged, unstaged and untracked)
when the tree is dirty, otherwise `HEAD`. The payload sets `working_tree: true` when it
took that path. Naming a revspec, `HEAD` included, always means committed refs. A merge
commit is scored for the diff it brought onto its first parent, so a merged PR reads as
its own content.

## Reading the results

| Field, in reading order | What it is | Act on it |
|---|---|---|
| `risk_percentile` | Where this change's diff size and spread ranks among the repo's recent commits (0-100) | Yes: size, not danger |
| `fix_history` | Prior bug fixes on the touched files | Yes: where to look |
| `review_priority` / `classification` | Tercile of the percentile: `low` / `moderate` / `high`. MCP returns them only with `include=["scales"]` | Same fact as the percentile |
| `score` | 0-10 diff size and spread, on a single-commit scale | Supporting only |
| `fallback_band` | `low` / `moderate` / `high` from the score | Only present when there was nothing to rank against |
| `independent_changes` | Groups of changed files that nothing links | Consider splitting |

### Fix history: where the change lands

`fix_history` answers a question diff shape cannot: have these files broken before?

```
Bug-fix record for HEAD: touches files with prior bug fixes
File                                 Lines   Prior fixes
core/pipeline/persist.py                40          21.6
cli/commands/update_cmd/command.py       6          19.3
```

- Prior fixes counts bug-fix commits that touched the file before the change, weighted
  by age against the change's own date: a fix one year earlier counts a half, two years
  a quarter. The same commit scores the same on every re-run.
- `density` is the churn-weighted mean of those per-file numbers. It is a ratio, so it
  does not grow with diff size: one line in a file fixed twenty times outranks a
  thousand lines in files never fixed.
- `percentile` ranks that density against the same measure over the repo's recent
  commits. It is `null` when the change touches no fix history, or when fewer than eight
  sampled commits are available. It ships in `--format json` and MCP
  `include=["diagnostics"]` only, so one percentile leads each surface.
- Manifests and lockfiles (`package.json`, `package-lock.json`, `pyproject.toml`,
  `go.sum` and the like) are left out of the listed files: a dependency bump edits them,
  which does not make them files that broke. `density` and its `percentile` still count
  them.

History is read from before the change: a commit is never ranked against fixes it
caused. For a range, the record is read at the fork point. Up to 20,000 commits are
walked, once per repository state. A bug fix is a commit whose subject matches `fix`,
`bug`, `patch`, `resolves`, `closes #N` or `fixes #N`, and does not contain `docs`,
`typo`, `bump`, `deps`, `chore`, `lint`, `format` or `style`. The same rule feeds the
health layer and [bug-fix history](BUG_HISTORY.md).

### The diff-shape score

The score is a plain logistic over Kamei-style change metrics: lines added and deleted
(`la`, `ld`), files touched (`nf`), directories and top-level subsystems touched (`nd`,
`ns`), the entropy of churn across files, and the author's prior commit count (`exp`,
`null` and contributing nothing when the author cannot be resolved). Each driver's push
is exact. Drivers are stated relative to the model's baseline commit, not to this repo,
so a small change can read "more lines added than baseline" and still rank below typical
in a repo of large commits. `nf`, `nd` and `ns` are not reported as drivers
([why](../architecture/change-risk.md#the-model)).

The absolute score is calibrated on single commits, so a squash-merged PR or a
`base..head` range skews high on it; the percentile does not. `score_unit` states this
in every payload.

### What the sample is anchored to

`repowise risk` samples recent commits live (`--baseline`, default 200). The web UI uses
the indexed commit history.

| Subject | Sample runs back from |
|---|---|
| A commit | its first parent |
| A `base..head` range | the merge-base of the two sides |
| Uncommitted work | `HEAD` |

A change never ranks against itself or anything after it, so a merge reads the same
rank as `sha` or `sha^1..sha`, and in its `changes show` checklist, which names the
sample size and the commit it ran back from.

### PR mode (`get_risk` with `changed_files`)

`get_risk(changed_files=[...])`, or `repowise risk --target FILE --changed-file FILE`,
answers a different question: what does this set of files reach, and what is missing
from it. The response opens with a `directive` block:

| Field | Meaning |
|---|---|
| `may_break` | Production files in structural reverse-import reach of the diff. Candidates for review, not proven breakage |
| `missing_cochanges` | Files that historically change with these but are not in the diff |
| `tests_to_run`, `tests_to_run_basis` | Which tests to run, from the same selection `repowise impacted-tests` makes; `measured` when coverage decided a changed file, else `inferred`. `tests_run_all` says when every test must run, `tests_to_run_why` why each listed test is in. `include=["tests"]` adds the typed `test_recommendations` rows |
| `missing_tests` | Changed files with a test gap; present only when coverage can back it |
| `coverage` | `{status, reason}` when there is no per-test coverage map |
| `tests_to_update` | Up to three test files the change will probably need edited, with why: `name_pair`, `imports` or `co_change`; empty when none qualify |
| `reach` | `localized`, `moderate` or `broad`: the band of the structural heuristic below; `null` when no score was computed |
| `next_calls` | What to call next |
| `summary` | One sentence over all of the above |

In a workspace the directive also carries cross-repo fields, dropped when no workspace
is loaded: `will_break_consumers` (repos that structurally depend on this one;
structural reach, not a runtime claim), `missing_cross_repo_cochanges`,
`breaking_changes` (incompatible provider contract changes since the last index),
`conformance_violations` and `dependency_cycles`. See
[Cross-Repo Blast Radius](../scale/WORKSPACES.md#cross-repo-blast-radius) and
[Breaking-Change Guard](../scale/WORKSPACES.md#breaking-change-guard).

The directive also names `recommended_reviewers`, and `include=["blast"]` adds
`pr_blast_radius`. The structural impact score is a deterministic, uncalibrated 0-10
heuristic over PageRank, churn and transitive dependents
([formula](../architecture/change-risk.md#pr-structural-impact-get_risk)), banded
`localized` (below 4), `moderate` (4 to below 7) and `broad` (7 and up). It is not a
probability and does not decide review. MCP carries only the band, as `directive.reach`;
`include=["blast", "scales"]` adds the score and its scale. The REST blast-radius
response and the CLI keep the score with its exact deprecated alias
`overall_risk_score`.

## Independent changes

This answers whether the files in front of you are one change or several. It is a
structural property of the diff, with no score. Only indexed source files in a language
whose resolver emits import edges are grouped
([eligibility](../architecture/change-risk.md#independent-changes-grouping)). Three
kinds of link join them:

- **Index edges** between two different changed files: imports, calls, type references,
  framework and dynamic edges, reads.
- **Stored co-change pairs**: files history moves together.
- **Shared commits**, for a `base..head` range only: files one commit touched belong
  together.

Files left out are listed as `ungrouped_files` and never counted as an independent
change. `bridging_files` names the files that alone hold a group of three or more
together. The `basis` field states in words exactly what was checked; a missing edge is
a claim about this index, not about the code. The report is silent unless there are at
least two groups. `repowise risk` prints the groups under the driver table;
`--format json` and `get_change_risk` carry them as `independent_changes` (absent
without an index).

## Branch overlap

Branch overlap answers one question: which other open branches edit the files this
change edits. It is git first, so it works in a fresh clone with no index.

```bash
repowise overlap                          # HEAD against the trunk
repowise overlap --base main --branch feature/x --limit 100
```

It scans local and remote branches, newest committer date first, up to `--limit`
(default 50), and intersects what each did since it forked with this change's files,
ignoring noise paths and dependency manifests on both sides
([scan rules](../architecture/change-risk.md#branch-overlap-scan)). A branch with no
shared file after filtering produces no entry.

Every row states its basis, one of two:

- **`same file`**: both branches change that path.
- **`co-change pair, N of M commits`**: shown only under a branch that already has a
  direct hit. A file the other branch edits that history pairs with one of yours at
  least half the time. At most three per branch. Needs an index.

Each entry carries `ahead`, `behind` and the date of its last commit. Branches sort by
shared files, then recency, then name. There is no score. It compares paths, not hunks:
a shared file is a reason to talk to the other author, not a predicted merge conflict.
`repowise overlap` prints one line when nothing overlaps, naming how many branches were
scanned of how many exist. `get_change_risk` omits the `branch_overlap` block when
nothing overlaps or the branch scan times out.

## Tuning and suppressing

- `--exclude` / `-x PATTERN` (repeatable, gitignore-style) omits files. The same filter
  applies to the sampled commits, so the comparison stays like for like.
- `.riskignore` at the repository root holds project-wide, risk-only patterns. They
  apply automatically and combine with `-x`.
- `--ext` counts only the listed suffixes.
- `--baseline N` sets the sample size; `0` turns ranking off and leaves only
  `fallback_band`.

## In CI

`--fail-above-percentile P` turns the command into a size gate on `risk_percentile`: it
flags changes larger than most, not likelier to break, and the 0-10 value never decides.

```bash
repowise risk --fail-above-percentile 95 --format github
repowise risk origin/main...HEAD --fail-above-percentile 95 --format markdown
```

With the flag, or with `--format markdown` or `github`, and no revspec, the subject is
the CI change: the pull request's target branch (from CI variables, else the remote's
default branch) `...HEAD`. A CI checkout of a PR is a merge commit, so `HEAD` alone
would be the wrong change.

Exit codes are the shared CI ones: `0` at or below the percentile, `1` above, `2` when
the gate cannot evaluate. A change with no percentile cannot be gated: `--baseline 0`
turns ranking off, and a shallow clone with fewer than 8 recent commits has nothing to
rank against, so fetch full history. A revspec git cannot read also exits `2`. At
percentile P roughly (100 - P)% of changes fail by construction; a failure asks for a
split or a second reviewer, not a code fix.

`json` adds a `gate` object with the unrounded percentile it compared. `markdown` leads
with the verdict, then the range, sample, size and fix-history rank. `github` writes an
`::error::` (or `::notice::`) and puts the markdown in the job summary. The GitHub
Action runs it as the `risk` gate (input `risk-fail-above-percentile`; empty reports the
rank without gating), and the GitLab template as the `repowise-risk` job when
`REPOWISE_RISK_FAIL_ABOVE_PERCENTILE` is set. See [CI](../start/CI.md).

## Accuracy and limits

- The score is a diff-size statistic. Lines added carries a weight 7.6x the next
  feature, and `la` alone reproduces the full score within 0.12-0.16 points on every
  repo tried.
- Against churn alone the score adds little: leave-one-repo-out AUC on 4,102 commits
  across 7 repositories (AG-SZZ labels) is 0.772 for the model and 0.766 for churn
  alone, and the 95% CI of the gap includes zero.
- The score does not rank danger. On 47 within-repo pairs of a small dangerous change
  and a large boring one, built so size alone scores 0, the score picks the dangerous
  one in 0 of 47 and fix density in 46 of 47. This is a falsification test, not an
  accuracy estimate.
- `fix_history` has no AUC of its own: SZZ labels are size-biased, so it scores near
  chance against them.
- The fix classifier is keyword based. It misses conventions outside its list (Django's
  `Fixed #N` is the notable one) and drops genuine fixes whose subject says `docs` or
  `style`; where a project's convention falls outside it, `fix_history` reads low.
- Absolute scores skew high on ranges and squash merges: the scale is one commit. Use
  the percentile.

Method, calibration and every public scale:
[architecture/change-risk.md](../architecture/change-risk.md).

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

Full CLI entries:
[`repowise risk`](../reference/CLI_REFERENCE.md#repowise-risk-revspec),
[`repowise overlap`](../reference/CLI_REFERENCE.md#repowise-overlap). MCP schemas:
[`get_change_risk`](../agent/MCP_TOOLS.md#get_change_risk),
[`get_risk`](../agent/MCP_TOOLS.md#get_risk). REST:
`GET /api/repos/{repo_id}/risk/range`. Related: [BUG_HISTORY.md](BUG_HISTORY.md)
(per-file and per-symbol fix counts), [CODE_HEALTH.md](CODE_HEALTH.md) (file-level
scores) and [Workspaces](../scale/WORKSPACES.md) (cross-repo blast radius and
contracts).
