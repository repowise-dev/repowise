# `git_indexer`

Mines git history into per-file metadata (ownership, churn, hotspots,
co-change partners) for the ingestion pipeline.

## Purpose

Turn a repository's commit history into the signals the wiki and health
layers consume: commit counts and recency windows, contributor/bus-factor,
significant commits, a temporal hotspot score, and co-change relationships.

## Public API

Imported via `repowise.core.ingestion.git_indexer`:

- `GitIndexer` — the orchestrator (`index_repo`, `index_changed_files`).
- `GitIndexTier` — `FULL` (default) or `ESSENTIAL` indexing depth.
- `GitIndexSummary` — counts + duration returned by `index_repo`.
- `backfill_full_tier(indexer, repo_id, *, job_store=None)` — promote an
  ESSENTIAL index to FULL as a resumable phase.
- Module-level building blocks (unit-tested directly): `index_file`,
  `compute_co_changes`, `compute_percentiles`, `get_blame_ownership`,
  `is_significant_commit`, `detect_original_path`.

## Tiers

| Tier | Per-file history | `git blame` ownership | Co-change walk |
|------|------------------|-----------------------|----------------|
| `FULL` (default) | yes | yes | yes |
| `ESSENTIAL` | yes | no (commit-author fallback) | no |

`ESSENTIAL` is for the fast orchestrator path (`--mode fast`) on very large
repos: it skips the two O(repo) signals so a first index lands quickly, then
`backfill_full_tier` fills in blame + co-change later.

## Internal layout

| Module | Contents |
|--------|----------|
| `tiers.py` | `GitIndexTier` enum + `includes_blame` / `includes_co_change` |
| `_constants.py` | commit-depth defaults, decay half-lives, skip heuristics, GitPython noise patch |
| `records.py` | `_CommitRec`, `GitIndexSummary`, rename/skip path helpers |
| `file_history.py` | `index_file` — per-file parse + base metrics (blame gated by tier) |
| `enrich.py` | blame ownership, commit significance, rename detection, percentiles |
| `co_change.py` | `compute_co_changes` — repo-wide decay-weighted pair walk |
| `indexer.py` | `GitIndexer` class wiring the above; back-compat instance shims |
| `backfill.py` | `backfill_full_tier` resumable ESSENTIAL→FULL promotion |

## Extension points

A downstream indexer can call the module functions directly with its own
executor, or subclass nothing and just pass a different `GitIndexTier`.
`backfill_full_tier` accepts any `JobStore` for checkpoint/resume.

## Tests

`tests/unit/test_git_indexer.py` (per-function behaviour),
`tests/unit/ingestion/test_git_indexer_tiers.py` (tier gating + backfill),
`tests/integration/test_git_intelligence_integration.py` (end-to-end).
