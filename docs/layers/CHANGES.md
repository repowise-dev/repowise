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
| CLI | `repowise changes list [--category C] [--shape merge\|squash\|commit] [--grep TEXT]`, `repowise changes show REF [--format table\|json\|in-toto]` |
| REST | `GET /api/repos/{id}/changes` (filters `category`, `shape`, `q`, `needs_attention`, `author`, `area`, `since`, `until`, `delta`, `touches_hotspot`; `evidence=true` adds each row's kept checklist status counts, leaving out the health row since health leads from the commit rollup), `GET /api/repos/{id}/changes/summary`, `GET /api/repos/{id}/changes/{change_id}` |
| Web | Commits page, "By change" view: each change with its checklist's attention and unknown counts and a "Needs attention" filter; a detail page per change |

Every row leads with `health`: its commits' stored health deltas summed (introduced, worsened, resolved, files worsened) and a `kind` of `improved`, `worsened`, `mixed` or `neutral`; `null` means no commit was scanned. The `delta` filter's `improved` and `worsened` each include `mixed`. `/changes/summary` counts a period's changes by kind, improvements first, and the detail adds the worse findings (file, lines, symbol, biomarker) behind the counts. `GET /api/repos/{id}/commits` carries the same `health_delta` per commit and takes the same filters, and `GET /api/repos/{id}/wins?since=&author=` lists findings resolved, files whose score rose and refactorings that landed.

`show` and the detail route accept a change id or any commit sha the change landed.
Full flags: [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-changes).

## Suspects

`repowise suspects --symptom TEXT [--files F] [--trace FILE] [--since D] [--until D]` ranks the changes merged in the 14 days before `--until` against what was seen. Five signals rank the candidates (text match, import distance from the files the symptom names, co-change, recency, stack-trace hits) and are fused by reciprocal rank; each row lists the signals that fired and their evidence. Measured on 43 real regressions in django and node, the change a later revert or fix named was in the top 5 for 67% of issues, against 28% for ranking by shared files, and text match carries most of that ranking; the method is in [bug-history.md](../architecture/bug-history.md#suspects-ranking-measured). The same ranking is at `POST /api/repos/{id}/suspects`, in `get_context(include=["suspects"])` and as the chat tool `find_suspects`.

Most regressions surface later than 14 days after the change, so a change missing from the list is not cleared; widen `--since`. Files are mapped onto those tracked at HEAD, so a file renamed or deleted in the window may not match. The order says where to start reading, not what caused it. To confirm a suspect, let git find the first bad commit with a check that fails on the bug:

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
