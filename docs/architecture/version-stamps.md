# Version stamps

A version stamp is a module-level `*_VERSION` constant whose value is written
next to something repowise stores or emits. When the stored value and the
code's value differ, the reader does something about it: it recomputes, it
throws a cache away, or it refuses to misread a file.

This page lists every one of them, grouped by what a bump does. The list is
checked: `tests/unit/health/test_model_version_coupling.py` fails when a
module-level `*_VERSION` constant under `packages/*/src/repowise/` is missing
from the tables below, or when a row names a constant that no longer exists.

Paths are relative to `packages/<package>/src/repowise/`, so `core/...` is in
`packages/core`, `server/...` in `packages/server` and `cli/...` in
`packages/cli`. Values are left out on purpose; the code holds them.

## One bump per release

Bump a stamp at most once per release. Before moving one, check whether it
already moved since the last tag:

```bash
git log <last-tag>..origin/main -S"<CONSTANT> =" --oneline
```

If it did, do not bump it again. Released users upgrade from the last tag's
value, so one bump recomputes every change in the release once, whatever
order the PRs merge in. Parallel PRs in the same release carry the same bumped
value. A second bump adds nothing for users and conflicts with every other open
PR that touches the line.

At release time, compare the recompute stamps against the previous tag and
name the ones that moved in the changelog's version-stamps line.

## Recompute stamps

A bump makes every existing index redo work once, on the user's first
`repowise update` after upgrading. These are the stamps the one-bump rule is
written for.

The first five rows are pinned together by the coupling test. Each of the
other four versions an id that is stored on health rows and rewritten only by
the health re-score, which `HEALTH_ANALYZER_VERSION` alone triggers. Moving
one of them without the analyzer leaves every stored id minted by a kernel
that no longer exists, on a store that is never told to recompute. Every other
row has its own gate and moves alone; the test lists those by name.

