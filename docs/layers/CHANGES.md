# Change Records

A Change Record is one merged change (a pull request, a merge request, or a lone
commit) read back from git: what landed, from which commits, touching which files,
with what provenance. `repowise init` builds the records and every `repowise update`
extends them. No forge API is called.

## How changes are reconstructed

The first-parent chain of `HEAD` is the sequence of things that landed. Each
first-parent commit becomes one change:

| What landed | Shape | Commits in the change | Files |
|---|---|---|---|
| A merge commit | `merge` | The commits its branch brought in (`M^1..M^2`) | The merge against its first parent |
| A squash commit naming a PR or MR | `squash` | The squash commit | That commit's diff |
| Any other commit | `commit` | The commit itself | That commit's diff |

- A merge that names no change of its own (`Merge branch 'main' of ...`, a release
  branch merged back) gives up the named squash commits and PR merges inside it as
  their own changes. It keeps the rest, and is dropped when nothing is left.
- Rebase merges leave no grouping in git, so each rebased commit is its own change.
- A merge's title comes from its body when the subject is boilerplate (`Merge pull
  request #12 from ...`). Its author is the author of the branch's oldest commit.
- The window is the repository's `commit_limit` first-parent commits. An update reads
  only what landed after the newest stored change, and rebuilds after a rebase or
  branch switch.

## Identity

The first that applies:

| Order | Source | Id |
|---|---|---|
| 1 | A PR or MR number the message names, read per forge (GitHub, GitLab, Bitbucket, Azure DevOps) | `pr-123` |
| 2 | A Gerrit `Change-Id:` trailer | `change-I<40 hex>` |
| 3 | The merge commit | `merge-<sha>` |
| 4 | The commit | `commit-<sha>` |

A number that appears twice (a PR cherry-picked onto the main line) gets a suffixed id,
and the change that holds the plain id keeps it across runs.

## Provenance

Each change carries the three-state label from [AI attribution](ATTRIBUTION.md),
rolled up over its commits, with the counts and coverage behind it:

| Label | When |
|---|---|
| `agent` | Any commit in the change is agent-attributed |
| `human_attested` | Every commit is human-attested |
| `unknown` | Anything else |

```json
"provenance": {
  "label": "agent",
  "agent_commits": 2,
  "human_attested_commits": 0,
  "unknown_commits": 3,
  "coverage": 0.4
}
```

With `attribution.enabled: false` the block is `null`.

## Metadata

Reviewers, approvals, labels and linked issues come through the
`ChangeMetadataSource` seam. The default source, `git`, reads only what history
holds: the `Approved-by:` trailers GitLab writes on merge commits, and the issues a
message closes (`Fixes #12`). A field it cannot read is `null`, which means unknown.

A plugin registers another source under `SeamRegistry("change-metadata")`, selected
with `REPOWISE_SEAM_CHANGE_METADATA`. See
[the plugin author guide](../reference/PLUGINS.md).

## Where it shows

