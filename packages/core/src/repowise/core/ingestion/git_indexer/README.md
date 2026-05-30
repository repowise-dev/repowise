# `git_indexer`

Mines git history into per-file metadata (ownership, churn, hotspots,
co-change, change entropy, prior-defect history) for the ingestion pipeline.

## Purpose

Turn a repository's commit history into the signals the wiki and health
layers consume:

- commit counts and recency windows (90d / 30d), file age, `is_stable`;
- contributor count, bus factor, primary/recent ownership (blame-based on the
  FULL tier, commit-author fallback otherwise);
- significant commits + commit-category ratios (feature / fix / refactor / …);
- a temporal hotspot score (exponentially decayed churn) and repo-wide
  percentiles (`churn_percentile`, `is_hotspot`);
- co-change partners and Hassan **change entropy** (History Complexity Metric);
- per-function blame (modification counts, line age) for function-level health;
- **prior-defect history** — bug-fix commits touching each file in the trailing
  window, the most cost-effective defect predictor.

## Window anchoring (`REPOWISE_GIT_WINDOW_ANCHOR`)

Every recency window (90d/30d, age, temporal decay, co-change/entropy decay, the
prior-defect window) is measured relative to a single reference "now":

- **default (env unset)** → wall-clock `now()`, so "churned lately" means
  relative to today — the live-product meaning.
- **`REPOWISE_GIT_WINDOW_ANCHOR=head`** → the repo's most-recent commit
  timestamp. This makes indexing **deterministic** (re-indexing the same commit
  yields identical windows) and **correct for historical checkouts**: scoring a
  worktree detached at an old commit then measures the window *before that
  commit*, not an empty window in its future. Used by the defect benchmark to
  score repos at a past T0 without leaking future history into the signals.

`REPOWISE_SKIP_EDITOR_SETUP=1` is the companion guard that lets `init` index a
transient worktree without touching the developer's global editor config.

## Public API

Imported via `repowise.core.ingestion.git_indexer`:

- `GitIndexer` — the orchestrator (`index_repo`, `index_changed_files`).
- `GitIndexTier` — `FULL` (default) or `ESSENTIAL` indexing depth.
- `GitIndexSummary` — counts + duration returned by `index_repo`.
- `backfill_full_tier(indexer, repo_id, *, job_store=None)` — promote an
  ESSENTIAL index to FULL as a resumable phase.
- Module-level building blocks (unit-tested directly): `index_file`,
  `compute_co_changes`, `compute_co_changes_and_entropy`, `compute_percentiles`,
  `compute_prior_defects`, `get_blame_ownership`, `is_significant_commit`,
  `detect_original_path`, `is_fix_commit`.

## Tiers

| Tier | Per-file history | `git blame` ownership + per-fn blame | Co-change + entropy |
|------|------------------|--------------------------------------|---------------------|
| `FULL` (default) | yes | yes | yes |
| `ESSENTIAL` | yes | no (commit-author fallback) | no (deferred) |

`ESSENTIAL` is for the fast orchestrator path (`--mode fast`) on very large
repos: it skips the two O(repo) signals so a first index lands quickly, then
`backfill_full_tier` fills in blame + co-change/entropy later. The prior-defect
pass runs on both tiers (it is bounded to the window, not O(repo)).

## Performance: one repo-wide commit index

The naive design spawns one `git log -- <file>` per tracked file — O(files)
subprocesses. Instead, when rename-tracking is off (the default), `indexer.py`
builds a single repo-wide commit index once (`git_commit_index.load_commit_index`,
`git log -<commit_limit> --numstat`) and each per-file worker reads its slice
from that dict. This caps depth at `_DEFAULT_COMMIT_LIMIT` (the newest N commits)
to keep `init` inside its time/memory budget.

**Caveat the prior-defect pass works around:** that cap bounds the index by
*commit count*, so on a hyperactive repo a hot file's slice under-represents a
wide window. `compute_prior_defects` therefore does NOT read the index — it runs
its own date-bounded `git log prior_sha..HEAD --name-only` pass, which reaches
the full window at a fraction of the cost of lifting the global cap (it scales
with window activity, not total repo age). Windowed-but-decayed signals
(90d counts, entropy) tolerate the cap because old commits contribute ~nothing.

## Per-commit rows + just-in-time change-risk

The same repo-wide walk also yields **per-commit** rows (the `git_commits`
table), not just per-file aggregates. `load_commit_index` accepts an optional
`commit_sink`: when supplied it appends each commit (sha, author, ts, subject,
and its full `(path, added, deleted)` footprint across *all* files) during the
existing walk — no extra `git` pass. `commit_rows.build_commit_rows` then turns
those into rows carrying Kamei change features (lines/files/dirs/subsystems
touched, churn entropy) and a calibrated **change-risk** score/level from
`analysis.change_risk` — runtime-safe (pure arithmetic, no LLM, no blame).
Author *experience* (the one change-risk feature that costs a subprocess in the
live `repowise risk` path) is reconstructed **in memory** here: walking commits
oldest→newest, each author's prior-commit count is the running tally. Empty in
rename-tracking mode (which uses the per-file walk, not the batched index).

## Internal layout

| Module | Contents |
|--------|----------|
| `tiers.py` | `GitIndexTier` enum + `includes_blame` / `includes_co_change` |
| `_constants.py` | commit-depth defaults, decay half-lives, the bug-fix keyword classifier (`is_fix_commit`, mirrors the benchmark) + `PRIOR_DEFECT_WINDOW_DAYS`, skip heuristics, GitPython noise patch |
| `records.py` | `_CommitRec`, `GitIndexSummary` (carries `commit_rows`), the `git log` record format + rename/skip path helpers |
| `commit_rows.py` | `build_commit_rows` — per-commit Kamei features + change-risk from the walk's sunk commits (pure, in-memory author experience) |
| `file_history.py` | `index_file` — per-file parse + base metrics (blame gated by tier) |
| `enrich.py` | blame ownership, commit significance, rename detection, percentiles |
| `function_blame.py` | per-line blame index → per-function modification counts + line age (FULL tier; feeds `function_hotspot` / `code_age_volatility`) |
| `co_change.py` | `compute_co_changes` / `compute_co_changes_and_entropy` — repo-wide decay-weighted pair walk + Hassan HCM |
| `prior_defects.py` | `compute_prior_defects` — windowed bug-fix count per file (leakage-aware, benchmark-mirroring) |
| `indexer.py` | `GitIndexer` class wiring the above; merges co-change/entropy/prior-defects into per-file metadata; back-compat instance shims |
| `backfill.py` | `backfill_full_tier` resumable ESSENTIAL→FULL promotion |

(`git_commit_index.py` lives one level up in `ingestion/` — the repo-wide
commit-bucketing pass `indexer.py` consumes; see Performance above.)

## Extension points

A downstream indexer can call the module functions directly with its own
executor, or subclass nothing and just pass a different `GitIndexTier`.
`backfill_full_tier` accepts any `JobStore` for checkpoint/resume.

## Tests

`tests/unit/test_git_indexer.py` (per-function behaviour, incl. the fix-commit
classifier + prior-defect counting), `tests/unit/ingestion/test_git_indexer_tiers.py`
(tier gating + backfill), `tests/integration/test_git_intelligence_integration.py`
(end-to-end).