| Constant | File | What a bump triggers | When to bump |
|---|---|---|---|
| `HEALTH_ANALYZER_VERSION` | `core/analysis/health/engine.py` | `health_analyzer_changed()` reads true, so the next `update` re-scores health for every file, not only changed ones. Also part of the event snapshot stamp | Any change to what the health re-score writes: a detector, a score, a finding's detail, a biomarker |
| `PERFORMANCE_MODEL_VERSION` | `core/analysis/health/perf/causal.py` | The prefix of every stored performance finding id; an id from another version reads as stale until the re-score rewrites the findings. Coupled with the analyzer | Identity, grouping, actionability or ranking of performance findings changes meaning |
| `REFACTORING_MODEL_VERSION` | `core/analysis/health/refactoring/identity.py` | The prefix of every stored refactoring plan id; rewritten by the re-score. Coupled with the analyzer | The refactoring identity kernel changes |
| `FINDING_ID_VERSION` | `core/analysis/health/finding_identity.py` | Hashed into the `public_id` stored on every health finding, so every id changes; stored rows are rewritten by the re-score. Coupled with the analyzer | The finding identity kernel changes |
| `IDENTITY_VERSION` | `core/analysis/change_health/identity.py` | Hashed into every `chf_` id stored on commit-health rows, which are refilled when their analyzer version no longer matches. Coupled with the analyzer | The change-finding identity inputs change |
| `SECURITY_SCANNER_VERSION` | `core/analysis/security/patterns.py` | `security_scanner_changed()` reads true, so the next `update` rescans every tracked file. A store with no stamp counts as changed | A pattern is added or removed, a severity changes, or a rule's precision changes |
| `GIT_HISTORY_VERSION` | `core/ingestion/git_indexer/_constants.py` | `git_history_stale()` reads true, so the next `update` re-walks git history and rewrites the rows of files that did not change. A store with no stamp counts as stale. Also part of the event snapshot stamp | What the stored git rows hold changes: owners, top authors, bus factor, churn, blame owners |
| `STORE_FORMAT_VERSION` | `core/upgrade/version.py` | The upgrade manager runs every migration in `core/upgrade/registry.py` between the stored and the current format. Version 3 rebuilt the wiki once | The on-disk store layout changes. Add the matching migration to the registry in the same commit |
| `PARSER_SCHEMA_VERSION` | `core/upgrade/version.py` | Folded into the parse-cache fingerprint, so every file is re-parsed once and its call edges reconciled. Also part of the event snapshot stamp | Python-side extraction output changes. Grammar and query-file edits invalidate the cache on their own and need no bump |
| `CONTRACTS_VERSION` | `core/workspace/contracts.py` | Stored per-repo contracts with another version are not reused, so they are re-extracted on the next update. Also feeds the route fingerprint and the event snapshot stamp | A contract extractor or matcher changes what it emits |
| `ROUTE_TABLE_VERSION` | `core/workspace/repo_routes.py` | Changes the routes fingerprint, so each repo's route rows are rebuilt on the next update | The route module changes what a row holds |
| `STATEMENT_SITES_VERSION` | `core/workspace/statement_sites.py` | Changes the sites fingerprint, so each repo's statement-site rows are rebuilt on the next update | The statement-site module changes what a row holds |
| `EXTERNAL_CALL_SITES_VERSION` | `core/ingestion/external_calls.py` | A stored version that no longer matches marks a repo's `external_call_sites` incomplete, so the next update rebuilds them | The external call-site pass changes which calls it records or what a row holds |
| `KG_BUILDER_VERSION` | `core/analysis/knowledge_graph.py` | Part of the knowledge-graph fingerprint, so every store rebuilds its knowledge graph once, on the next update that has work to do | What the builder or its curation pass emits changes, including the edge-type map |
| `SNAPSHOT_FACTS_VERSION` | `core/events/snapshot.py` | Part of `current_stamp()`, so a stored snapshot run under another stamp is not reused and its facts are recomputed | A snapshot fact's definition changes |
| `ACTIONS_MODEL_VERSION` | `core/analysis/actions/model.py` | Part of the stored actions snapshot key, so a view built under another version is ignored and rebuilt | A rule's output changes meaning or shape |
| `FIX_FIRST_MODEL_VERSION` | `core/analysis/health/fix_first/model.py` | Part of the Fix-first snapshot and counts keys, so both are rebuilt | Ranking, eligibility or the item shape changes meaning |
| `ONBOARDING_GENERATION_VERSION` | `core/generation/onboarding/slots.py` | Folded into every onboarding page's source hash, so all onboarding pages regenerate once on the next docs update, with model calls | A builder or template change should reach pages already cached |
| `STRUCTURAL_GENERATION_VERSION` | `core/generation/page_generator/structural.py` | Structural pages regenerate once on the next docs update. No model calls | A renderer's output improves without any template's bytes changing |
| `MODEL_PAGE_GENERATION_VERSION` | `core/generation/page_generator/reuse.py` | Part of the model-page reuse fingerprint, so subject-keyed model pages regenerate once, with model calls | A change the template hash cannot see should reach model pages |

## Local cache versions

Each of these versions a cache file under `.repowise/` (or a cached payload).
A file with another version reads as absent and is rebuilt on the next run.
No stored result changes; the cost is one cold run. Bump when the payload's
shape changes. Changes to what feeds the cache usually key on a fingerprint
instead and need no bump; the row says when they do not.

