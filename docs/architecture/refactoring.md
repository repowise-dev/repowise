# Refactoring intelligence: internals

User guide: [docs/layers/REFACTORING.md](../layers/REFACTORING.md). This page is for
contributors. Code lives in `packages/core/src/repowise/core/analysis/health/refactoring/`.

## Detectors

Each detector is one module registered with `@register` in `registry.py`; adding a
type is a new file plus that decorator. `detect_refactorings` runs every enabled
detector per file, isolates failures (a crashing detector yields no suggestion for
that file) and applies the `min_confidence` floor. Detectors run inside the health
pass (`HealthAnalyzer` in `analysis/health/engine.py`) on data already computed.

| Module | Method |
|--------|--------|
| `extract_class.py` | LCOM4 union-find components as candidate classes; god-class shape confirmed with WMC and TCC (Lanza-Marinescu). |
| `extract_method.py` | Intra-procedural dataflow (CFG, def/use, reaching definitions, liveness) over functions already flagged `large_method`, `brain_method` or `complex_method`. Spans must be single-exit and statement-bounded; the behavior-preservation gate suppresses what it cannot prove. |
| `extract_helper.py` | Rabin-Karp clone pairs, clustered transitively; the helper site is the community centroid of the involved files. |
| `move_method.py` | Jaccard distance between a method's entity set and each class; fires only when a foreign class is clearly nearer and draws more of its calls. |
| `break_cycle.py` | Import-graph SCCs (one repo-wide index per pass); greedy minimum feedback arc set picks the cut edges. |
| `split_file.py` | Community detection (Leiden via the shared graph helpers) over a weighted intra-file symbol graph of calls, shared helpers, shared imports and co-change; emitted only above a modularity gate. |
| `performance_fix.py` | Wraps causal performance opportunities that have a supported shared intervention. |

The methods follow published work: Fokaefs-Tsantalis class splitting, Bavota
feature-envy distance, MFAS for cycles, Newman modularity for decomposition.

## Plan shape

`RefactoringSuggestion` in `models.py` is the source of truth for every field
(`plan`, `evidence`, `impact_delta`, `effort_bucket`, `blast_radius`, `confidence`,
`source_biomarker`, `validation`). Text is rendered only at the surfaces.
`impact_delta` credits the share of the source finding the plan removes; for
Extract Method that is `ccn_removed / ccn`, `slice_nloc / nloc`, or the larger of
the two for `brain_method`, capped at 1. `naming.py` returns `null` when no fact
anchors a name. Extract Method's `helper_naming.py` names a helper from a `timed()`
label, a banner comment, or `compute_<out>` for an effect-free span, and drops any
name already taken in scope.

Plan ids come from `identity.py`: a `refac<version>_` prefix over a per-type stable
kernel. Bumping the version re-mints ids; a held id from an older version reports
`stale_model`.

## Ranking

`recommendations.py` owns four components and one score:

```
priority = benefit * (1 + leverage) / (1 + cost + risk)
```

Benefit multiplies, so a plan with no evidence of gain scores zero. Leverage is
weighted health deficit, dependents and entry reach. Blast radius feeds cost and
risk, never benefit. Graph-native and performance plans take benefit from their own
evidence.

## Opportunities

`opportunity.py` composes a file's plans into one opportunity at index time; the
finalizer writes `refactoring_opportunities` and a `refactoring_summaries` row in the
same transaction that reconciles plans. `performance_fix` is excluded (the
performance layer owns those). Plans whose source finding the finding registry
withholds are left out of steps and evidence but stay addressable by id.

Plan rank and validation are worked out once per index, so plan lists and single
plans are served from SQL however many plans a repository has. An index written by
an older version keeps working, at the old speed, until its next update.

Serving (filters, views, scope, paging) lives in `serving.py`; the server reads it
through `services/refactoring_health.py`, which MCP `get_health` and the REST routes
share, so the two surfaces cannot disagree. Scope `fix_first` takes its eligibility
from the Fix-first builder itself.

## REST routes (local server)

```text
GET  /api/repos/{repo_id}/refactoring/opportunities          ?view= &scope= &file_path= &limit=
GET  /api/repos/{repo_id}/refactoring/opportunities/{opportunity_id}
GET  /api/repos/{repo_id}/refactoring/summary
GET  /api/repos/{repo_id}/refactoring/targets                ?refactoring_type= &min_confidence=
GET  /api/repos/{repo_id}/refactoring/targets/page           ?limit= &offset= &refactoring_type=
GET  /api/repos/{repo_id}/refactoring/{suggestion_id}
POST /api/repos/{repo_id}/refactoring/{suggestion_id}/generate-code
GET  /api/repos/{repo_id}/refactoring/settings
PUT  /api/repos/{repo_id}/refactoring/settings
```

## Code generation

`llm/enrich.py` gathers source spans, builds the prompt, calls the provider, runs the
per-type self-check and caches results under the repo's `.repowise` directory by a
hash of plan, source and model. `llm_enrichment_enabled` treats an unset
`refactoring.llm.enabled` as off. The REST endpoint and MCP tool resolve the provider
with `get_chat_provider_instance` (`server/provider_config.py`), the resolver chat uses;
the CLI uses `resolve_provider`, as page generation does. The MCP tool (`tool_refactoring.py`) is registered
`default=False`.