| Surface | Use |
|---|---|
| CLI `changes list` | `[--category C] [--shape merge\|squash\|commit] [--grep TEXT] [--needs-attention] [--limit N] [--offset N] [--format table\|json]`. `--limit` defaults to 30 (1 to 1,000). `--needs-attention` keeps changes whose kept checklist flags an item and builds none |
| CLI `changes show REF` | `[--format table\|json\|in-toto] [--evidence/--no-evidence] [--refresh]`. The checklist is on by default; `--refresh` rebuilds it |
| CLI `changes export` | `[--since D] [--until D] [--format json\|csv] [--refresh]`. Every change in the window with its checklist, as JSON Lines or one CSV row per change and item |
| CLI `report changes` | The change management report for a period, see [REPORTS.md](REPORTS.md#the-change-report-period) |
| REST list | `GET /api/repos/{id}/changes`, paged by `limit` (default 50, up to 200) and `offset` |
| REST summary | `GET /api/repos/{id}/changes/summary`: a period's changes counted by health kind |
| REST detail | `GET /api/repos/{id}/changes/{change_id}`; `evidence=true` adds the checklist, `refresh=true` rebuilds it |
| MCP | `get_context(targets=["change:<id>"])`: a change card with its first 25 files |
| MCP | `get_context(include=["changes"])` and `get_risk(include=["changes"])` on a file: its 5 newest changes, with `recent_changes_total` when there are more |
| MCP | `get_overview(include=["changes"])`: changes by category and by week over the 90 days to the newest change, with the agent share when attribution is on |
| MCP | `get_change_risk(include=["evidence"])`: the checklist for a revspec, the kept one when the revspec names a stored change |
| Chat | Charts of changes by category, by week and by provenance, from the `get_overview` block |
| Web | Commits page, "By change" view: each change with its checklist's attention and unknown counts and a "Needs attention" filter; a detail page per change |
| PR bot | Its evidence section (off by default) builds the same checklist, with the same wording, for the pull request |

REST list filters:

- `category`, `shape`, `q` (substring of the title, id or a file path).
- `needs_attention`: only changes whose kept checklist has an attention item.
- `author`, `area` (a directory or file path), `since`, `until`.
- `delta` (`improved`, `worsened`, `mixed`, `neutral`) and `touches_hotspot`.
- `evidence=true` adds each row's kept checklist status counts. It leaves out the health row, since health leads from the commit rollup.

The summary route takes the same filters except `shape`, `q`, `needs_attention` and `evidence`.

Health on every row:

- Every row leads with `health`: its commits' stored health deltas summed (introduced, worsened, resolved, files worsened).
- `kind` is `improved`, `worsened`, `mixed` or `neutral`; `null` means no commit was scanned.
- The `delta` filter's `improved` and `worsened` each include `mixed`.
- `/changes/summary` counts a period's changes by kind, improvements first. The detail adds the worse findings (file, lines, symbol, biomarker) behind the counts.
- `GET /api/repos/{id}/commits` carries the same `health_delta` per commit and takes `author`, `area`, `since`, `until`, `delta` and `touches_hotspot`.
- `GET /api/repos/{id}/wins` lists findings resolved, files whose score rose and refactorings that landed. It takes `since` (default 30 days ago), `until` (default now), `author` and `top` (items per section, 1 to 50, default 10).

`show` and the detail route accept a change id or any commit sha the change landed.
Full flags: [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-changes).

## Suspects

```bash
repowise suspects --symptom TEXT [--files F] [--trace FILE] [--since D] [--until D] [--limit N]
```

- Ranks the changes merged in a window against what was seen. The window defaults to the 14 days before `--until`, and `--until` to now.
- `--limit` defaults to 10 (1 to 200). At most 20,000 characters of symptom and 50 `--files`. Give at least one of `--symptom`, `--files` or `--trace`.
- Five signals rank the candidates: text match, import distance from the files the symptom names, co-change, recency and stack-trace hits. They are fused by reciprocal rank.
- Each row lists the signals that fired and their evidence. The order says where to start reading, not what caused it.

The same ranking is served in four places:

| Surface | Window and rows |
|---|---|
| CLI `repowise suspects` | `--since`, `--until`, `--limit` (default 10) |
| REST `POST /api/repos/{id}/suspects` | `since`, `until`, `limit` (default 10) in the body |
| Chat tool `find_suspects` | `since` and `until` arguments |
| MCP `get_context(include=["suspects"])` | Fixed: 5 rows over the 14 days before the call. The targets' files and path words, or the `symptom` text, are the symptom |

Measured result:

- On the 43 regressions in django and node whose named change merged inside the 14-day window, the change a later revert or fix named was in the top 5 for 67% of issues, against 28% for ranking by shared files.
- Those 43 are 16% of the 265 issues scored: in the rest the named change merged earlier (91% of django issues, 62% of node issues).
- Text match carries most of the ranking. The method is in [bug-history.md](../architecture/bug-history.md#suspects-ranking-measured).

Reading the result:

- Most regressions surface later than 14 days after the change, so a change missing from the list is not cleared.
- To read further back, widen `--since` on the CLI, REST or chat. Over MCP the window is fixed; run `repowise suspects --since <date>` with the same symptom.
- `candidates_capped: true` means the window holds at least `commit_limit` changes, so older changes in it may not be indexed. Re-index with a larger `--commit-limit` to read them.
- Files are mapped onto those tracked at HEAD, so a file renamed or deleted in the window may not match.

To confirm a suspect, let git find the first bad commit with a check that fails on the bug:

```bash
git bisect start <bad-sha> <good-sha>
git bisect run python -m pytest tests/test_invoices.py -x   # exit 0 good, 1-124 bad, 125 skip
git bisect reset
```

## The record and its in-toto export

Every surface serves one versioned JSON shape (`schema_version: 1`): id, identity,
shape, forge reference, title, merge time, head sha, author, commits, files with
added and deleted lines, category, provenance and metadata. Each record keeps up to
1,000 files; `files_changed` carries the full count.

`--format in-toto` wraps the same record as the predicate of an
[in-toto Statement v1](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md)
with predicate type `https://repowise.dev/ChangeRecord/v1`. Its subjects are the
change's commits, keyed by `gitCommit` digest. It claims no SLSA level.

## Accuracy

Measured on the last ~100 merged pull requests of each repository, a change counts
as correct when its file set matches the forge's:

| Repository | Forge | Correct |
|---|---|---|
| repowise | GitHub | 100% |
| express | GitHub | 98.7% |
| gitleaks | GitHub | 97.9% |
| flask | GitHub | 99.0% |
| fd | GitHub | 98.9% |
| gitlab-dangerfiles | GitLab | 100% |

## Limits

- Reviewers and labels stay `null` with the default source; git does not record them.
- A partial clone (`--filter`) gets no Change Records: reading file stats there would fetch blobs.
- Changes before the `commit_limit` window are not read, so suspects only rank changes inside it.