| Constant | File | What a bump triggers | When to bump |
|---|---|---|---|
| `_VERSION` | `core/pipeline/incremental/resolution_cache.py` | `resolved_imports.pkl` is discarded and imports are resolved again. Also keyed on the parser fingerprint | The cached resolution payload changes shape |
| `_CACHE_VERSION` | `core/ingestion/parse_cache.py` | `parse_cache.pkl` is discarded | The pickle's shape changes. Extraction changes bump `PARSER_SCHEMA_VERSION` instead |
| `_CACHE_VERSION` | `core/analysis/health/walk_cache.py` | `health_walk_cache.pkl` is discarded and the health walk runs cold | The payload changes shape. Walker changes key on the fingerprint |
| `_CACHE_VERSION` | `core/analysis/health/duplication/token_cache.py` | `duplication_cache.pkl` is discarded and files are re-tokenized | The payload changes shape. Tokenizer changes key on the fingerprint |
| `_DELTA_VERSION` | `core/analysis/health/duplication/token_cache.py` | `duplication_cache_delta.pkl` is discarded | The delta payload changes shape |
| `_INDEX_VERSION` | `core/analysis/health/duplication/pair_index.py` | `duplication_pairs.pkl` is discarded and pairs are recomputed | The pair index changes shape or meaning |
| `_CACHE_VERSION` | `core/ingestion/graph/_centrality_cache.py` | `centrality_cache.pkl` is discarded and centrality is rescored | The entry shape changes, or the scoring changes: the signature keys on graph structure only, so a warm cache would keep the old values |
| `_CACHE_VERSION` | `core/pipeline/incremental/graph_seed.py` | `graph_seed_edges.pkl` is discarded | The seed snapshot changes shape |
| `_WINDOW_CACHE_VERSION` | `core/ingestion/git_commit_index.py` | The commit window and deep-region caches are re-walked | The git log format or the record layout changes |
| `_CACHE_VERSION` | `core/ingestion/git_indexer/fix_walk_cache.py` | `fix_walk_cache.json` is discarded | The cached fix walk changes shape |
| `_CACHE_VERSION` | `core/ingestion/dynamic_hints/scan_cache.py` | `dynamic_hint_scans.json` is discarded and files are rescanned | The scan record changes shape |
| `_CACHE_VERSION` | `core/savings/pricing.py` | `pricing-snapshot.json` reads as absent and the next scan rewrites it | The snapshot payload changes shape |
| `_VERSION` | `core/analysis/decisions/rationale_harvest.py` | Part of the `rationale_harvest.json` fingerprint, so the harvest reruns | The harvest output changes without its input files changing |
| `_PROMPT_VERSION` | `core/analysis/health/refactoring/llm/enrich.py` | Cached refactoring drafts written under the old prompt are not served; the next request drafts again, with a model call | The prompt's wording changes |
| `_ANSWER_SCHEMA_VERSION` | `server/mcp_server/tool_answer/config.py` | Cached `get_answer` payloads below it are ignored and answered again | The stored answer payload or what synthesis writes into it changes |
| `_OVERLAY_VERSION` | `core/workspace/cross_repo.py` | `cross_repo_edges.json` from an older version is discarded and rebuilt on the next workspace update | The meaning of a persisted cross-repo field changes |
| `_KG_SCHEMA_VERSION` | `core/analysis/knowledge_graph.py` | `knowledge-graph.json` below it is ignored and the knowledge graph rebuilt | The file's schema changes |
| `SCHEMA_VERSION` | `core/workspace/store.py` | `workspace.db` with another version is rebuilt | Any table change |

## Schema and format IDs

These version a file, an id, a payload or a wire response that something else
reads: an older repowise, a plugin, a CI job, an agent. A bump tells that
reader the shape changed. No analysis result is recomputed. Bump only for a
change a previous reader could misread: a removed or renamed field, or a field
whose meaning changes. Adding an optional field does not bump. Rows that work
differently say so.

