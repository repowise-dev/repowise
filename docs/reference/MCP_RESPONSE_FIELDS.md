# MCP Response Fields

The field dictionary for every repowise MCP tool response. [MCP_TOOLS.md](../agent/MCP_TOOLS.md) says which tool to call and with what arguments; this page says what comes back.

## Contents

- [The `_meta` envelope](#the-_meta-envelope)
- [On the wire](#on-the-wire)
- [Truncation and recovery](#truncation-and-recovery)
- [Ignored arguments](#ignored-arguments)
- [Next-call shape](#next-call-shape)
- [Workspace routing](#workspace-routing)
- Per tool: [get_answer](#get_answer) · [get_context](#get_context) · [get_symbol](#get_symbol) · [search_codebase](#search_codebase) · [get_risk](#get_risk) · [get_change_risk](#get_change_risk) · [get_why](#get_why) · [get_overview](#get_overview) · [get_health](#get_health) · [get_dead_code](#get_dead_code) · [list_repos](#list_repos) · [workspace tools](#workspace-tools)

---

## The `_meta` envelope

Every response carries `_meta`. A field is present only when it carries a signal; absence means "nothing to report", never an error. The same rule holds across the payload: empty strings, empty lists, `null` and a `false` status flag (`*_degraded`, `*_truncated`, `*_capped`, `*_partial`) are not sent. A `false` that is itself the answer (`test_gap`, `is_hotspot`, `verified`, `complete`) stays.

Routine responses carry a lean envelope. `get_overview` (called once per session) carries the whole one: `contract_version`, `timing_ms`, `response_budget`, `completeness`, `state` and the freshness detail. `REPOWISE_MCP_DEBUG_META=1` restores it on every tool, plus `tokens_saved` and `replaced_tokens`; it is read per call, so no restart is needed.

| Field | When present | Meaning |
|-------|--------------|---------|
| `hint` | Sometimes | A short follow-up suggestion |
| `cached` | Only when `true` | The response came from cache |
| `stale_warning` | Only on a real signal | One fact: the commit the facts below describe, and what differs from it for this response |
| `index_scope` | Recorded, and `status` not `complete` | How the index was built: run mode, provenance, git tier, whether it is whole |
| `degraded` | A part of the server failed for this call | Reasons, e.g. `["embedder"]` or `["retrieval:vector"]` |
| `embedder_warning` | Embedder fell back, or the semantic index could not open | Why |
| `semantic_search` | Only when `false` | Retrieval is full-text only (keyless install or degraded embedder) |
| `newer_release` | Once per server process | A newer repowise is published; restart the server after upgrading |
| `server_stale` | Every response, once repowise's code changed on disk after the server started | The server mixes old and new code (after a pull or an upgrade) until it restarts |
| `keyless_note` | Keyless `get_answer`; `get_overview` until a model writes the prose | The command that adds answers and docs |
| `omitted` | Content was cut | `refs`, `tokens`, `restore`; see [Truncation and recovery](#truncation-and-recovery) |
| `recovery_unavailable` | The omission store could not be written | The storage failure; cut rows cannot be restored |
| `complete` | Whole units were served | Verified symbol bodies or whole files. Do not re-read them |

Debug envelope only (`get_overview`, or `REPOWISE_MCP_DEBUG_META=1`):

| Field | Meaning |
|-------|---------|
| `contract_version` | Response contract version (currently 4); the version of record even when not emitted |
| `timing_ms` | Tool wall time |
| `block_timing_ms` | `get_change_risk` only: wall ms per block (`score`, `impacted_tests`, `patch_coverage`, `prior_fixes`, `cross_repo`, `branch_overlap`, `health_delta`). Some blocks overlap, so they need not sum to `timing_ms` |
| `index_age_days` | Whole days since the index last synced, the same time `repowise status` reports |
| `indexed_commit`, `live_head` | Short SHAs of the index and of the checkout |
| `index_behind` | `true` if HEAD moved, `false` if not; absent if the comparison could not run |
| `working_tree_dirty` | Served targets with uncommitted edits no `repowise update --working-tree` has indexed |
| `embedder_degraded`, `embedder` | The embedder state behind `degraded` |
| `response_budget` | `limit_chars`, `tier` (`default` or `expanded`), `serialized_chars`; `enforcement_error` when the protected fields could not fit |
| `completeness` | `capped`, `shown`, `total`, `reason`: the collections a reducing pass counted |
| `floor` | Fields whose values are lower bounds |
| `state` | Roll-up of `degraded` (with `degraded_reasons`), `partial` and `truncated` |

Notes:

- **`hint`:** on a `get_answer` graded `low` while the index is behind HEAD and no file it cites changed, it says so.
- **`stale_warning`:** HEAD moved and changed files this response serves; a served file has unseen uncommitted edits, or was indexed from edits since reverted; or the index is very old and git is unreachable. HEAD moving without touching what was served sends nothing. It is a fact, never a command: `Facts below describe <indexed sha>; a file this response serves differs at HEAD <sha>.` (or `differs in the working tree`, or, for a call from another checkout, `differs in <checkout> at <sha>`). `repowise update` (`--working-tree` for uncommitted edits) brings the index up to date.
- **`index_scope`:** the whole object comes with `get_overview(include=["scope"])`, or everywhere with `REPOWISE_MCP_INDEX_SCOPE=full`.
- **`keyless_note`:** on every `get_answer` that degrades with `no-llm-provider`, and on `get_overview` while no model has written the prose. One line: set a provider key, run `repowise generate` (one command, no re-index; `repowise update --full` on an Index only repo), then restart the MCP server. With model-written prose, `get_answer` names only the key and the restart.
- **`complete`:** sliced bodies and partial ranges never count.
- **Counts from the graph are floors:** `dependents_count`, `co_change_partners_total` and similar counts walk resolved edges, so an unresolved edge is uncounted, not proven absent.
- **No freshness fields** on `list_repos`, `get_architecture`, `get_blast_radius`, `get_conformance`, `get_dependency_path`, or a `repo="all"` call (no single commit to compare). `repo="all"` names stale repos in a top-level `repos_stale`.

---

## On the wire

- `structuredContent` is `{"result": <payload>}`; tools advertise an `outputSchema`.
- The text block is the same payload as compact JSON, so clients that forward it send fewer tokens.
- `REPOWISE_MCP_PRETTY_JSON=1` restores indented text.

---

## Truncation and recovery

Responses fit 24,000 serialized characters, or 32,000 with an expansion argument (a nonempty `include` for most tools; `include=["evidence"]` for `get_answer`). Cut content goes to the repo's [omission store](../agent/DISTILL.md#the-omission-store):

```jsonc
"_meta": {
  "omitted": {
    "refs": ["a1b2c3d4e5f6"],
    "tokens": 5840,
    "restore": "`repowise expand <ref>`, or get_symbol(\"repowise#<ref>\", query=...)"
  }
}
```

- A truncated skeleton block becomes a `[repowise#<ref>: ...]` marker in place. Other cut content goes into one document per response.
- A response still over budget sheds whole blocks in a per-tool order, cheapest loss first, and reports `truncated: true`.
- A list that was cut carries `<key>_total`, the size before the cut. A list served whole carries no count.
- Restore with `repowise expand <ref>` or `get_symbol(symbol_id="repowise#<ref>")`; `query` filters the lines.
- Include-gated blocks you did not ask for are not reported as cut.

---

## Ignored arguments

A value outside a closed vocabulary is dropped, so the response is the one you would get without it, and the tool names it. Absent when every argument was understood; one entry per argument.

```jsonc
"ignored_arguments": [
  { "argument": "kind",
    "values": ["unused_exports"],
    "valid": ["unreachable_file", "unused_export", "unused_internal", "zombie_package"] }
]
```

| Tool | Arguments checked | Shape |
|------|-------------------|-------|
| `get_dead_code` | `kind`, `tier`, `min_confidence` | Top-level list |
| `get_context`, `get_risk`, `get_change_risk`, `get_overview` | `include` | Top-level list |
| `search_codebase` | `kind`, `mode`, `pattern` when `query` is also given | Top-level list |
| `get_answer` | `include` | `_meta.ignored_arguments` map: `{"include": [...]}` |
| `get_health` | `refactoring_*`, `performance_*`, `scope`, `counts` | Top-level map, e.g. `{"counts": "code-shape"}` |

- **`get_risk`:** `include` values `tests` and `blast` without `changed_files` land in one entry with `"requires": "changed_files"`, since only PR mode builds them.
- **`search_codebase`:** an unknown `mode` runs as `auto`; a `pattern` entry carries `"superseded_by": "query"`.
- **`get_health`:** a value meant for the other control (`scope="code_shape"`) adds `ignored_arguments_hint`, except on a detail lookup. A misspelled `only` key lands in `unknown_only_keys`; a detail lookup (`finding_id`, `plan_id`, `opportunity_id`) lists `scope` and `counts` there too, since they cannot act on one row.

---

## Next-call shape

Every suggested follow-up (`get_health`'s `fix_first.lead.next_call`, the refactoring and performance summaries' `next_call`, `get_risk`'s `directive.next_calls`) has one shape:

| Field | Meaning |
|-------|---------|
| `purpose` | Why to make the call |
| `mcp` | The call as text, e.g. `get_health(opportunity_id="refop3_...")` |
| `cli` | The equivalent terminal command, or null |
| `tool`, `arguments` | The same call, structured |

---

## Workspace routing

In workspace mode, `get_context`, `get_symbol`, `get_risk` and `get_health` serve a path (or `alias:path`) from the repo that has it when no `repo` is passed.

| Field | Meaning |
|-------|---------|
| `repo` | The alias that answered, when routed away from the default repo (per card in `get_context`, top level elsewhere) |
| `candidates` | A target in several repos: their aliases, beside an `error` naming the `alias:path` form. Nothing is guessed |
| `routes` | Refusal from a one-repo tool whose targets span repos: per target `{"repo": alias}` or `{"candidates": [...]}`; `error` names the per-repo calls |
| `hint` | A target no repo has: points at `search_codebase(query=..., repo="all")` |

---

## `get_answer`

| Field | Meaning |
|-------|---------|
| `answer`, `citations` | The answer, with file and symbol citations |
| `confidence` | `high`, `medium` or `low`: the one trust signal. Cite `high` directly |
| `retrieval_quality` | With `include=["diagnostics"]`: `high`, `partial` or `weak`, rating retrieval alone |
| `symbol_bodies` | Live bodies of symbols the answer names. Read before calling `get_symbol` |
| `retrieval` | Evidence rows: one text (`excerpt` where served, else `summary`) and key symbols; shrinks as confidence rises |
| `candidate_files` | Ranked paths not already in `citations`: up to 3 at `high`, else 5. Navigation, not evidence |
| `candidates` | With `include=["evidence"]`: the top 5 files as `{path, lines?, defines?}` |
| `best_guesses`, `fallback_targets` | On low confidence: where to look, each with a reason |
| `episodes` | Dated facts about this checkout; `still_true` says how current |
| `degraded` | Synthesis could not run (no provider, or the call failed) |
| `grounding` | No-synthesis answers: `exact_symbol` or `extracted` |
| `note`, `next_action_hint` | How the answer was built; on weak retrieval, which `best_guesses` row to start from |
| `next_actions` | Up to 3 `get_symbol('<id>')` calls for functions the answer names whose bodies it does not serve; with a `flow_path`, the first is `get_symbol('<id>', depth=2)`, which also returns the bodies it calls |

Notes:

- **`answer`:** a quoted code block found in a cited file carries that file's line range in its citation (`(src/compose.ts:50-60)`).
- **`confidence`:** `high` is content-grounded on a top hit covering the question, or states the value on the verified definition line of a constant the question names, wherever that line ranked. A why-answer whose mechanism is absent from the source drops to `medium`.
- **`retrieval_quality`:** `weak` when the top hit leaves out most of the question.
- **`candidate_files`:** served at every confidence; absent when retrieval resolved no file.
- **`best_guesses`:** at `low` or `degraded` a row swaps its `excerpt` for `functions` (up to 3 `{name, line}`), `lines` and `size_bytes`; `include=["evidence"]` restores it.
- **`degraded`:** built from retrieval and mined rationale with no LLM; `confidence` is `medium` on a dominant covering top hit, else `low`, never `high`. With `weak` retrieval too, the reply is `best_guesses`, `candidate_files` and one guidance line, with no `citations`.
- **`grounding`:** `exact_symbol` serves the bodies of an exactly named symbol; `extracted` quotes a live source line verbatim.
- **Evidence by grade** (`include=["evidence"]` skips this): `high` keeps one quote, plus one body when not already grounded; `medium` one body, two quotes, top two rows; `low` two bodies, top three rows.
- **Retrieval** fuses full-text and vector search over wiki pages with the symbol index (keyed on the question's content words) by reciprocal rank. The symbol leg reaches private helpers a page's symbol table omits.

---

## `get_context`

Per target, under `targets`:

| Field | Meaning |
|-------|---------|
| title, summary, symbols | Docs summary and symbols with signatures and lines; a file page's generated `File: <path>` title is not sent |
| `symbols_truncated`, `symbols_total` | Compact card: `{shown, total, hint}` and the full count |
| `hotspot` | Churn flag |
| `parent_page` | The concept page above this target: `title`, `target_path` |
| `freshness` | Only when the page is stale or the file has uncommitted edits: `freshness_status`, `is_stale`, `confidence_score`, `working_tree` |
| `hotspot_files` | Directory card: hotspot files under it, absent at zero |
| `fix_history` | Files with counted bug fixes: `fix_count` over `window` (6 months), age, `bug_magnet`. A cue to call `get_risk` |
| `episodes` | Count of dated records; `get_why` serves them |
| decisions | Titles; with `include=["decisions"]` the lanes `decisions`, `candidates` (max 3), `history` (max 2) |
| `file_preview` | File with no symbols: `lines`, `chars`, and `heading_count` + 3 `headings` (markdown, reST) or 3 lines as `head` |
| `resolved_to`, `note` | A `path::Name` miss answered with the file's card |
| `did_you_mean` | Symbol miss: up to 5 closest ids, in that file or repo-wide |
| `cross_repo` | Workspace: `co_changes_with` in other repos, contract `consumers` / `providers` |
| `*_basis` | Beside an empty `callers`, `callees` or `used_by`: language, resolved call edges, guess share |

Top level: `targets`, `_meta`, `ignored_arguments`, `suspects` when asked, and `dropped_targets` with `recovery` when the budget dropped targets.

- **symbols:** compact cards list the top 15: types (classes, interfaces, structs, traits, enums, type aliases, impls, modules), then functions and methods, then the rest, each by centrality. A budget trim below 15 keeps symbols by kind and name match. `include=["symbols"]` lists all.
- **`symbol_id`:** absent means `path::name`; methods and overloads carry theirs.
- **`fix_history`:** age is measured to the indexed commit.
- **`*_basis`:** an empty list means no resolved edge, not proof of none.

### Context opt-in blocks

| `include` | Adds |
|-----------|------|
| `full_doc` | `content_md`, plus `digest_md` (questions answered, concept index, public API, git signals); dropped first over budget |
| `ownership` | Primary owner, bus factor, contributor count; on a directory, `owners_by_files` (top 3, `owners_total` beyond) and `basis` |
| `last_change`, `community` | Newest change: `date`, `days_ago`, and `author` only when a stored author's own newest commit is that change; community |
| `callers`, `callees` | Call-graph neighbours |
| `references` | Symbol edit set; see [references](#references) |
| `metrics` | PageRank, betweenness, percentiles |
| `decisions` | The three decision lanes |
| `skeleton` / `skeleton+` | Bodies elided; `skeleton+` keeps all non-function code and elides every body. Both asked renders `skeleton+` |
| `health`, `doc_drift` | Health scores and biomarkers; documents naming the file |
| `symbols` | Every symbol of a file |
| `changes`, `provenance`, `suspects`, `files` | See below |

`docs` and `freshness` are always on and legal to pass; `freshness` is sent only on drift.

### references

Every live line naming a symbol target, as `{path, line, kind, text}` with kind `definition`, `import`, `call` or `reference`. An `import` line inside a wrapped import statement adds `from`, the module that statement names. A `complete` list is the whole rename edit set; otherwise `reasons` says why. Three more kinds follow the code sites:

- `reexport`: a barrel that hands the symbol on without naming it (`export * from`, `from module import *`)
- `same_name`: the definition of another symbol with the same name, such as a mirrored constant; listed when at most 5 exist
- `doc`: the first line of each document naming the symbol, with `mentions` counting them when there are more; release notes are skipped

Never complete for:

- languages other than Python, TypeScript and JavaScript (same-package use needs no import)
- over 200 sites, a stale call line, an unreadable file, or an uncommitted edit outside the scanned files
- a rename on import, export or destructure, a default export, or a CommonJS export
- a dynamic use the graph recorded; string-built access is otherwise unseen
- over 50 documents naming it
- over 3 plain `reference` sites in one file of a set over 20 sites: the rest go to `sites_omitted_by_file`, recoverable from `_meta.omitted`. A set of 20 sites or fewer is served whole.

### changes

`recent_changes`: the newest five Change Records touching the file, each `{id, ref, title, merged_at, category, provenance}`; `recent_changes_total` when more exist.

- `ref` is the forge number (`#123`, `!12`), absent for an unnamed change.
- `provenance` is `{label, coverage}`, label `agent`, `human_attested` or `unknown`; absent when stored without a label or attribution is off.
- Only the newest 500 changes are read and a change stores its first 1,000 files, so a file past either limit can be missing.

### provenance

`agent_commits`, `human_attested_commits`, `unknown_commits`, `coverage` (known over all), `agent_share` (agent over known), `summary`.

- Below 30% coverage `agent_share` is null with `suppressed_reason: "coverage_below_floor"`, and the agent and human-attested counts are dropped.
- Unknown never counts as human. Attribution off: `{"unavailable": "attribution_off"}`.

### files

On a directory or module card: every indexed file under it (git rows and graph file nodes), recursively, code files before tests and each group by path, with the test evidence `get_risk` reads for its test gaps.

- `total`, `complete`, `counts` (listed rows per state: `tested`, `untested`, `type_only`, `test`, `not_code`), `rows`, `basis`.
- Over 100 files: the first 100, `complete: false`, `omitted` and a `hint`. A cap cuts test files first; name a subdirectory as a target for the rest.
- A code row is `{path, has_test}` plus, when found, `named_tests` (test files with its file name, nearest directory first; one in another directory is a weaker match), `reached_by` (other tests reaching it through calls or imports) and `covered_by` (per-test coverage rows from `repowise coverage add`), 3 each with `*_total` beyond.
- `has_test: false` means none of the three exists. `type_only: true` marks a file that declares only types and needs no test.
- A test file is `{path, is_test: true}`; a file that is not code is `{path, code: false}`.
- `note` appears when the index holds no test files, so `has_test: false` is not evidence there.
- On a file or symbol target the block is `files_basis: "directory targets only"`.

### suspects

One top-level block: `window` (`since`, `until`), `candidates` (changes merged in the last 14 days), `texts_missing` (unreadable changes, ranked on title and files), `symptom_files` (targets mapped to files at HEAD), `note`, and `suspects`.

- `suspects`: at most 5 rows of `{id, ref, title, merged_at, category, head_sha, files_changed, rank, signals}`, ranked against the targets' files and path words, or the `symptom` text.
- `signals` maps each fired signal (`text`, `graph`, `co-change`, `recency`, `trace`) to its `rank` and `evidence`.
- A reading order, never a cause: no score or probability.

### Component targets

`component:<name>`: declared context (owner, system, criticality, tags) and `facts` over its files: health, hotspots, bus factor, test reach, dead code, each with a `basis` (`measured`, `inferred`, `unavailable`).

- Components come from [`.repowise/components.yaml`](CONFIG.md#repowisecomponentsyaml) or the index.
- Health and dead code skip gated-language files and say how many.
- Only `include=["ownership"]` applies. It adds declared teams with 90-day activity, a count of individual owners, and findings `critical_no_active_owner`, `declared_owner_inactive`, `critical_bus_factor_one`, `catalog_entry_no_code`.
- An owner with no identity in history is `unknown` and raises nothing, so `critical_no_active_owner` never fires when every owner is unknown. Individuals match only by exact git email or forge noreply login.

### Change targets

`change:<id>` (`pr-123` or a commit sha the change landed): `type: "change"`, `id`, `ref`, `title`, `merged_at`, `category`, `shape` (`merge`, `squash`, `commit`), `head_sha`, `commit_count`, `files_changed`, `lines_added`, `lines_deleted`, `files` (first 25, then `files_total` and an omission ref), `linked_issues` when messages close any, `provenance`.

- `provenance` is `{label, agent_commits, human_attested_commits, unknown_commits, coverage}`, or `{"unavailable": "attribution_off"}`, or `{"unavailable": "not_recorded"}` for a change stored with attribution off.
- An unknown id returns `error` naming the id forms; an ambiguous short sha returns `error` with ids as `candidates`.

### Package targets

`package:<name>` (`package:requests`, `package:npm:lodash`): manifest declarations, a summary, components and owner teams, and up to 25 import sites with line, `mapping` (`declared`, `inferred`) and `reachability` (`reachable`, `unreachable`, `test`, `unknown`, with a basis). See [PACKAGE_USAGE.md](PACKAGE_USAGE.md).

### Skeleton

Cut from stored symbol bounds, no parsing at query time: every signature, the imports, and the bodies of top symbols by centrality, hotspot and query match. Elisions carry 1-indexed line ranges, and `fold_hint` gives the `get_symbol('<path>:<start>-<end>')` call that reads one.

A file with no symbol bounds is served whole (`mode: "raw"`). A Markdown or reStructuredText document over the skeleton budget is served as its headings with line numbers (`mode: "outline"`). `_meta.complete` counts only files whose whole source survived the response budget.

### doc_drift

- `references`: documents naming this file that still resolve to it.
- `documents_with_drift`: a listed document has some stale assertion somewhere, not necessarily about this file. Do not merge the two claims.
- `mentions`: live documentation lines naming the file by basename or `folder/stem`, which `references` cannot resolve; `mentions_basis` says to check each.
- `references_basis` states the limits; `references_excluded` counts documents dropped by exclude rules.
- Refusals: `{"unavailable": "not_computed"}` (pass not run), `"index_predates_doc_drift"`, `"drift_read_failed"`.

### Several targets under one budget

Each target gets an equal share; unused share passes on. Over its share, a target drops symbols, then optional blocks largest first (`dropped_blocks`). Miss `suggestions`, `did_you_mean` and ambiguity `candidates` are never shed. If identity cards still do not fit, whole targets go, and the response leads with `dropped_targets` and a narrowed `recovery` call.

---

## `get_symbol`

| Field | Meaning |
|-------|---------|
| source | Up to about 600 lines (200 for a range), line-numbered in `Read` format |
| start and end lines, `kind` | Exact bounds of what was served; `symbol_start_line` / `symbol_end_line` give the whole symbol only when a slice was served |
| `truncated` | Body cut; carries a continuation to pass back as `reference` |
| `ambiguous`, `candidates` | Several matches (overloads, re-exports, conditional definitions); every body returned, none chosen |
| `callee_bodies` | `depth` 2 or 3: each callee's `depth`, source, `verified`; each once, at its shallowest depth |
| `not_rendered` | Bodies past the budget, each with a `fetch_with` range read |
| `outlined`, `members`, `members_total` | Container too large to serve whole |
| fallback lines | On a miss, closest matches from a live grep |
| `source`, `created_at`, `original_tokens` | Omission ref: where the stored content came from |

`outlined: true` puts the header in `source` and `members` (each `symbol_id`, signature, lines; `members_total` when capped) in place of the body, with no `_meta.complete`.

---

## `search_codebase`

| Field | Meaning |
|-------|---------|
| `results` | Ranked symbol, file and concept rows |
| `candidates` | Up to `limit` distinct openable file paths, best first. Read these before a Read. Absent when it would only repeat the `results` paths in order |
| `lines` | Identifier or literal query: `{path, line, kind, text}`, definitions first, max 50 |
| `complete`, `reasons` | Whether `lines` is every match, and why not |

| Row | Fields |
|-----|--------|
| Symbol | `type: "symbol"`, `symbol_id`, `name`, `kind`, `path`, `start_line`, `end_line`, `signature`, `next: "get_symbol"` |
| File | `type: "file"`, `page_id`, `path`, `title`, `next: "get_context"`; an indexed file with no page (config, data, tests) has no `page_id` or `title` |
| Concept | `page_type`, `path` if the page names a file, `symbol_id` on a symbol page, `relevance_score`, `snippet`, `sources` |

Row notes:

- **`path`:** the openable repo-relative file; absent when the row names no file (module group key, onboarding slot, overview), never a page id. There is no `file` key.
- **`page_id`, `title`:** dropped where rebuildable: id `{page_type}:{symbol_id or path}`, title `File: <path>` / `Symbol: <symbol_id>` in the page language. Prose titles stay.
- **`target_path`:** kept only where it differs from `path` (a page's group key, slot or repo name).
- **`symbols`:** on a symbol hit outside `symbol` mode, up to 5 other matches in the file as `name:line`, then `+N more`. On a page row, up to 3 query-matching `name:line` (`Owner.member`).
- **`matched_lines`:** on the first three page rows with a match, up to 3 live `{line, text}` sharing the most query words, code before comments and imports. Never every match.
- **`sources`:** `fts`, `vector`, `symbol`, or a mix; omitted when `["fts"]` and `_meta.semantic_search` is `false`.

`lines` notes:

- The `get_context` [references](#references) of one to three exact symbols, else a live token scan (kind `definition` or `match`).
- Past 3 plain `reference` lines per file in a set over 20 lines, or 50 total, rows go to `lines_omitted_by_file`, recoverable from `_meta.omitted`. A set of 20 lines or fewer is served whole (a multi-word query still stops at 15). Test files sort last.
- A multi-word query caps `lines` at 15 (definitions, calls, imports) and lists the 5 files with most omitted rows; `lines_omitted_by_file_total` counts all.

Ranking:

- Outside `mode="symbol"`, `limit` counts distinct files: one row per file (a file page and its `symbol_spotlight` page included), freed slots go to the next pages, then symbols. `symbol` mode keeps one row per symbol, so overloads in one file each list.
- Symbols rank by exact and qualified name, query-token coverage, then centrality; non-test first unless `kind="test"`. A `symbol_spotlight` id is `file.py::Symbol` with `path` the file.
- Some pages are not files (a `module_page` group key, an `scc_page` hash). `candidates` maps symbol pages to files, merges same-file symbols, skips fileless pages, and backfills.
- A hybrid query drops fileless pages (module, onboarding, overview, decision) without refilling, unless `page_type` or `kind="doc"` asks for pages; concept mode keeps them. Decisions rank below file pages unless the query is why-shaped.

---

## `get_risk`

Per file:

| Field | Meaning |
|-------|---------|
| `defect_profile` | Files with counted fixes: `fix_count` over `window` (6 months), `last_fix_days_ago` (to the indexed commit), `bug_magnet`, `top_symbols` |
| `hotspot_score` | 0 to 1 churn percentile |
| `health_score` | 0 to 10 |
| `dependents_count` | Direct structural dependents; a floor |
| `co_change_partners` | `file_path`, `support` (shared commits, when known), `conf_ab` (share of the target's commits touching the partner, when known), `has_import_link`; by recency-decayed weight. Correlation only |
| `primary_owner` | Always, with the owner clause in `risk_summary` |
| `cross_repo_impact` | Workspace, only when another repo has a concrete link to the file: a contract on either side, or a package import that reaches it (the imported module or subpath, or a file its re-exports forward to). `affected_repos`, the overlay's `status` (and `reason` when not available), and contract consumer and provider rows. Co-change alone is history, not a link: it stays in `cross_repo_links` |
| test gaps, `security_signals` | Coverage gaps and security findings |
| `resolved`, `unresolved_reason` | No indexed file: `unsupported_target_kind` (`module:` id), `directory`, `not_indexed`, `no_such_path`; counts omitted, not zeroed |
| `signal_tier`, `signal_tier_reason` | `"history"` for docs, config, query and other non-code files |

- **`signal_tier`:** the card keeps commit counts, `last_commit_at`, `trend`, owner and `security_signals`, and omits hotspot, co-change, dependents and classification.
- Multi-target calls add `global_hotspots`. Floats have 4 significant digits. Direct rows' `structural_score` is in pagerank-weighted hotspot units, not comparable to `get_change_risk`.

| `include` | Adds |
|-----------|------|
| `owners` | `owner_pct`, `owner_line_pct`, `recent_owner`, `recent_owner_pct`, `bus_factor`, `contributor_count` |
| `changes` | `recent_changes`, `recent_changes_total`, as in [get_context](#changes) |
| `tests` | Typed `test_recommendations` |
| `blast` | `pr_blast_radius`: transitive files, co-change warnings, test gaps, structural score; reviewers stay in the directive |
| `graph` | `dependents` (direct vs transitive), `consumers` (typed contract consumers), `cross_repo_links` (contract rows carry `strategy` and `origin`, see [cross-repo link rows](#cross-repo-link-rows)), `impact_surface`, `direct_risks`, `relationship_analysis` (empty vs unavailable or partial) |
| `churn` | `change_magnitude`, `risk_type`, `change_pattern` |
| `scales` | Unit, range and calibration per scalar; identical every call |

### PR mode directive

With `changed_files`, the response leads with `directive`:

| Field | Meaning |
|-------|---------|
| `may_break` | Production files in reverse-import reach: review candidates, not proven breakage |
| `missing_cochanges` | Co-change partners absent from the change |
| `tests_to_run` | The `repowise impacted-tests` selection, max 10, in [run order](CLI_REFERENCE.md#repowise-impacted-tests-revspec) |
| `tests_to_run_basis` | `measured` (coverage decided a file), `inferred` (graph or rule), `none` |
| `tests_to_run_why` | Per file: changed file and evidence, or `runs with every subset: ...` |
| `tests_run_all`, `tests_run_all_reasons` | No subset can be vouched for; the list is what to run first |
| `tests_broadcast_total`, `tests_import_closure_total` | Tests left out of the list that still run |
| `tests_status`, `tests_status_reason` | Failed selection; names nothing |
| `tests_to_update` | Up to 3 test files to edit, `{path, reason}` |
| `missing_tests`, `files_without_measured_tests` | Changed files with no measured test |
| `coverage` | Which coverage evidence was available |
| `test_recommendations` | `include=["tests"]`: rows with `basis` `measured` (per-test map) or `inferred` (structural) |
| `recommended_reviewers` | Up to 5 primary owners of affected files |
| `reach` | `localized` (<4), `moderate` (4 to <7), `broad` (7+), or `null` |
| `next_calls` | `get_change_risk()`, then `get_context` on the first `may_break` files |

Notes:

- **`tests_broadcast_total`:** run only because a conftest on a route keeps every test under it. **`tests_import_closure_total`:** reached only through imports over one file away, with no call path.
- **`tests_to_update`:** reasons in order `name_pair` (named for a changed file), `imports` (its own code imports one; conftest or setup reach does not count), `co_change`. Only tests the selection runs for a changed file and a runner collects. Edit, not run; a file can be in both lists.
- **`missing_tests`:** absent when coverage is unavailable or stale, since empty would read as no gaps.
- **`coverage`:** `{status, reason}` with no per-test map; with stored line coverage, `reason` is `no_per_test_map_aggregate_available` and `coverage.aggregate` gives file count and freshness. With a map, `coverage_analysis`, `test_analysis`, `test_inference_analysis` say if each source was available, stale, partial or degraded.
- **Measured `test_recommendations`** match a changed file, not its lines (`evidence[].match: "file"`); a test file gone or changed since measurement carries `stale: true` and `stale_reason`.
- **`reach`:** bands an uncalibrated 0 to 10 heuristic that never sees the diff; not a probability. The raw `pr_blast_radius.structural_impact_score` needs `include=["blast", "scales"]`.

In workspace mode the directive adds `will_break_consumers` (structural reach only), `missing_cross_repo_cochanges`, `breaking_changes` (provider incompatibilities since the last index with impacted consumers; a link does not prove field use), `conformance_violations` and `dependency_cycles`; dropped when no workspace is loaded. `cross_repo_relationship_analysis` carries `graph_evidence` and this repo's `http_calls`.

---

## `get_change_risk`

| Field | Meaning |
|-------|---------|
| `directive` | `status` (`review_required`, `review_recommended`, `clear_in_analyzed_scope`, `unknown`): the verdict. Plus headline, reasons, next actions |
| `health_delta` | What the change newly made worse; see [health_delta](#health_delta) |
| `risk_percentile` | Diff size and spread against recent commits. Size, not a verdict |
| `diff_shape` | One sentence on size and spread. The rank is a diff-size statistic: it barely beats churn alone and cannot rank danger |
| `is_fix` | Only when the change is a bug fix |
| `exclude_patterns` | Only when something was excluded: request patterns plus `.riskignore`, less repowise's own output (always excluded) |
| `working_tree` | Only without `revspec`: uncommitted work scored, or clean tree fell back to `HEAD` |
| `fix_history` | Recency-weighted fix record of touched files (no manifests or lockfiles), with `overlap` |
| `impacted_tests` | Tests the change needs |
| `patch_coverage` | Share of changed executable lines the stored coverage ran |
| `branch_overlap` | Other branches editing the same files |
| `independent_changes` | The diff is several unrelated changes |
| `cross_repo` | Workspace: consumers and breaking changes |
| `status` | `"nothing_to_score"` on an empty diff |

- **`next_actions`** names only what the diff needs: inspect a finding at `path:line`, run the selected tests, cover changed lines. Skipped files read in `reasons` and `health_delta.skipped`, a missing test map in `impacted_tests.status`, and coverage measured before the change in `patch_coverage.freshness` with `file_counts.stale_file`.
- **Directive order:** gating findings (high or critical, not performance) lead the reasons, inspect actions and `top_findings`; the count reads "N of M" when others are lower. The headline ends with the code's health on both sides.
- **`include=["scales"]`** adds each field's unit and calibration, and the `review_priority` tercile with `classification` (`Below-typical`, `Typical`, `Above-typical diff size`).
- **`include=["diagnostics"]`** adds `score` (raw 0 to 10), `fallback_band`, `risk_authority`, `score_measures`, `score_unit`, `baseline_sample_size`, `features`, `drivers`, `fix_history` `density` and `percentile`, and `health_delta.analyzer` (rules fingerprint; `context` is `index_dependents` when centrality gates read the index's dependents counts on both sides, `changed_files` with no index), `.base`, `.head` (resolved revisions), `.limits`.
- **`include=["findings"]`** lists every finding with its expand call as `inspect`. `evidence` adds the evidence checklist; `tests` adds `impacted_tests.why` and `basis_by_file`.
- **`per_commit=true`** (a range only) replaces the review with `commits`: one row per non-merge commit, oldest first, up to 30, each with `sha`, `subject`, `files`, `lines`, `risk_percentile` (or `fallback_band`), up to two `drivers` that raised it, `fix_prone` files and `is_fix`. Every row ranks against the same history (`ranked_against`, read at the range's merge-base), so a row can differ slightly from that commit scored alone. `review` names the full-review calls, `merges_skipped` counts merge commits, and `more` carries the call for the commits past the cap (on a range with merged branches it can repeat a few rows, and it ranks against its own start). `include=["diagnostics"]` adds each row's `score`.

### health_delta

Both revisions are analysed from their own content; a head finding is reported only when the diff explains it.

| Field | Meaning |
|-------|---------|
| `scope` | Changed, eligible, analysed, skipped, failed counts |
| `status` | `available`, `partial` or `unavailable`; `partial` is never a clean bill |
| `introduced`, `worsened`, `resolved` | Totals |
| `top_findings`, `findings_total` | Three most actionable, and the count |
| `files`, `files_total` | Up to 10 files, largest structure move first |
| `score_basis`, `score_change` | `structure_only` (code shape of the changed bytes, no coverage); nloc-weighted score change |

Skips:

- Docs and config (Markdown, JSON, YAML, TOML and similar): `not_code`, never `partial`. A change of only these reads `clear_in_analyzed_scope` with a headline saying health does not cover them.
- Tree-sitter queries (`.scm` under `queries`) and templates (`.j2`, `.jinja`, `.jinja2`, `.tmpl`, `.tpl`, `.hbs`, `.mustache`, `.ejs`, `.njk`): `not_analyzable_data`, never `partial`; alone they read `clear_in_analyzed_scope` with their own headline, since they can change behaviour.
- HTML, schema and other files with no health dialect make a run `partial`.

Findings name `dimension`, `biomarker`, `severity`, `path`, `symbol`, head-side `lines`, `reason` and `attribution` (`basis` `added_lines`, `changed_symbol`, `changed_call_edge`, `new_file`, `file_change`, `context_change` or `unknown`, plus `confidence`).

- Identity ignores line numbers, so moved code introduces nothing. Ids are bound to the two revisions; pass one as `finding_id` to expand it.
- A match to a stored finding carries `health_reference` for `get_health(finding_id=...)`. Performance findings carry `opportunity_id`, ordered by opportunity rank.

Files rows: `base_score`, `head_score`, `nloc_base`, `nloc_head` (null on a missing side); `base_structure`/`head_structure` with diagnostics. Each score is 10 minus the structure deduction of the changed files' bytes, from passes reading no history or coverage. It can differ from `get_health(counts="code_shape")`, whose structure half also counts coverage markers and clones with unchanged files. A deleted file has a base-only row, with `moved_to` when split.

`score_change` (base-nloc weighted, so a growing improved file keeps its gain): `base`, `head`, `delta` (two decimals), `structure_delta` (moves even at the 1.0 floor), `files_compared`.

### impacted_tests

The `repowise impacted-tests` selection over every touched path, ignoring the score's filters, less repowise output (the index, and managed files changed only in repowise's block), which the CLI reads when tracked.

| Field | Meaning |
|-------|---------|
| `status` | `selected`, `run_all`, `degraded`, or a nothing-selected reason |
| `run_all`, `reasons`, `reasons_total` | No subset can be vouched for; up to 3 reasons |
| `tests_to_run`, `total`, `truncated` | Max 10, in [run order](CLI_REFERENCE.md#repowise-impacted-tests-revspec); `always_run_total` tests last |
| `broadcast_total`, `import_closure_total` | Left out of the list and `total`, still run (as in `get_risk`) |
| `tests_to_run_kind` | `test_id` when a coverage node id is listed, else `test_file` |
| `basis` | `measured`, `inferred` or `none`, as in `get_risk` |
| `why`, `basis_by_file` | `include=["tests"]`: per-file reason and evidence |
| `map_present`, `line_coverage` | With a coverage map |

- **Nothing-selected statuses:** `no_index`, `unknown`, `config_invalid`, `timeout` (over 30 seconds), `busy` (another selection running), `no_source_line_changes`; each sets `run_all`, and `repowise impacted-tests` gives the answer.
- **`degraded`:** the index predates files changed outside the change and the selection holds over half the tests. The list stands, `summary` says so, and the directive asks for every test.
- With `run_all` the list is what to run first. When most of the suite runs anyway, the tail is not stored for recovery.
- **`basis_by_file`** values: `coverage`, `changed-test`, `call-graph`, `import-graph`, `conftest`, `full-run`, `no-tests-needed`, ...
- **`line_coverage`** buckets changed lines (matched on the side the map was measured at, else by file): `untested_changes`, `stale_test_candidates` (covered lines whose test is absent from the diff), `covered`, `no_coverage_data`. Build the map with `pytest --cov=<package> --cov-context=test` (pytest-cov) and `repowise coverage add`.

### patch_coverage

Present when coverage is stored. With a gate set and coverage measured at head, it is the computation and JSON `repowise coverage check --format json` gates on. Otherwise: `status` (`measured`, `not_measured`), `freshness`, nonzero `file_counts`, and totals and `files` when a changed file was measured; diagnostics keep the full shape.

- `patch_coverage_pct` is null when no changed line is executable. Unnamed files read `not_in_report`.
- `scope.freshness` `stale`: measured at another commit; files changed since read `stale_file` with no figures.
- `path_gates` judges the path-scoped gates in `coverage.gates`.
- File rows carry `risk` (`fix_pressure`, `dependents`, `hotspot`, `bug_magnet`, `risky`, `reasons`, `basis`) and up to 8 `hints` per uncovered range: `range`, `symbol`, up to 3 `tests` to extend, `basis` `per_test` (measured), `call_graph` or `import_graph` (inferred).
- Without `revspec` it covers what a push would bring, from the merge-base with the default branch.

### fix_history

- `is_fix` is a keyword rule on the subject, not the conventional-commit type.
- `overlap` (needs an index), per file: `fix_count`, `overlapping_lines` (`approximate`: past ranges use their own parent, so read "this area was patched before"), `last_fix_days_ago`, `changed_lines`, `share_of_change`.
- `concentration` names a file holding at least half the changed lines.
- Never names the commit that introduced a bug; absent without fix history.

### branch_overlap

Git-only, no index needed. `branches[]`: `ahead`, `behind`, `last_commit`, `files[]`, each with a `basis` (`same file` or a co-change pair with its `partner`). Scans the newest 50 branches; `scanned`, `total`, `truncated` describe the scan. Absent when no branch shares a file, or on scan timeout.

### independent_changes

Groups changed files over index edges, co-change pairs and (for a range) shared commits; docs, config, data and tests are never grouped. `groups[]` has `files` and `bridging_files`; `ungrouped_files` and `summary` say what was left out and why; `basis` says what was checked. Absent when the diff is one change.

### cross_repo

- `consumers[]`: `provider_file`, consumer `repo`, `file`, `contract_id`, `contract_type`, `match_type`, `strategy`, `origin`, and `tests` (`state` `measured`, `inferred`, `none` or `unresolved`; up to 5 `tests_to_run`).
- `breaking_changes[]`: `contract_id`, `kind`, `severity`, `detail`, `impacted_repos`. `breaking_changes_available` says whether detection ran, so empty is not an all-clear.
- `graph_evidence` and `http_calls` for the changed repo, as in [workspace tools](#workspace-tools).
- Absent outside workspace mode or when no published file changed.

---

## `get_why`

| Field | Meaning |
|-------|---------|
| `decisions` | Accepted in a recorded event. Treat as constraints |
| `candidates` | Inferred, never accepted: hints, not rules (`candidates_note`). Max 3 |
| `history` | Accepted, then withdrawn. Max 2 |
| `answer_basis` | Strongest lane: `decision`, `episode`, `rationale`, `archaeology`, `documentation`, `candidate`. Only `decision` rules |
| `authority` | Per row: `accepted` or `candidate` |
| `currency` | Accepted rows: `active`, `needs_review` (files moved, still binds), `uncheckable` (names nothing), `superseded`, `dismissed`. Candidates: `review_state: "open"` |
| `alignment` | Path mode: `governing_count` (accepted records still binding), `active_count`, `deprecated_count`, `uncheckable_count`, `candidate_count` (the last four sum to the records naming the path); `score` from `active_count` |
| `git_archaeology` | No decision governs the path; `git_log` keeps its 5 newest rows, rest via the omission marker |
| `provenance`, `evidence_refs` | Self-contained evidence refs. Matching ids mean shared evidence, not corroboration |

- **A question no decision answers** (no top-level decision, no rule or candidate on a named file) gets a compact verdict: `reason`, at most 3 `code_rationale` rows (`path`, `lines`, `comment`), at most 2 `key_commits` (`sha`, `date`, `message`, `pr_number`; commits the question singled out first, then the file's key commits carrying its terms), and `more`, the `get_why(targets=[...])` call for the full file history. `repowise why --full` keeps the whole card.
- Commit rows under `git_archaeology` and `origin_story.key_commits` inherit the block's `provenance`.
- Read the lane, not `status`: `status: "active"` can lack acceptance. Only `repowise decision confirm` or a committed ADR marked accepted accepts.
- `candidates` and `history` shed first under budget, so an absent lane may mean a tight budget.
- The dashboard (no arguments): stale decisions, conflicts, ungoverned hotspots, `retired_decisions`, `unscoped_decisions`, five rows each, sizes in `counts`.

---

## `get_overview`

| Field | Meaning |
|-------|---------|
| `title`, `content_md` | Summary section; full essay with `include=["content"]` |
| `key_modules` | Up to 12 topmost module pages in outline order: name, path, section, `package`, `pages` |
| `entry_points` | Detected entry points, build and CI tooling set aside, grouped by package |
| `architecture` | Layers in stack order (names, file counts, `root`), no test layer, plus `dependencies` |
| `commands` | Build, test, lint, dev, format, typecheck from manifests: `"."` plus packages naming a task the root lacks |
| `code_health` | Score, band, `production_average`, hotspot health, worst file, open findings, `code_files`, `files_scored`, `files_by_band` |
| `git_health` | Hotspot count, churn trend, `riskiest_files` (5: bug magnets, then fix mass, then churn) |
| `next_actions` | First 3 actions for the week (or quarter): `id`, `tier`, `title`, `impact`, `target`, plus each horizon's `total` |
| `more`, `workspace` | Opt-in block names; workspace footer |

- **`dependencies`:** the ten heaviest package edges (`from`, `to`, `verb`, `weight`), `edges_total` when more; absent in a single-package repo.
- **`code_health`:** `production_average` when tests, tooling, vendored or generated files are in the reading; `files_scored` when some files have no score.
- **`next_actions`:** `include=["actions"]` serves both horizons with `done_when` and `by_tier`; `get_health(fix_id="act_...")` opens one. `unavailable` names inputs the list was built without and `unavailable_reasons` gives each one's reason; both are absent when every input was read.
- **`repo="all"`:** each repo has `summary` (essay's first sentence), `code_files` (its `code_health.code_files`, or graph code files before health runs) and `symbol_count`; `total_code_files` sums them.

Opt-in blocks, computed only when named: `outline` (page tree, two levels), `tour` (`guided_tour`, `reading_order`), `decisions` (`key_decisions`), `graph` (`community_summary`), `ownership` (`knowledge_map`), `components`, `changes`, `actions`, `tools` (`tool_surface`), `scope` (whole `_meta.index_scope`).

**changes:** Change Records in a 90-day `window` (`since`, `until`) ending at the newest merge: `total`, `by_category` (largest first), `by_week` (`{week, changes}`, Monday weeks, zeros included), and `provenance` (`agent_changes`, `human_attested_changes`, `unknown_changes`, `coverage`, `agent_share`, `summary`).

- Below 30% coverage `agent_share` is null with `suppressed_reason` and agent and human-attested counts are dropped. Unlabelled changes count as unknown.
- `{"total": 0}` with no records; `provenance` is `{"unavailable": "attribution_off"}` with attribution off. Never grouped by person.

---

## `get_health`

### Modes and identity

| `mode` | When |
|--------|------|
| `dashboard` | No targets |
| `targets` | With `targets` |
| `fix_item`, `action` | `fix_id` `fix1_...`; `act_...` (the Do next action, with `details`, `commands`) |
| `finding`, `refactoring_plan`, `refactoring_opportunity`, `performance_opportunity` | An id lookup |
| `refactoring_evidence`, `performance_evidence` | Paging an opportunity's evidence |
| `conflict` | Two selectors passed |

- `unresolved` reasons: `not_indexed`, `no_such_path` (with `removed_by_commit` and `moved_to` when git explains it), `excluded`, `not_measured`, `no_such_module` (with `known_modules`).
- `mode`, `_meta`, `unresolved`, `known_modules` and every kept list's `*_total` survive `only`. `scope` and `counts` are echoed.
- `_meta.health_analysis`: stored analysis, not recomputed; `status` `available`, `provenance_unknown` (no row recorded the commit) or `unavailable`. `_meta.health_analyzed_at` / `_commit` date the pass, which can lag indexing; `_meta.health_analyzed_commits_distinct` when rows span passes.

### fix_first

One ranked queue across refactoring, performance and code-shape work, the same object `repowise health` renders.

| Field | Meaning |
|-------|---------|
| `lead` | The queue's first item on any page, with `next_call` |
| `items` | Up to 5: `id`, `tier`, `kind`, `title`, `target`, `why`, `gain`, `effort`, `confidence` |
| `items_total`, `cursor` | Eligible items, and the page start |
| `counts` | The funnel; see below |
| `tier` | `now` (safe to start), `next` (needs judgment), `later` |
| `kind` | `refactor` (one file's composed refactoring), `perf_fix` (one intervention and its sinks), `finding` (code-shape, no plan) |
| `totals` | `candidates`, `eligible`, `shown`, `dormant` (functions kept out as `gated_off`) |
| `by_improves`, `detail_call` | Rollup; call that opens the lead |

- **Paging:** with `only=["fix_first"]`, `items` pages from `cursor` (`recovery.fix_first` names the next page) and `limit` sets the size, up to 25.
- **Due items:** a `now`/`next` lead carries `first_step` (`action`, `line`, `file` if not the target's) and `verify` (`command`, or `prerequisite` when no test reaches it); with `only=["fix_first"]`, so do due items among the page's first five. `get_health(fix_id=...)` returns one in full.
- **`counts`:** `inventory` (all units), `in_scope` (minus `test`, `tooling`, `generated`, `vendored`, `docs_example`), `eligible`, `due` (`now` or `next`), `shown`, `excluded` (non-zero, by reason: `gated_off` for a same-file constant flag switching it off, `unreachable` for sure dead code, `history_only`), `gated` (counted in `inventory` only; always 0, since Fix first applies no gate).
- Lists named in `only` carry the same count object: `top_findings_counts`, `performance_opportunities_counts`, `refactoring_plans_counts`, matching the CLI and dashboard.
- `top_findings_counts` judges scoring findings as queue units (not performance or zero-impact); `top_findings` ranks every open production finding, so `top_findings_total` can exceed `in_scope` and `shown` can exceed `eligible`.
- Ordered by value, then tier. History markers (churn, ownership, co-change, prior fixes) never create or lead an item; they ride under `context`. Always production code, whatever `scope`; `limit` never changes the lead. Analysed commit: `_meta.health_analysis`.

### Dashboard and metric rows

| Field | Meaning |
|-------|---------|
| `kpis` | Averages and worst-file pointers |
| `kpis.band`, `kpis.analysed_share` | Band (`null` under half analysed); scored share when some left out |
| `unanalysed_file_count` | Files with no health dialect, unscored and excluded; absent when none |
| `gap_analysis` | Recovery to 8.0: points, files below, `files_to_reach_target`, `files_for_half_gap`, `weighted_gap_points`, `weighted_gross_gap_points` |
| `worst_files`, `test_worst_files` | Lowest raw scores, production and test |
| `high_leverage_files` | By `weighted_deficit`, with `share_of_repo_gap_pct` |
| `top_findings` / `findings`, `test_findings` | Production and test; totals sum to the open set |
| metric row | One file's scores and deductions |

- **`kpis`:** hotspot health, `average_health` (NLOC-weighted), `average_health_unweighted`, `average_health_weighting`, pillar averages, `production_average` (the files `worst_performer_path` is picked from, while tests, tooling, vendored or generated files are in the average), `worst_performer_path`, `worst_test_path`, `performance_causes` (`now`, `production`, `all`, `per_10k_loc`; see [PERFORMANCE.md](../layers/PERFORMANCE.md#the-default-queue)). `get_overview` reports bands the same way.
- **Metric row:** `score` (defect, headline), `maintainability_score`, `performance_score`, `weighted_deficit` = (8 - score) x NLOC, `is_test`, `has_test_file`, `primary_biomarker`, `total_deduction`, `structure_deduction`, `history_deduction`, `withheld_deduction`, `history: "short"` (under 5 commits, so most history markers cannot fire).
- Score = 10 minus structure and history deductions, floored at 1.0; history is 0.0 under `counts="code_shape"`. Deficit and gap fields share score points x NLOC and compare only with each other.
- `primary_biomarker` prefers a discrete finding over `coverage_gradient`. `details.actions` (agent prompt, silencing rule) appear only with `finding_id=...`.
- Each finding has a `dimension`. `advisory` findings (`assertion_free_test`, `mock_saturated_test`, `hidden_coupling`, `brain_method`, `untested_complexity`) never deduct and need `include=["advisory"]`. `mode: "targets"` is not split by test.

### Held-back findings

- **Provisional types:** only with `include=["unverified"]`, marked `verification: "unverified"`.
- **Withheld types:** never listed but still deduct; rows show `withheld_deduction` and `_meta.withheld` names type, count and reason.
- **Language gates:** per layer and language, gated, preview or ungated (no row), in `finding_registry.py`. Gated rows are stored, addressable by id, counted in `_meta.gated`, listed with `include=["unverified"]`. Preview rows are served; `_meta.preview` gives `{layer: {language: note}}` with the measurement (e.g. `preview, measured 22% precision on 55 judged plans from 2 Java repos, 2026-10-15`).
- Nothing is gated. Health is ungated on Java, C#, C and preview on C++; refactoring, performance and dead code are preview on Java, C#, C, C++ (dead code also Kotlin, Swift).
- **Vendored and generated files:** findings held back in every language (changed upstream or by a generator), counted in `_meta.gated.health` under `vendored` and `generated`; `_meta.gated.reasons` explains once when nothing is listed. Scores, KPIs and worst-file order still count them.
- **History markers** (ownership, churn, change entropy, co-change, other git kinds): on tests, held back under `test_history` (code-shape findings still list); elsewhere, held from finding lists under `history`, though they order hotspots and Fix first and `get_risk` lists them.
- **`findings_hidden`** (targeted mode): one line beside `findings` when a file or row hold kept findings off it, with their count by hold and the points they still deduct. Absent when nothing is held.

### Opt-in blocks

| `include` | Adds |
|-----------|------|
| `biomarkers` | Findings in dashboard mode |
| `refactoring` | `refactoring_opportunities`, `suggestion_legend` |
| `trend` | Snapshot diff; declining or predicted-decline alerts |
| `coverage` | Coverage rows and summary; see [Coverage](#coverage) |
| `accuracy` | `defect_accuracy`: precision@K of least-healthy files recently bug-fixed vs base rate, `lift`; `null` with little history |
| `signals` | Targeted: prior defects, change scatter, 90-day churn, owners, graph degree; `null` per missing field |
| `churn_complexity` | Recently changed files: commits, max CCN, NLOC, score, churn percentile |
| `untested_complexity` | Functions by CRAP: `crap`, `ccn`, `coverage_basis`, and `coverage_pct` (measured) or `coverage_tier` (`direct`, `reached`, `none`) |
| `doc_drift` | `findings` (document, line, wrong `target`, `reason`, `kind`, `confidence`, `suggestion`), `findings_total`, `documents`, `basis` |
| `semantics` | `_meta.health_semantics`: legend for deficit points and percentiles |
| `what_if` | Up to 10 worst files (or targets) with `ceiling` and fixes |
| `unverified`, `recipe` | Provisional and gated findings; `plan.recipe` with `plan_id` |
| `performance`, `defect`, `maintainability`, `advisory` | One dimension; `performance` adds the queue |

- **`untested_complexity`:** estimated tiers only with `include=["unverified"]`; never both measures.
- **`doc_drift`:** evidence to check, not a proven defect.
- **`what_if`:** `ceiling` is the score with no structure deduction (history's cap shrinking with it); `target_out_of_reach` when below 8.0; `fix_all_structure` gains, top 3 `findings`, and `top_fixes` across files. Each fix has `points` (upper bound when its category is capped) and `gain` (`file`, `average`, `hotspot`). `structure_deduction` may include withheld types no fix covers.
- **`only` aliases:** `biomarkers` -> `findings`, `accuracy` -> `defect_accuracy`, `refactoring` -> `refactoring_plans`. Dimension names land in `unknown_only_keys` with `unknown_only_keys_hint`, which names the `include` a missing include-gated key needs; for targeted `signals`, name `metrics`.

### Refactoring

`refactoring_opportunities`: one unit per file with `lead_biomarker`, `addresses_primary_problem` (`true`, `false`, `null` without a dominant finding), ordered `steps` (`mechanical` or `judgment`) and evidence counts.

- Without targets it lists what Fix first takes (`refactoring_scope: fix_first`); `refactoring_opportunities_hidden` counts the rest by reason, `refactoring_opportunities_scope` names the scope.
- A step with `relocated_by` names a symbol an earlier step moves: locate it again.
- `refactoring_summary` rolls up by type, effort, confidence and lifecycle.

- `opportunity_id="refop..."` returns full steps, member plans, validation profile, `next_actions`, `found`, and `status` (`open`, `acknowledged`, `resolved`, `false_positive`). `only=["refactoring_evidence"]` + `cursor` pages evidence.
- `refactoring_plans` (only when named in `only`): the queue's plans in order, steps together, same scope, filters and view, up to 25 a page. Row: `id` (for `plan_id`, `generate_refactoring_code`), `refactoring_type`, `file_path`, `target_symbol`, `line_start`, `line_end`, `effort_bucket`, `confidence`, `impact_delta`, `source_biomarker`, `opportunity_id`, `classification`, `relocated_by`, and `relocates` on a Split File plan (ids it stands for).
- `refactoring_plans_total` and the cursor count plans, so a Split File page can show fewer rows. Also `refactoring_plans_opportunities_total`, `refactoring_plans_scope`, `refactoring_plans_hidden` (by reason). Empty: `refactoring_plans_status.reason` `no_applicable_findings`, `plan_analysis_indeterminate`, `no_eligible_targets` or `analysis_unavailable`.
- `plan_id=...` returns the full plan: detector detail (cohesion `groups`, `cut_edges`), `evidence`, `blast_radius`, validation, and `payoff` when resolved. Performance plans live in `performance_opportunities`. See [REFACTORING.md](../layers/REFACTORING.md).

### Performance

Findings carry `health_impact: 0` and never blend into the score. `perf_rank` is an integer ordering key weighing the marker (superlinear > per-iteration crossings > in-loop CPU), the boundary (`details.boundary_kind`: subprocess, network or database, lock, filesystem) and `details.cross_function`.

- Bare `get_health()` ranks them in `fix_first` as `perf_fix`. `opportunity_id="perf..."` adds `plan_steps`, `validation`, `siblings`; `only=["performance_evidence"]` + `cursor` pages evidence; rows carry a public `finding_id`.
- Ids are stable per model version; older ones resolve to `model_state.state: "stale_model"` with `refresh_required`. `lifecycle_status` is `open` or `resolved`.
- `performance_opportunities` holds `cost_proof` `proven` causes. `unproven` (loop size unmeasured) never sets `may_lead` and no list shows it; counted in `performance_summary.default_queue.excluded.unmeasured_cost`, `performance_summary.proof` and the `proof` facet. REST lists them with `proof=unproven` on `/api/repos/{id}/health/performance-opportunities`.
- The `growth_source` facet (`loop`, `registry`, `cold_path`, `none`, `n/a`) names the loop showing growth. `fix_first`, Do next and `performance_summary.lead` take only `plan_ready` causes with `loop` (or per-call causes an entry point reaches); `fix_first.totals.excluded.unconfirmed_cost` counts the rest.

### Coverage

Per-file `covered_line_count`, `total_coverable_lines` (targeted adds `covered_lines`) and a `summary`.

- `freshness.status`: `current` (at the indexed commit), `stale`, `unknown`. `source_formats` lists merged formats.
- `report_paths`: ingest mapping `total`, `matched`, `unmatched`, `ambiguous`, `unmatched_sample`.
- Dashboard `history`: one point per complete ingest, newest 10.
- Targeted `decay`: `confirmed_lines`, `invalidated_lines` (unknown, not uncovered), `drifted` once a fifth has moved.

---

## `get_dead_code`

| Field | Meaning |
|-------|---------|
| `tiers` | `high` (0.7+), `medium` (0.4 to 0.7), `low`: path, kind, confidence, lines, cleanup impact, stable `id` |
| `summary` | Totals over all open findings; `filtered_findings`, `filters` (non-default), `scope` |
| `summary.withheld_types` | Provisional types; ask with `kind=` to see them, marked unverified |
| `summary.gated` | Gated-language findings per language; `include_unverified=true` shows them |
| `summary.call_resolution_basis` | Per language, resolved call edges and guess share |
| `by_directory`, `by_owner` | Rollups with `group_by` |
| `limit_note` | A `limit` over 25 was clamped |

- No language is gated; dead code on Java, C#, C, C++, Kotlin and Swift is preview, labelled in `_meta.preview`.
- In workspace mode a finding another repo may use is never `safe_to_delete`, and a contract link to its file also lowers its confidence.
- The 0.7 and 0.4 floors are shared by the CLI, web UI and this tool. See [DEAD_CODE.md](../layers/DEAD_CODE.md).

---

## `list_repos`

| Field | Meaning |
|-------|---------|
| `workspace`, `workspace_root`, `default_repo` | Mode, root, default alias |
| `repos` | `alias`, `path` (config-relative), `absolute_path`, `is_default`; workspace adds `indexed_at`, `last_commit_at_index` |
| `hint` | How to use `repo` here |

Any of `alias`, `path`, `absolute_path` passes unchanged as `repo`. Single-repo mode: `workspace: false`, `workspace_root: null`, `default_repo: "default"`.

---

## Workspace tools

### `get_blast_radius`

Impacted services with `score` (0 to 1 relative path weight, uncalibrated), `distance` (hops), `structural` (real dependency vs co-change only) and edge kinds; `impact_score_semantics`, `impacted_repos`, `structural_count`, `behavioral_count`, `total_impacted`, unresolved targets. `symbol_targets[].consumers[]` names consuming symbols per contract link, with `strategy` and `origin`, each with a `get_change_risk`-shaped `tests` block. `evidence` is the graph's evidence state.

### `get_architecture`

`score` (1 to 10; higher is lower coupling, smaller core), `architecture_type` (`core-periphery`, `hierarchical`), `propagation_cost_pct` (share of other services the average service reaches), `core_size`, `core_ratio`, `core_members`, `cycle_count`, `cycle_count_capped` (true when the enumeration budget stopped the count, so `cycle_count` is a floor), `conformance_violations`, `role_breakdown` (Core, Shared, Control, Peripheral), `evidence`, `http_calls`, `summary`.

### Cross-repo link rows

A `package_dependency` row appears only when the dependent's imports reach the file; `granularity` is `file` when the workspace recorded which files they reach, `repository` on an overlay written before that (every file inside the package directory counts). A contract link row carries `strategy`, the rule that matched it (`exact_id`, `unique_path`, `gateway_route`, `host_only` and the rest of `LinkStrategy`), and `origin`, how the contract at the row's far end was read (`route_registration`, `client_call`, `sql_string` and the rest of `ContractOrigin`). Both are omitted on an artifact written before they were stored. `match_type` derives from `strategy`, so a `unique_path` or `gateway_route` link still reads `exact` there. Values are in [WORKSPACE_CONTRACTS.md](WORKSPACE_CONTRACTS.md).

`graph_evidence` is `{state, reason}`, present only when the state is `partial` or `insufficient`; `reason` lists the evidence reason codes (`isolated_services`, `repos_without_contracts` and the rest of `graph_evidence`). `http_calls` counts HTTP calls `read`, `not_linked` and `in_service`, workspace wide on `get_architecture` (which adds `linked`) and for the changed repo on `get_risk` and `get_change_risk`; calls served inside their own service are counted apart, never as not linked. The counts are separate figures, not a partition of `read`.

### `get_conformance`

`violations` (`source`, `target`, `rule_source` / `rule_target`, `edge_kind`), `cycles` (`nodes`, `length`), `violation_count`, `cycle_count`, `total_cycles`, `total_capped` (true when `total_cycles` is "at least"), `rules_evaluated`. A report that never ran says so, not zero counts.

### `get_dependency_path`

The path, or else nearest common ancestors, shared neighbours, community analysis and bridge suggestions.

### `get_execution_flows`

Scored entry points, each with a breadth-first call trace and whether it crosses community boundaries.

### `generate_refactoring_code`

`suggestion_id`, `plan`, generated code and unified diff; or `generation: {available: false, reason: "disabled"}`; or `error: "no_provider"`.

### `set_finding_status`

The new status and its timestamp.
