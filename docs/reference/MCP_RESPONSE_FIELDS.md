# MCP Response Fields

The field dictionary for every repowise MCP tool response. [MCP_TOOLS.md](../agent/MCP_TOOLS.md) says which tool to call and with what arguments; this page says what comes back and how to read it.

## Contents

- [The `_meta` envelope](#the-_meta-envelope)
- [Truncation and recovery](#truncation-and-recovery)
- [Ignored arguments](#ignored-arguments)
- [Next-call shape](#next-call-shape)
- Per tool: [get_answer](#get_answer) · [get_context](#get_context) · [get_symbol](#get_symbol) · [search_codebase](#search_codebase) · [get_risk](#get_risk) · [get_change_risk](#get_change_risk) · [get_why](#get_why) · [get_overview](#get_overview) · [get_health](#get_health) · [get_dead_code](#get_dead_code) · [list_repos](#list_repos) · [workspace tools](#workspace-tools)

---

## The `_meta` envelope

Every response carries `_meta`. Fields are present only when they carry a signal, so read absence as "nothing to report", never as an error.

| Field | When present | Meaning |
|-------|--------------|---------|
| `contract_version` | Always | Version of the response contract |
| `timing_ms` | Usually | Tool wall time |
| `hint` | Sometimes | A short follow-up suggestion. On a `get_answer` reply graded `low` while the index is behind HEAD, it says to run `repowise update` and ask again |
| `cached` | Only when `true` | The response came from cache |
| `index_age_days` | When a repository is resolved | Days since the last `repowise update` |
| `indexed_commit` | When a repository is resolved | Short SHA the index was built from |
| `live_head` | When `.git/HEAD` is readable | Short SHA of the current checkout; equal to `indexed_commit` when current |
| `index_behind` | When the live-versus-indexed comparison ran | `true` if HEAD moved, `false` if it matches. Absent means the comparison could not run |
| `stale_warning` | Only on a real signal | HEAD moved and the move changed files this response serves, or the index is very old and git is unreachable. Two commits with identical trees set `index_behind` with no warning |
| `index_scope` | When the index records it | Compact description of how the index was built: run mode, provenance, git tier, whether it is whole |
| `embedder_degraded` | When an embedder is resolved | `true` or `false` |
| `embedder`, `embedder_warning` | Only when the embedder fell back to a mock or degraded mode, or the semantic index on disk could not be opened | Which embedder, and why |
| `newer_release` | Once per server process | A newer repowise is published; restart the MCP server after upgrading |
| `response_budget` | Always | `limit_chars` (the ceiling), `tier` (`default` or `expanded`), `serialized_chars` (size delivered) |
| `omitted` | When content was cut | `refs`, `tokens`, `restore`. See [Truncation and recovery](#truncation-and-recovery) |
| `recovery_unavailable` | When the omission store could not be written | Names the storage failure; the cut rows cannot be restored |
| `scope_hint` | `get_answer`, `get_context` | Up to three knowledge-graph layers holding none of the served paths, with file counts: areas the answer did not touch |
| `complete` | When whole units were served | Symbol bodies (bounds verified against the live file) or whole files. Do not re-read them. Sliced bodies and partial ranges are never counted |
| `completeness` | Always | `capped`, plus `shown` and `total` summed over the collections a reducing pass counted, and `reason` naming the pass that dropped most. It measures what survived reduction, not what share of the repository you hold |
| `floor` | When a count comes from walking the graph | Names those fields. Their values are lower bounds: an unresolved edge is uncounted, not proven absent |
| `state` | Only when something fired | `degraded` with `degraded_reasons`, `partial`, `truncated`. A roll-up of the response's own flags |

`list_repos`, `get_architecture`, `get_blast_radius`, `get_conformance` and `get_dependency_path` carry no freshness fields. Neither does a workspace-wide `search_codebase(repo="all")`, since there is no single indexed commit to compare.

---

## Truncation and recovery

Responses fit 24,000 serialized characters by default and 32,000 when the call passes an expansion argument (for most tools, a nonempty `include`; for `get_answer`, `include=["evidence"]`). `_meta.response_budget` reports which ceiling applied.

Content cut to fit is stored in the repo's [omission store](../agent/DISTILL.md#the-omission-store), and `_meta.omitted` says how to get it back:

```jsonc
"_meta": {
  "omitted": {
    "refs": ["a1b2c3d4e5f6"],
    "tokens": 5840,
    "restore": "`repowise expand <ref>`, or get_symbol(\"repowise#<ref>\", query=...)"
  }
}
```

- A truncated skeleton block is replaced in place by a `[repowise#<ref>: ...]` marker. Other cut content goes into one combined document per response.
- A response still over budget sheds whole blocks in an order each tool declares, cheapest loss first, and reports `truncated: true`.
- A capped list keeps its count: it gains `<key>_total`, `<key>_emitted`, `<key>_truncated`, `<key>_omitted` and `<key>_reduced_reason` siblings as they apply. Some blocks also carry an `omission_marker` for their own tail.
- Restore with `repowise expand <ref>` in a shell, or `get_symbol(symbol_id="repowise#<ref>")` from any MCP client. `query` filters the restored lines.
- Include-gated blocks are projections, not omissions: a block you did not ask for is not reported as cut.

---

## Ignored arguments

A value outside a closed vocabulary is dropped, not applied, so the response is the one you would have got without it. The tool names what it dropped:

```jsonc
"ignored_arguments": [
  { "argument": "kind",
    "values": ["unused_exports"],
    "valid": ["unreachable_file", "unused_export", "unused_internal", "zombie_package"] }
]
```

The key is absent when every argument was understood. One entry per argument.

| Tool | Arguments checked | Shape |
|------|-------------------|-------|
| `get_dead_code` | `kind`, `tier`, `min_confidence` | Top-level list, as above |
| `get_context` | `include` | Top-level list |
| `search_codebase` | `kind`, `mode` (an unknown `mode` runs as `auto`) | Top-level list |
| `get_risk`, `get_change_risk` | `include` | Top-level list |
| `get_answer` | `include` | `_meta.ignored_arguments`, a map: `{"include": [...]}` |
| `get_health` | `refactoring_*` and `performance_*` filters, `scope`, `counts` | Top-level flat map of argument to dropped value, e.g. `{"counts": "code-shape"}` |

`get_health` reports a misspelled `only` key under `unknown_only_keys`. A detail lookup (`finding_id`, `plan_id`, `opportunity_id`) also lists `scope` and `counts` there, since a population control has nothing to act on for one stored row.

---

## Next-call shape

Every suggested follow-up (`get_health`'s `fix_first[].next_call`, the refactoring and performance summaries' `next_call`, `get_risk`'s `directive.next_calls`) has one shape:

| Field | Meaning |
|-------|---------|
| `purpose` | Why to make the call |
| `mcp` | The call as text, e.g. `get_health(opportunity_id="refop3_...")` |
| `cli` | The terminal command giving the same answer, or null |
| `tool`, `arguments` | The same call, structured |

---

## `get_answer`

| Field | Meaning |
|-------|---------|
| `answer` | The synthesised answer, with file and symbol citations |
| `confidence` | `high`, `medium` or `low`: rates the prose. `high` is content-grounded and can be cited directly. A why-answer whose named mechanism is absent from the retrieved source is downgraded to `medium` |
| `retrieval_quality` | `high`, `partial` or `weak`: rates the evidence under the prose |
| `symbol_bodies` | Live bodies of the symbols the answer names. Read these before calling `get_symbol` |
| `retrieval` | Evidence rows (summary, snippet, key symbols). Shrinks as confidence rises |
| `candidates` | Ranked shortlist of files retrieval resolved, `{path, lines?}`, up to 5. Navigation, not evidence. Only with `include=["evidence"]` |
| `best_guesses`, `fallback_targets` | On low confidence: where to look, each with a one-line reason |
| `episodes` | A dated fact recorded about this checkout that bears on the question; `still_true` says how current it is |
| `degraded` | Synthesis could not run (no provider, or the call failed). The answer is assembled from retrieval and mined rationale with no LLM, and `confidence` is graded from the retrieval: `medium` unless `retrieval_quality` is `weak`, never `high` |

How much evidence rides along depends on the grade. `high` keeps one quote, and one symbol body when the answer is not already grounded. `medium` keeps one body, two quotes and the top two evidence rows. `low` keeps two bodies and the top three rows. `include=["evidence"]` skips this trimming.

Retrieval fuses three legs by reciprocal rank: full-text and vector search over wiki pages, and the structural symbol index keyed on the question's content words. The symbol leg reaches private helpers that a file page's public symbol table does not list.

---

## `get_context`

Per target, under `targets`:

| Field | Meaning |
|-------|---------|
| title, summary, symbols | Docs summary and the symbols defined, with signatures and line numbers. Compact cards list the top 15 (types, then functions and methods, then the rest, by centrality) with `symbols_truncated` `{shown, total, hint}` and `symbols_total`; a budget trim below that keeps symbols by kind and name match, not centrality; `include=["symbols"]` lists all. A row without `symbol_id` is `path::name` |
| `hotspot` | Churn flag |
| `fix_history` | Only on files with counted bug fixes: count, age, `bug_magnet`. A cue to call `get_risk` |
| `episodes` | Count of dated records bound to the target; `get_why` serves their bodies |
| decisions | Titles by default. With `include=["decisions"]`, three lanes: `decisions` (accepted, governing), `candidates` (proposed, at most 3), `history` (accepted then withdrawn, at most 2) |
| `file_preview` | For a file with no indexed symbols: `lines`, `chars`, and for a markdown or reST document `heading_count` with the first three `headings`; otherwise the first three non-empty lines as `head` |
| `resolved_to`, `note` | A `path::Name` target whose symbol did not resolve answers with the file's card instead |
| `cross_repo` | Workspace mode: `co_changes_with` partners in other repos and contract `consumers` / `providers` |
| `*_basis` | Beside an empty `callers`, `callees` or `used_by`: the language, how many call edges the index resolved for it, and the share that are guesses. An empty list means no resolved edge, not proof of none |

Opt-in blocks: `full_doc`, `ownership` (primary owner, bus factor, contributor count), `last_change`, `callers`, `callees`, `metrics` (PageRank, betweenness, percentiles), `community`, `skeleton`, `health`, `doc_drift`, `symbols` (every symbol of a file).

**Skeleton.** Sliced from the index's stored symbol bounds, with no parsing at query time: every signature, the import preamble, and the bodies of the top symbols ranked by centrality, hotspot and query match. Elision markers carry 1-indexed line ranges, so you can read any part back.

**doc_drift.** `references` lists documents that name this file and still resolve to it. `documents_with_drift` says a listed document carries some assertion that no longer holds, anywhere in it, not necessarily about this file. Do not merge the two claims. `references_basis` states the limits; documents dropped by exclude rules are counted in `references_excluded`. An answer the store cannot support is a refusal: `{"unavailable": "not_computed"}` (the pass has not run yet), `"index_predates_doc_drift"`, or `"drift_read_failed"`.

**Several targets under one budget.** Each target gets an equal share, and a small target's unused share passes to the others. A target over its share drops symbols first, then its optional blocks largest first (named in `dropped_blocks`). Miss `suggestions` and ambiguity `candidates` are never shed. When even the identity cards cannot all fit, whole targets go first; the response leads with `dropped_targets` and a `recovery` call narrowed to them.

---

## `get_symbol`

| Field | Meaning |
|-------|---------|
| source | Up to about 600 lines (200 for a range read), each prefixed with its line number in `Read` format |
| start and end lines, `kind` | Exact bounds of the symbol |
| `truncated` | The body was cut; the response carries the continuation to pass back as `reference` |
| `ambiguous`, `candidates` | Several indexed symbols match (overloads, re-exports, conditional definitions). Every candidate body is returned; none is chosen silently |
| `callee_bodies` | With `depth` 2 or 3: each callee's `depth`, source and `verified` flag. Each symbol appears once, at its shallowest depth |
| `not_rendered` | Candidates or callees past the budget, each with a `fetch_with` range read |
| fallback lines | On a miss, the closest matches from a live grep |
| `source`, `created_at`, `original_tokens` | For an omission ref: where the stored content came from |

---

## `search_codebase`

| Field | Meaning |
|-------|---------|
| `results` | Ranked hits. Symbol hits: `type: "symbol"`, `symbol_id`, `name`, `kind`, `path`, `start_line`, `end_line`, `signature`, `next: "get_symbol"`. File hits: `type: "file"`, `page_id`, `path`, `title`, `next: "get_context"`. Concept hits: wiki pages with `page_type`, `path` when the page names a file, `relevance_score`, `snippet`, `sources` |
| `path` | The repo-relative file a row names, openable as is. Absent when the row names no file (a module page's group key, an onboarding slot, the repo overview); never a page id |
| `file` | Deprecated alias of `path` on symbol and file hits (and on a `symbol_spotlight` page). Removed in the next minor release |
| `target_path` | On a page hit, kept only where it is not the same string as `path`: a page with no `path` keeps its group key, slot or repo name here. Where it is dropped, `page_id` stays |
| `symbols` | On a symbol hit outside `symbol` mode: up to five other matching symbols in the same file, as `name:line`, then a `+N more` entry counting the rest. Those matches share the row instead of taking slots of their own |
| `sources` | The retrievers that found a concept hit: `fts`, `vector`, or both. A hit found by `fts` alone has no semantic agreement |
| `candidates` | Up to `limit` distinct openable file paths, best first |

Outside `mode="symbol"`, `limit` caps distinct files: hits are collapsed to one row per file, best row first, and the freed slots go to the next pages, then the next symbols. This includes concept mode, where a file page and a `symbol_spotlight` page of the same file are one row. `mode="symbol"` keeps one row per symbol, so overloads in one file each list.

Symbol hits rank by exact and qualified name match, query-token coverage, then graph centrality; non-test before test unless `kind="test"`. A `symbol_spotlight` page's id is `file.py::Symbol`; its `path` is the file.

`results` ranks pages, and some pages are not files: a `module_page` is named by a group key that looks like a directory, an `scc_page` by a hash. `candidates` resolves symbol pages to their file, collapses several symbols of one file into one entry, skips pages that name no file, and backfills from below the result window. If the next move is a Read, read `candidates`. A hybrid query (prose around an identifier) drops pages that name no file (module, onboarding, overview, decision pages) from `results` without refilling their slots, unless `page_type` or `kind="doc"` asks for pages; concept mode keeps them. Decision records rank below file pages unless the query is why-shaped.

---

## `get_risk`

Per file:

| Field | Meaning |
|-------|---------|
| `hotspot_score` | 0 to 1 churn percentile |
| `health_score` | 0 to 10 |
| `dependents_count` | Direct directed structural dependents; a floor over the indexed graph |
| `co_change_partners` | Historical correlation only. Each has a recency-decayed `weight`, a `direction` (`a_to_b`, `b_to_a` or `undirected`, where `a` is the assessed file), and `conf_ab` / `conf_ba`, the share of each file's commits that touched the other |
| owners, reviewers | Primary and recent owners (`owner_pct`, `recent_owner_pct` are 0 to 1) and recommended reviewers |
| test gaps, `security_signals` | Test coverage gaps and security findings for the file |
| `resolved`, `unresolved_reason` | A target naming no indexed file: `unsupported_target_kind` (a `module:` id), `directory`, `not_indexed`, `no_such_path`. Counts are omitted, never zeroed |

Opt-in blocks: `graph` adds typed `dependents` (direct versus transitive), `consumers` (typed contract consumers only), `cross_repo_links`, `impact_surface` and `direct_risks`, with `relationship_analysis` distinguishing an empty analysis from an unavailable or partial one. `churn` adds `change_magnitude`, `risk_type` and `change_pattern`. `scales` adds the unit, range and calibration of every scalar; it is identical on every call, so ask once. A multi-target call also carries `global_hotspots`.

**PR mode** (`changed_files` passed). The response starts with `directive`:

| Field | Meaning |
|-------|---------|
| `may_break` | Production files in reverse-import reach of the change: candidates for review, not proven breakage |
| `may_break_tests` | Test files reached the same way, kept separate so tests do not crowd out production impact |
| `missing_cochanges` | Historical co-change partners absent from the change |
| `test_recommendations` | Typed rows, each with a `basis`: `measured` (the per-test coverage map found the test) or `inferred` (structural reach, not coverage proof) |
| `tests_to_run`, `tests_to_run_basis` | The test ids or files to run, and whether the list is `measured`, `inferred` or `none`. An empty list with unavailable coverage never means no tests are needed |
| `missing_tests`, `files_without_measured_tests` | Changed files with no measured test |
| `coverage_analysis`, `test_inference_analysis` | Whether each evidence source was available, stale, partial or degraded |
| `structural_impact_score` | Uncalibrated 0 to 10 structural heuristic: `localized` below 4, `moderate` 4 to below 7, `broad` 7 and up. Not a breakage probability. `overall_risk_score` is a deprecated alias |
| `next_calls` | `get_change_risk()` for the diff itself, then `get_context` on the first `may_break` files |

In workspace mode the directive also carries `will_break_consumers` (services in other repos that structurally depend on this one; structural reach only despite the name), `missing_cross_repo_cochanges`, `breaking_changes` (provider incompatibilities since the last index, with impacted consumers; a consumer link does not prove field use), `conformance_violations` and `dependency_cycles`.

Every float is rounded to 4 significant digits. Direct rows' `structural_score` values are in pagerank-weighted hotspot units and are not comparable to `get_change_risk` scores.

---

## `get_change_risk`

| Field | Meaning |
|-------|---------|
| `directive` | `status` (`review_required`, `review_recommended`, `clear_in_analyzed_scope`, `unknown`), a headline, reasons, next actions |
| `health_delta` | What the change newly made worse. See below |
| `risk_percentile`, `review_priority`, `classification`, `is_fix` | The change ranked against the repo's recent commits |
| `diff_shape` | One sentence on size and spread; never a danger verdict |
| `working_tree` | Whether uncommitted work was the subject |
| `fix_history` | Recency-weighted bug-fix record of the touched files, with `percentile` and `overlap` |
| `impacted_tests` | Tests that execute the changed lines |
| `patch_coverage` | Share of changed executable lines the stored coverage ran |
| `branch_overlap` | Other branches editing the same files |
| `independent_changes` | When the diff is several unrelated changes |
| `cross_repo` | Workspace mode: consumers and breaking changes across repos |

`include=["diagnostics"]` adds `score` (the raw 0 to 10 number), `fallback_band`, `risk_authority`, `score_measures`, `score_unit`, `baseline_sample_size`, `features` and `drivers`. `include=["scales"]` adds each field's unit, range and calibration.

**health_delta.** Both revisions are analysed from their own content, and a finding at head is reported only when the diff explains it. `scope` counts changed, eligible, analysed, skipped and failed files; `status` is `available`, `partial` or `unavailable`, and `partial` is never a clean bill. `introduced`, `worsened` and `resolved` are totals; `top_findings` holds the three most actionable, with `findings_total`. Each finding names `dimension`, `biomarker`, `severity`, `path`, `symbol`, head-side `lines`, a `reason`, and an `attribution` (`basis` of `added_lines`, `changed_symbol`, `changed_call_edge`, `new_file`, `file_change`, `context_change` or `unknown`, with a `confidence`). Identity ignores line numbers, so moved code introduces nothing. `inspect` gives the `finding_id` call that expands one; ids are bound to the two revisions. A finding that matches a stored one carries `health_reference` for `get_health(finding_id=...)`. Performance findings carry `opportunity_id` and are ordered by opportunity rank.

**impacted_tests.** `tests_to_run` names tests the per-test coverage map proves execute the changed lines, capped at ten with `total` and `truncated`. `line_coverage` buckets: `untested_changes`, `stale_test_candidates` (covered lines whose test file is absent from the diff), `covered`, `no_coverage_data`. With no coverage map, `status` is `inferred` when the import graph names test files reaching the change, or `no_map` (run the full suite). `basis` is always present: `measured`, `inferred` or `none`; `tests_to_run_kind` is `test_id` or `test_file`. Build the measured map with `coverage run --contexts=test` and `repowise coverage add`.

**patch_coverage.** Present when the index stores coverage; the same computation and JSON shape `repowise coverage check --format json` gates on. `patch_coverage_pct` is null when no changed line is executable; files the report never names read `not_in_report`. `scope.freshness` is `stale` when coverage was measured at another commit than the change's head. `path_gates` judges the path-scoped gates in `coverage.gates`. Each file row carries `risk` (`fix_pressure`, `dependents`, `hotspot`, `bug_magnet`, `risky`, `reasons`, `basis`) and up to eight `hints` per uncovered range: `range`, `symbol`, up to three `tests` to extend, and a `basis` of `per_test` (measured), `call_graph` or `import_graph` (inferred). Without a `revspec`, it covers everything a push would bring, diffed from the merge-base with the default base branch.

**fix_history.** `is_fix` applies a fixed keyword rule to the commit subject, not the conventional-commit type. `overlap` adds a diff-shape filter and needs an index: per file, `fix_count`, `overlapping_lines` (labelled `approximate`: past fix ranges are numbered against their own parent commit, so read it as "this area was patched before"), `last_fix_days_ago`, `changed_lines` and `share_of_change`. `concentration` names a file holding at least half the changed lines. The block never names the commit that introduced a bug, and is absent on an index with no fix history.

**branch_overlap.** Git-only, so it works without an index. Each `branches[]` entry has `ahead`, `behind`, `last_commit` and `files[]`; each file row states its `basis` (`same file`, or a co-change pair with its `partner`). The scan covers the newest 50 branches; `scanned`, `total` and `truncated` describe the scan, not a cap. Absent when no other branch edits a shared file, or when the scan times out.

**independent_changes.** Groups changed files by connectivity over index edges, co-change pairs, and (for a range) shared commits. Docs, config, data files and tests are never grouped. Each `groups[]` entry lists `files` and `bridging_files`; `ungrouped_files` and `summary` name what was left out and why; `basis` states what was checked. Absent when the diff is one change.

**cross_repo.** `consumers[]` names each contract link (`provider_file`, consumer `repo`, `file`, `contract_id`, `contract_type`, `match_type`) and a `tests` block (`state` of `measured`, `inferred`, `none` or `unresolved`, up to five `tests_to_run`). `breaking_changes[]` carries `contract_id`, `kind`, `severity`, `detail` and `impacted_repos`; `breaking_changes_available` says whether detection ran, so an empty list is not an all-clear. Absent outside workspace mode or when the change touches no published file.

---

## `get_why`

| Field | Meaning |
|-------|---------|
| `decisions` | Accepted records: someone accepted each in a recorded event. Treat as constraints |
| `candidates` | Inferred records nobody accepted. Hints, never rules; `candidates_note` says so. At most 3 |
| `history` | Accepted, then withdrawn. At most 2 |
| `answer_basis` | Strongest lane served: `decision`, `episode`, `rationale`, `archaeology`, `documentation`, `candidate`. Only `decision` is a ruling |
| `authority` | Per row: `accepted` or `candidate` |
| `currency` | On accepted records: `active`, `needs_review` (files moved, still binds), `uncheckable` (names nothing), `superseded`, `dismissed`. Candidates carry `review_state: "open"` instead |
| `alignment` | Path mode: `active_count`, `deprecated_count`, `uncheckable_count`, `candidate_count`, summing to `governing_count`. `score` comes from `active_count` alone |
| `git_archaeology` | Present when no decision governs the path |
| `provenance`, `evidence_refs` | Self-contained evidence references. Matching ids mean shared evidence, not independent corroboration |

Read the lane, not `status`: a record can carry `status: "active"` with no acceptance behind it. Only `repowise decision confirm` or a committed ADR marked accepted produces an acceptance.

The `candidates` and `history` lanes shed first under budget pressure, so an absent lane may mean the budget was tight. The health dashboard (no arguments) returns stale decisions, conflicts, ungoverned hotspots, `retired_decisions` and `unscoped_decisions` (five rows each, full sizes in `counts`).

---

## `get_overview`

| Field | Meaning |
|-------|---------|
| `content_md` | The overview essay's summary section; the full essay with `include=["content"]` |
| `key_modules` | Name, path, and outline section |
| `entry_points` | Detected entry points |
| `architecture` | Layer names and file counts in stack order, test layer left out |
| `code_health`, `git_health` | Repo-level health and history summaries |
| `next_actions` | Per horizon (week, quarter): the first three stored actions (`id`, `tier`, `title`, `impact`, `done_when`, `target`) with `total` and `by_tier` |
| `more` | Names the opt-in blocks |
| `workspace` | Workspace mode footer |

Opt-in blocks: `outline` (wiki page tree, two levels), `tour` (`guided_tour`, `reading_order`), `decisions` (`key_decisions`), `graph` (`community_summary`), `ownership` (`knowledge_map`). They are not computed unless named.

---

## `get_health`

### Modes and identity

`mode` is `dashboard` (no targets), `targeted`, `fix_item` (`fix_id`), or `conflict` (two selectors passed). `unresolved` names targets that matched nothing, with a reason: `not_indexed`, `no_such_path`, `excluded`, `not_measured`, `no_such_module` (which also returns `known_modules`). `mode`, `_meta`, `unresolved`, `known_modules` and every kept list's `*_total` survive any `only` projection. `scope` and `counts` are echoed on the response.

`_meta.health_analysis` labels the result as stored analysis that this call did not recompute. Its `status` is `available`, `provenance_unknown` (metrics exist but no row recorded their commit) or `unavailable`. `_meta.health_analyzed_at` and `_meta.health_analyzed_commit` date the health pass, which can lag indexing; `_meta.health_analyzed_commits_distinct` appears when rows come from several passes.

### fix_first

One ranked queue across refactoring, performance and code-shape work, the same object `repowise health` renders.

| Field | Meaning |
|-------|---------|
| `lead` | Equal to `items[0]` |
| `items` | Up to five: `id`, `tier`, `kind`, `title`, `target`, `why`, `gain`, `effort`, `confidence`, `next_call` |
| `tier` | `now` (worth doing, safe to start), `next` (worth doing, needs judgment), `later` |
| `kind` | `refactor` (one file's composed refactoring), `perf_fix` (one intervention with its sinks), `finding` (a code-shape finding with no plan) |
| `totals` | `candidates`, `eligible`, `shown`, and `excluded` counted by reason |
| `by_improves`, `basis`, `model_version`, `detail_call` | Rollup, basis, and the call that opens the lead in full |

Items are ordered by value first, then tier. History markers (churn, ownership, co-change, prior fixes) never create or lead an item; they ride on an item under `context`, and history-only files count under `totals.excluded.history_only`. `fix_first` always describes production code, whatever `scope` says; `limit` caps the items, never which leads.

### Dashboard and metric rows

| Field | Meaning |
|-------|---------|
| `kpis` | Hotspot health, `average_health` (NLOC-weighted), `average_health_unweighted`, `average_health_weighting`, pillar averages, `worst_performer_path`, `worst_test_path`, `non_code_files`, `average_health_code_only` |
| `gap_analysis` | Weighted points the average must recover to reach 8.0, files below it, `files_to_reach_target`, `files_for_half_gap`, `weighted_gap_points`, `weighted_gross_gap_points` |
| `worst_files`, `test_worst_files` | Lowest raw scores, production and test |
| `high_leverage_files` | Ranked by `weighted_deficit`, with `share_of_repo_gap_pct` |
| `top_findings` / `findings`, `test_findings` | Production and test findings; their totals sum to the whole open set |
| metric row | `score` (defect dimension and headline), `maintainability_score`, `performance_score`, `weighted_deficit` = (8 - score) x NLOC, `is_test`, `has_test_file`, `primary_biomarker` |

`weighted_deficit` and the gap fields share one unit, score points x NLOC; they compare only with each other. Non-code files score a mechanical 10.0 because no marker reads them; `average_health_code_only` shows the headline without them. `primary_biomarker` prefers a discrete finding over the continuous `coverage_gradient`. Each finding carries a `dimension`; `advisory` findings (`assertion_free_test`, `mock_saturated_test`) never deduct and appear only with `include=["advisory"]`.

Provisional finding types appear only with `include=["unverified"]`, each marked `verification: "unverified"`. Targeted mode is never split into production and test.

### Opt-in blocks

| `include` | Adds |
|-----------|------|
| `biomarkers` | Findings in dashboard mode |
| `refactoring` | `refactoring_opportunities`, `suggestion_legend` |
| `trend` | Snapshot diff and declining or predicted-decline alerts |
| `coverage` | Stored coverage rows and summary |
| `accuracy` | `defect_accuracy`: of the K least-healthy files, how many were recently bug-fixed versus the base rate (precision@K, `lift`). `null` on repos with too little history |
| `signals` | Targeted mode: per-file prior defects, change scatter, 90-day churn, owners, graph degree. `null` per missing field, never an imputed zero |
| `churn_complexity` | One point per recently changed file: commits, max CCN, NLOC, score, churn percentile |
| `doc_drift` | `findings` naming the document to edit, its line, the `target` it wrongly claims, `reason`, `kind`, `confidence`, optional `suggestion`; plus `findings_total`, `documents`, `basis`. A finding is evidence to check, not a proven defect |
| `semantics` | `_meta.health_semantics`: the legend for deficit points and percentiles |
| `performance`, `defect`, `maintainability`, `advisory` | Filter findings to one dimension; `performance` also adds the performance queue |

`only` aliases: `biomarkers` -> `findings`, `accuracy` -> `defect_accuracy`, `refactoring` -> `refactoring_plans`. Dimension names have no single key and land in `unknown_only_keys`; for `signals` in targeted mode, name `metrics`.

### Refactoring

`refactoring_opportunities` holds one composed unit per file: `lead_biomarker`, `addresses_primary_problem` (`true`, `false`, or `null` when no dominant finding was recorded), ordered `steps` each with a `mechanical` or `judgment` applicability, and evidence counts. With no targets it lists what Fix first would take (`refactoring_scope: fix_first`); `refactoring_opportunities_hidden` counts the rest by reason and `refactoring_opportunities_scope` names the scope. A step with `relocated_by` refers to a symbol an earlier step moves: locate it again before applying.

`get_health(opportunity_id="refop...")` returns full steps, member plans, the validation profile and `next_actions`; `only=["refactoring_evidence"]` with `cursor` pages the evidence. `found` says whether the id matched; `status` is `open`, `acknowledged`, `resolved` or `false_positive`. `refactoring_summary` rolls up by type, effort, confidence and lifecycle.

`refactoring_plans` is the raw per-detector list, returned only when named in `only`: `extract_class` (cohesion `groups`), `extract_helper` (clone `occurrences`, `suggested_site`), `move_method`, `break_cycle` (`cut_edges`). Each carries `evidence`, `impact_delta`, `effort_bucket`, `blast_radius`, `file_weighted_deficit` and an `id` for `generate_refactoring_code`. An empty requested list has `refactoring_plans_status.reason`: `no_applicable_findings`, `plan_analysis_indeterminate`, `no_eligible_targets` or `analysis_unavailable`. See [REFACTORING.md](../layers/REFACTORING.md).

### Performance

Performance findings carry `health_impact: 0`; the pillar never blends into the score. They rank on an integer `perf_rank`, which weighs the marker (superlinear above per-iteration crossings above in-loop CPU), the boundary crossed (`details.boundary_kind`: subprocess, then network or database, then lock, then filesystem) and whether the loop crosses a function call (`details.cross_function`). It is an ordering key, not a score.

A bare `get_health()` ranks performance work inside `fix_first` as `perf_fix` items. `get_health(opportunity_id="perf...")` adds `plan_steps`, `validation` and `siblings`; `only=["performance_evidence"]` with `cursor` pages the evidence. Ids are stable within a performance model version; an id from an older model resolves to `model_state.state: "stale_model"` with `refresh_required`. `lifecycle_status` is `open` or `resolved`. Evidence rows carry a public `finding_id`.

### Coverage

`include=["coverage"]` returns per-file rows (`covered_line_count`, `total_coverable_lines`; targeted mode adds `covered_lines`) and a `summary`. `freshness.status` is `current` (measured at the indexed commit), `stale` or `unknown`. `report_paths` says how the report's entries mapped at ingest (`total`, `matched`, `unmatched`, `ambiguous`, `unmatched_sample`). `source_formats` lists merged formats. Dashboard mode adds `history`, one point per complete ingest, newest 10. Targeted rows add `decay`: `confirmed_lines`, `invalidated_lines` (now unknown, not uncovered), and `drifted` once a fifth of a file's measurement has moved.

---

## `get_dead_code`

| Field | Meaning |
|-------|---------|
| `tiers` | Findings by tier: `high` (0.7 and up), `medium` (0.4 to 0.7), `low`. Each has path, kind, confidence, line count, cleanup impact and a stable `id` |
| `summary` | Totals over every open finding; `filtered_findings` counts what the filters kept; `filters` echoes non-default filters; `scope` says which is which |
| `summary.withheld_types` | Provisional finding types held back; ask with `kind=` to see them, marked unverified |
| `summary.call_resolution_basis` | Per language, how many call edges the index resolved and the share that are guesses: the graph the findings rest on |
| `by_directory`, `by_owner` | Rollups with `group_by` |
| `limit_note` | A `limit` over 25 was clamped |

The 0.7 and 0.4 tier floors are shared by the CLI, the web UI and this tool. See [DEAD_CODE.md](../layers/DEAD_CODE.md).

---

## `list_repos`

`workspace` (bool), `workspace_root`, `default_repo`, and per repo `alias`, config-relative `path` and `absolute_path`. In single-repo mode: `workspace: false`, `workspace_root: null`, `default_repo: "default"`.

---

## Workspace tools

**`get_blast_radius`.** Impacted services with `score` (0 to 1 relative path weight, uncalibrated), `distance` (hops), `structural` (a real dependency versus co-change only) and edge kinds; `impact_score_semantics`, `impacted_repos`, `structural_count`, `behavioral_count`, `total_impacted`, unresolved targets. `symbol_targets[].consumers[]` names consuming symbols across each contract link, each with a `tests` block shaped like `get_change_risk`'s.

**`get_architecture`.** `score` (1 to 10, higher means lower coupling and a smaller core), `architecture_type` (`core-periphery` or `hierarchical`), `propagation_cost_pct`, `core_size`, `core_ratio`, `core_members`, `cycle_count`, `conformance_violations`, `role_breakdown` (Core, Shared, Control, Peripheral counts), `summary`.

**`get_conformance`.** `violations` (offending `source` and `target` services, the `rule_source` / `rule_target` matchers that fired, `edge_kind`), `cycles` (`nodes`, `length`), `violation_count`, `cycle_count`, `rules_evaluated`. A report that never ran says so instead of returning zero counts.

**`get_dependency_path`.** The path when one exists; otherwise nearest common ancestors, shared neighbours, community analysis and bridge suggestions.

**`get_execution_flows`.** Scored entry points, each with a breadth-first call trace and whether it crosses community boundaries.

**`generate_refactoring_code`.** `suggestion_id`, `plan`, the generated code and unified diff, or `generation: {available: false, reason: "disabled"}`, or `error: "no_provider"`.

**`set_finding_status`.** The new status and its timestamp.