| Constant | File | What a bump triggers | When to bump |
|---|---|---|---|
| `MIN_SHOWN_ANALYZER_VERSION` | `core/analysis/history/delta.py` | A display floor, not a stamp on stored data: stored commit deltas written below it read as never scanned. Nothing recomputes | Raise it with an analyzer bump that changes what a finding means |
| `CLIENT_IDENTITY_MAPPING_VERSION` | `core/agents/identity.py` | Recorded on savings events, so events attributed under different alias rules stay distinguishable | The announced-name alias rules change |
| `CHANGE_REVIEW_CONTRACT_VERSION` | `core/analysis/change_review/models.py` | Marks a serialized change-review bundle the previous reader cannot read | A field is removed, renamed or changes meaning |
| `MANIFEST_VERSION` | `core/analysis/decisions/manifest.py` | An older reader refuses a `decisions.yaml` with a higher version | A change a previous reader could misread; additive optional keys do not bump |
| `IDENTITY_VERSION` | `core/analysis/diff_risk/detect.py` | Hashed into every `drf_` diff-risk id, so every id changes. Nothing stores them | The identity inputs change |
| `BASELINE_VERSION` | `core/ci/baseline.py` | Versions the CI findings baseline, which the doc-drift baseline also uses | The baseline file changes shape |
| `EVIDENCE_SCHEMA_VERSION` | `core/changes/evidence.py` | Versions the change evidence checklist payload | A field is renamed, removed or changes meaning |
| `SCHEMA_VERSION` | `core/changes/record.py` | Versions the change record every surface serves | The record's shape changes for an existing reader |
| `EVENT_SCHEMA_VERSION` | `core/events/model.py` | Written to each engine event's `schema_version` | The event payload changes for an existing reader |
| `SCHEMA_VERSION` | `core/edit_capture/notes.py` | Versions authorship notes under `refs/notes/ai` | The note format changes |
| `INDEX_SCOPE_VERSION` | `core/index_scope.py` | Versions `state.json:index_scope` | The projection changes for an existing reader |
| `INVENTORY_VERSION` | `core/inventory/components.py` | Versions the component inventory payload | The payload changes for an existing reader |
| `SNAPSHOT_VERSION` | `core/perf_check.py` | Versions the saved exact-metric snapshots under `.repowise/perf/exact/` | The snapshot format changes |
| `BASELINE_VERSION` | `core/perf_measure.py` | An older `.repowise/perf/<name>.json` baseline is refused rather than misread | A breaking change to the baseline format |
| `_RECONCILE_LOGIC_VERSION` | `core/persistence/database.py` | Part of the schema reconcile stamp, so stores stamped by older code run the reconcile again | The reconcile learns a repair the model shape alone does not reveal |
| `PLUGIN_API_VERSION` | `core/registry/plugins.py` | Tells plugins the registry or seam contract changed | A breaking change to any registry or seam contract; a plugin's `MIN_PLUGIN_API_VERSION` above it marks the plugin failed |
| `SCHEMA_VERSION` | `core/report/model.py` | Versions the change report document | The document changes for an existing reader |
| `SCHEMA_VERSION` | `core/report/first_look/model.py` | A reader refuses a first-look document newer than it knows | The document's shape changes |
| `SCHEMA_VERSION` | `core/savings/contracts.py` | Versions each savings event payload | The event payload changes for an existing reader |
| `SAVINGS_SCHEMA_VERSION` | `core/savings/schema.py` | The savings store's `PRAGMA user_version`; an older build refuses a newer store and drops its events | Only a change an older build cannot read. Additive tables do not bump |
| `_VERDICT_REPAIR_VERSION` | `core/sessions/staging.py` | `PRAGMA user_version` marking that a one-shot repair ran on the session store | Never bumped. A new repair takes the next integer after `_SMEAR_REPAIR_VERSION` |
| `_SMEAR_REPAIR_VERSION` | `core/sessions/staging.py` | As above; a store below it gets the repair on its next update | Never bumped. A new repair takes the next integer |
| `SCHEMA_VERSION` | `core/settings/model.py` | Versions the settings JSON (`Setting.to_dict`, `Resolved.to_dict`) | A key is removed or changes type |
| `CURRENT_VERSION` | `core/workspace/config.py` | Versions the workspace config file | The config file's shape changes |
| `SCHEMA_VERSION` | `cli/agent_events.py` | Versions the CLI's machine-readable event stream | A field is renamed or removed, or changes meaning |
| `ARTIFACT_ENVELOPE_VERSION` | `server/chat_artifacts.py` | Versions artifact envelopes stored in chat messages | The envelope changes for an existing reader |
| `MCP_CONTRACT_VERSION` | `server/mcp_server/_meta.py` | The MCP response envelope version, shown by `get_overview` | The wire shape of the response envelope changes |
| `VIEW_SCHEMA_VERSION` | `server/schemas/views.py` | Versions stored chat view parts; readers accept the versions they list | A view part changes for an existing reader |

### Identifiers set by an external format

These name a version of someone else's format. Change one only to emit a
different version of that format.

| Constant | File | What a bump triggers | When to bump |
|---|---|---|---|
| `_BRANCH_COUNT_VERSION` | `core/analysis/health/coverage/clover.py` | The marker a known branch-count coverage writer emits, used to recognise its reports | That writer changes its marker |
| `SPEC_VERSION` | `core/inventory/cyclonedx.py` | The CycloneDX spec version the SBOM export declares | The export moves to another CycloneDX version |
| `API_VERSION` | `core/components/catalog.py` | The Backstage `apiVersion` of exported catalog entities | The export moves to another Backstage API version |
| `_API_VERSION` | `core/ownership/directory.py` | The Backstage `apiVersion` of exported ownership entities | The export moves to another Backstage API version |
