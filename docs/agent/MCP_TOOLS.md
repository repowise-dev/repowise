# MCP Tools Reference

repowise serves its codebase intelligence to AI coding assistants (Claude Code, Codex, Cursor, Cline, Windsurf and any other [Model Context Protocol](https://modelcontextprotocol.io) client) as a set of MCP tools. The tools answer questions from the index: the dependency graph, git history, generated docs, decision records, health and dead-code analysis. None of them edits your code. Two opt-in tools go further: `set_finding_status` records a triage verdict in the index, and `generate_refactoring_code` calls your configured LLM to draft a diff.

18 tools are registered in total. A single-repo server advertises 10 by default: exactly the canonical tools. Workspace mode adds the `list_repos` discovery utility, for 11. 7 specialist tools are opt-in where eligible.

This page tells you which tool to call and with what arguments. Every response field, the full `_meta` envelope and the truncation rules are in [MCP_RESPONSE_FIELDS.md](../reference/MCP_RESPONSE_FIELDS.md).

## Contents

- [Starting the server](#starting-the-server)
- [Configuring the tool surface](#configuring-the-tool-surface)
- [Reading a response](#reading-a-response)
- [Which tool for which question](#which-tool-for-which-question)
- [Workspace mode](#workspace-mode)

**Canonical tools (default in both modes, 10)**
[get_answer](#get_answer) &middot; [get_context](#get_context) &middot; [get_symbol](#get_symbol) &middot; [search_codebase](#search_codebase) &middot; [get_risk](#get_risk) &middot; [get_change_risk](#get_change_risk) &middot; [get_why](#get_why) &middot; [get_overview](#get_overview) &middot; [get_health](#get_health) &middot; [get_dead_code](#get_dead_code)

**Workspace discovery utility (default in workspace mode, 1)**
[list_repos](#list_repos)

**Opt-in specialists (7; workspace eligibility still applies)**
[get_dependency_path](#get_dependency_path) &middot; [get_execution_flows](#get_execution_flows) &middot; [generate_refactoring_code](#generate_refactoring_code) &middot; [set_finding_status](#set_finding_status) &middot; [get_blast_radius](#get_blast_radius) &middot; [get_architecture](#get_architecture) &middot; [get_conformance](#get_conformance)


---

## Starting the server

```bash
repowise mcp                              # stdio, for Claude Code, Codex, Cursor
repowise mcp --transport streamable-http  # HTTP on port 7338
repowise mcp --transport sse --port 7338  # legacy SSE transport
```

`repowise init` registers the server with Claude Code and installs its hooks; `repowise init --codex` writes project-local Codex MCP config and hooks. Pass `--no-editor-setup` (or set `REPOWISE_SKIP_EDITOR_SETUP=1`) for a scratch clone, worktree or CI run you do not want registered. Each Claude config holds a single `repowise` entry, so indexing a second repo repoints it; `init` prints a notice before it does.

---

## Configuring the tool surface

| Tool | Default | Mode |
|------|---------|------|
| `get_answer`, `get_context`, `get_symbol`, `search_codebase`, `get_risk`, `get_change_risk`, `get_why`, `get_overview`, `get_health`, `get_dead_code` | on | single-repo and workspace |
| `list_repos` | on | workspace only |
| `get_dependency_path`, `get_execution_flows`, `generate_refactoring_code`, `set_finding_status` | opt-in | single-repo and workspace |
| `get_blast_radius`, `get_architecture`, `get_conformance` | opt-in | workspace only |

Change the surface in `.repowise/config.yaml` under `mcp.tools`:

```yaml
mcp:
  tools: ["+get_execution_flows", "-get_dead_code"]   # adjust the default set
# tools: ["get_answer", "get_context"]                 # explicit allowlist
# tools: all                                           # every tool usable in this mode
# tools: lean                                          # the agent-lean profile
```

Or per launch, which overrides the config block:

```bash
repowise mcp --tools "+get_execution_flows"   # default set plus one
repowise mcp --tools "get_answer,get_context"  # explicit allowlist
repowise mcp --tools lean                      # agent-lean profile
repowise mcp --all                             # every tool usable in this mode
```

The dashboard Settings page has a per-repo toggle for each tool and writes the same `mcp.tools` block.

- Tokens prefixed `+` or `-` adjust the default set. A list without prefixes is an allowlist.
- Unknown names are ignored with a warning. A workspace-only tool named outside workspace mode is ignored too: it has no workspace graph to read.
- Raw MCP clients see the surface resolved when the server started. Restart the server after changing it.

- **Default (single-repo):** 10 tools, exactly the canonical intelligence set.
- **Default (workspace):** those 10 plus `list_repos`, the workspace discovery utility.

**The `lean` profile** is `get_answer`, `get_context`, `get_symbol`, `search_codebase`, `get_risk` and `get_why`, plus `list_repos` in workspace mode. It is small enough to keep every schema loaded, so when a repo sets `mcp.tools: lean`, `repowise init` skips the Claude Code tool-search recommendation.

See [CONFIG.md](../reference/CONFIG.md#the-mcp-block) for the config block itself.

---

## Reading a response

Every tool returns a JSON object with a `_meta` envelope. Most fields appear only when they carry a signal. An agent should check these:

| Field | What to do with it |
|-------|--------------------|
| `stale_warning` | Present only when the index is behind in a way that changed served files, or a served file has uncommitted edits (`working_tree_dirty` counts them; run `repowise update --working-tree`). Run `repowise update` before trusting file-level detail. Its absence means current. |
| `indexed_commit`, `live_head`, `index_behind` | Which commit the answer describes and whether HEAD has moved since. |
| `complete` | Symbol bodies or whole files served verified against the live file. Do not re-read them. |
| `state` | `degraded`, `partial` or `truncated` when something fired, with reasons. A degraded empty result is a failed read, not an empty repository. |
| `omitted` | Refs to content cut for size. Restore with `repowise expand <ref>` or `get_symbol("repowise#<ref>")`. |
| `response_budget` | The character ceiling that applied and the size delivered. Present when the budget cut something. |

`get_answer` adds a top-level `confidence` (rates the prose) and `retrieval_quality` (rates the evidence). Diagnostics such as `timing_ms` and `contract_version` ride only on `get_overview`; `REPOWISE_MCP_DEBUG_META=1` restores them everywhere. Full envelope: [MCP_RESPONSE_FIELDS.md](../reference/MCP_RESPONSE_FIELDS.md#the-_meta-envelope).

### Reversible truncation: `_meta.omitted`

Responses fit 24,000 serialized characters by default and 32,000 when the call passes an expansion argument (a nonempty `include`, for most tools). Content cut to fit is stored in the repo's [omission store](DISTILL.md#the-omission-store), and `_meta.omitted` lists the refs that restore it. Capped lists carry `*_total` siblings, so a count is never lost with its rows. Details: [MCP_RESPONSE_FIELDS.md](../reference/MCP_RESPONSE_FIELDS.md#truncation-and-recovery).

### Unrecognised arguments: `ignored_arguments`

A value outside a closed vocabulary (a misspelled `kind`, `include` key or `mode`) is dropped, never applied as a filter that matches nothing. The response then names it in `ignored_arguments`, with the valid values. The key is absent when every argument was understood. Per-tool shapes: [MCP_RESPONSE_FIELDS.md](../reference/MCP_RESPONSE_FIELDS.md#ignored-arguments).

---

## Which tool for which question

| Question | Tool |
|----------|------|
| How does X work? Where is Y? Why is Z like this? | `get_answer` |
| I am new to this repo. What is the shape of it? | `get_overview` |
| What is in this file, who calls this function, who owns it? | `get_context` |
| Show me this file's structure without reading all of it | `get_context(include=["skeleton"])` |
| Give me the body of this one symbol | `get_symbol` |
| Find the symbol, file or page matching this name or topic | `search_codebase` |
| Is this file risky to touch? What has broken here before? | `get_risk` |
| What could my set of changed files break, and which tests should run? | `get_risk(changed_files=[...])` |
| What did this commit, range or uncommitted diff make worse? | `get_change_risk` |
| Why was it built this way? Is there a decision governing it? | `get_why` |
| What should we fix first? Which files are least healthy? | `get_health` |
| Is the file I just edited healthier or worse? | `get_health(targets=[...])` |
| What can we delete? | `get_dead_code` |
| Which repos does this server serve? | `list_repos` (workspace) |
| How are these two files connected? | `get_dependency_path` (opt-in) |
| What runs when this entry point is called? | `get_execution_flows` (opt-in) |
| Turn this refactoring plan into a diff | `generate_refactoring_code` (opt-in) |
| Record that a refactoring plan is a false positive | `set_finding_status` (opt-in) |
| Which services in other repos depend on this one? | `get_blast_radius` (workspace, opt-in) |
| How coupled is the whole system? | `get_architecture` (workspace, opt-in) |
| Does the system obey our declared dependency rules? | `get_conformance` (workspace, opt-in) |

---

## Default tools

### `get_answer`

Answers a how, where or why question in one call: it runs hybrid retrieval over the wiki and the symbol index, then synthesises a cited answer. Use it first for any question about the code. Skip it when you already know the exact file or symbol; go to `get_context` or `get_symbol`.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `question` | string | required | The question, in plain language |
| `scope` | string | none | Path prefix to restrict retrieval to, e.g. `"src/auth/"` |
| `repo` | string | default repo | Workspace repo alias. `"all"` is not supported |
| `include` | list[string] | none | `["evidence"]` returns the full evidence projection with a larger budget |

**Key return fields:** `answer`, `confidence` (`high` / `medium` / `low`, rates the prose), `retrieval_quality` (`high` / `partial` / `weak`, rates the evidence), `citations`, `symbol_bodies` (live bodies of symbols the answer names), `retrieval`, `best_guesses` and `fallback_targets` (on low confidence; a low `best_guesses` row carries `functions`, `lines` and `size_bytes` instead of a page excerpt), `candidate_files` (ranked file paths the citations do not already name: up to 3 at `high`, 5 otherwise), `episodes` (dated facts bearing on the question), `degraded` (synthesis could not run), `_meta.scope_hint` (areas the answer did not touch).

A `high` answer can be cited directly. On `low`, read the rows the reply names, then `candidate_files`, before searching again. Without an LLM provider the tool still answers from retrieval, marked `degraded`.

```
get_answer(question="How does the authentication flow work?")
```

### `get_context`

A triage card for files, modules or symbols: summary, symbols with signatures and line numbers, hotspot and fix-history cues, ownership and decisions. It returns relationships, not source. Use it before reading or editing code, and batch every target in one call. Do not call `get_symbol` once per signature; use `include=["skeleton"]` or read the file.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `targets` | list[string] | required | File paths, module paths, or `"path::Symbol"` ids |
| `include` | list[string] | none | Any of `full_doc`, `ownership`, `last_change`, `callers`, `callees`, `metrics`, `community`, `decisions`, `skeleton`, `health`, `doc_drift`, `symbols` |
| `compact` | bool | `true` | `false` adds the structure block, imports and docstrings |
| `repo` | string | default repo | Workspace repo alias. `"all"` is not supported (returns an error) |

**Key return fields:** `targets` keyed by target, each with title, summary, symbols, `hotspot`, `fix_history` (files with counted bug fixes), `episodes`, decision titles, `file_preview` (for files with no symbols), `resolved_to` (a symbol miss falling back to its file); `dropped_targets` and `recovery` when the budget forced targets out; `_meta.complete` for whole files served.

A file's compact symbol list holds its top 15 symbols: types (classes, interfaces, structs, traits, enums, type aliases, impls, modules) first, then functions and methods, then the rest, each group by centrality. `symbols_truncated` gives the total, and `include=["symbols"]` lists them all. When the response budget trims the list further, it keeps symbols by kind and name match, not centrality. A row without `symbol_id` is `path::name`; methods and overload variants carry theirs, so pass a row's id to `get_symbol` when it has one.

`full_doc` returns the page as `content_md`, plus `digest_md` on pages that carry an agent digest: the questions the page answers, its concept index, public API and git signals. Both are dropped first when the response is over budget. `skeleton` renders a file with bodies elided: every signature, the imports, and the bodies of its most central symbols, with line ranges on every elision. `doc_drift` lists the documents that name the file and whether they carry drift. An empty `callers` or `callees` list comes with a `*_basis` saying how much of that language's calls the graph resolved; read it before concluding nothing calls a symbol.

```
get_context(targets=["src/auth/middleware.ts", "src/api/routes.ts"], include=["callers"])
get_context(targets=["src/big_module.py"], include=["skeleton"])
```

### `get_symbol`

Returns verified, line-numbered source for one symbol, a live line range, or an omission ref. It is a follow-up read, not an entry point: use it on an id another response gave you, for a body that was elided, or to restore truncated output. `get_answer` already ships `symbol_bodies`.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `symbol_id` | string | none | `"path/file.py::Name"`, `"path/file.py:140-180"` (live range, 200 lines max), or `"repowise#<ref>"` |
| `id` | string | none | Alias for `symbol_id` |
| `reference` | object | none | A `continuation_reference` or `fetch_reference` this tool emitted; pass it unchanged |
| `context_lines` | int | `0` | Extra lines before and after, 0 to 50 |
| `depth` | int | `1` | 2 or 3 also returns the bodies it calls, transitively |
| `query` | string | none | Omission refs only: keep stored lines matching this regex or substring |
| `repo` | string | default repo | Workspace repo alias. `"all"` is not supported |

**Key return fields:** `source` (up to about 600 lines, each prefixed with its line number), start and end lines, `kind`, `truncated` with a continuation to pass back, `ambiguous` and `candidates` when several symbols match, `callee_bodies` (with `depth` above 1), `not_rendered` (bodies past the budget, each with a range read to fetch it), fallback lines from a live grep on a miss.

```
get_symbol(symbol_id="src/auth/service.py::login", depth=2)
get_symbol(symbol_id="repowise#a1b2c3d4e5f6", query="FAILED")
```

### `search_codebase`

Hybrid search that routes by the shape of the query: identifiers search the symbol index, paths resolve files, prose runs wiki-semantic search, and mixed queries run both, keeping only pages that name a file. Use it when you want ranked hits themselves: enumerating matches, resolving an identifier to a `symbol_id`, scoping a later `get_context`. For a question, call `get_answer`; it runs this retrieval internally.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `query` | string | required | Identifier, path or natural-language text |
| `limit` | int | `5` | Max results. Outside `symbol` mode, at most this many distinct files: same-file hits share one row |
| `mode` | string | `"auto"` | `auto`, `concept`, `symbol`, `path` or `hybrid`. An unknown mode runs as `auto` |
| `kind` | string | none | `implementation`, `test`, `config` or `doc` |
| `symbol_kind` | string | none | Filter symbol hits, e.g. `function`, `class`, `method` |
| `page_type` | string | none | One page type, usually `file_page` or `module_page` |
| `repo` | string | default repo | Workspace repo alias, or `"all"` to search every repo |

**Key return fields:** `results` (every row that names a file carries it in `path`, and a row naming no file has no `path`; symbol hits carry `symbol_id`, line bounds and `signature`, plus `symbols` (`name:line` of up to five other matches in that file, then `+N more`) when several matched; concept hits carry `relevance_score`, `snippet` and `sources`; `file` on symbol and file hits is a deprecated alias of `path`, removed in the next minor release, and a page keeps `target_path` only where it differs from `path`), `candidates` (up to `limit` distinct openable file paths, best first). If your next move is a Read, read `candidates`: some `results` are pages that are not files.

```
search_codebase(query="GitIndexer index_repo")
search_codebase(query="login", mode="symbol", symbol_kind="method")
```

### `get_risk`

What history says about touching a file: hotspot score, bug-fix record, owners, co-change partners, dependents and security signals. Pass `changed_files` for PR mode, where the response leads with a `directive` naming what may break and which tests to run. To review a commit or diff itself, use `get_change_risk`.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `targets` | list[string] | `changed_files` | File paths to assess |
| `changed_files` | list[string] | none | Files in a change; switches on PR mode |
| `include` | list[string] | none | `graph` (typed dependents, consumers, cross-repo links), `churn`, `scales` (units and calibration, identical per call) |
| `repo` | string | default repo | Workspace repo alias. `"all"` is not supported |

**Key return fields:** per file: `hotspot_score` (0 to 1), `health_score` (0 to 10), `dependents_count`, `co_change_partners`, owners, test gaps, `security_signals`. In PR mode, `directive` with `may_break`, `may_break_tests`, `missing_cochanges`, `test_recommendations` (each `measured` or `inferred`), `tests_to_run`, `tests_to_run_basis`, `tests_to_update` (test files to edit, each with a `name_pair`, `imports` or `co_change` reason), `next_calls`, and the 0 to 10 `structural_impact_score`. A target naming no indexed file returns `resolved: false` with a reason, never zeroed counts.

Dependent counts are a floor over the indexed graph, and structural reach is not proof of runtime breakage. `structural_impact_score` is an uncalibrated heuristic, not a probability.

```
get_risk(targets=["src/auth/middleware.ts"])
get_risk(changed_files=["src/api/routes.ts", "src/middleware/cors.ts"])
```

### `get_change_risk`

Reviews one commit, a `base..head` range, or uncommitted work by comparing the two revisions directly; it needs no index refresh. It reports what the change newly made worse across defect, maintainability and performance, which tests cover the changed lines, and the changed files' bug-fix record. Use it before merging. Use `get_risk` for an indexed file's history.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `revspec` | string | uncommitted work | A commit, `base..head`, or `base...head` (from the merge-base). Pass `HEAD` when the tree is clean |
| `extensions` | list[string] | all | File suffixes to count, e.g. `[".py", ".ts"]` |
| `exclude_patterns` | list[string] | none | Gitignore-style paths to omit; combined with a root `.riskignore` |
| `include_paths` | list[string] or string | all | Gitignore-style paths to keep, as a list or one comma-separated string |
| `baseline` | int | `200` | Recent commits sampled for percentile ranking; `0` disables percentiles |
| `include` | list[string] | none | `findings` (every finding), `diagnostics` (raw score mechanics), `scales` (units) |
| `finding_id` | string | none | Expand one `health_delta` finding |
| `repo` | string | default repo | Workspace repo alias. `"all"` is not supported |

**Key return fields:** `directive` (`status` of `review_required`, `review_recommended`, `clear_in_analyzed_scope` or `unknown`, with reasons and next actions), `health_delta` (introduced, worsened and resolved findings with `status` and `top_findings`), `impacted_tests`, `patch_coverage` (when coverage is stored), `fix_history`, `branch_overlap`, `independent_changes`, `diff_shape`, `risk_percentile`, `cross_repo` (workspace mode).

Trust `health_delta.status`: `partial` means files were skipped and the change is not cleared. `diff_shape` describes size and spread, never danger. An empty diff returns `status: "nothing_to_score"`.

```
get_change_risk()
get_change_risk(revspec="main..HEAD", exclude_patterns=["tests/"])
get_change_risk(revspec="main..HEAD", finding_id="chf_27a13be11e7ee33f")
```

### `get_why`

Why code is shaped the way it is: decision records, with git archaeology and mined rationale comments as fallbacks when no decision governs a path. Call it before a refactor or before diverging from an existing pattern.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `query` | string | none | A question, or a file or module path. Omit for the decision health dashboard |
| `targets` | list[string] | none | Paths to anchor a question to, or to ask about on their own with no `query` |
| `id` | string | none | A decision id or `ev_...` evidence id from an earlier response |
| `reference` | object | none | An evidence reference this tool emitted; pass it unchanged |
| `repo` | string | default repo | Workspace repo alias, or `"all"` (only with a `query`) |

Modes: a question searches decision records; a path returns that file's `decisions`, `candidates` and `history` plus its origin story and `alignment`; `targets` alone asks about those files; no arguments returns the health dashboard (stale decisions, conflicts, ungoverned hotspots); `id` resolves one record directly.

**Key return fields:** `decisions` (accepted, binding), `candidates` (inferred, nobody accepted them: hints, not rules), `history` (accepted, since withdrawn), `answer_basis` (the strongest lane served; only `decision` is a ruling), `alignment`, `git_archaeology`, `evidence_refs`.

```
get_why(query="why JWT over sessions?")
get_why(query="src/payments/processor.ts")
get_why()
```

### `get_overview`

The architecture map: summary, key modules, entry points, layers, code and git health, and the next actions for the week and quarter. Call it once, first, in an unfamiliar repo. Skip it afterwards; it does not change within a session.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `include` | list[string] | none | Any of `content` (full essay), `outline` (wiki page tree), `tour` (guided tour and reading order), `decisions`, `graph` (community clusters), `ownership` (top owners) |
| `repo` | string | default repo | Workspace repo alias, or `"all"` for the cross-repo topology |

**Key return fields:** `title`, `content_md` (summary section by default), `key_modules`, `entry_points`, `architecture` (layers in stack order, plus `dependencies`: the ten heaviest package-to-package edges with `from`, `to`, `verb` and `weight`, and `edges_total` when more exist; absent in a single-package repo), `code_health`, `git_health`, `next_actions`, `more` (names the opt-in blocks), and a `workspace` footer in workspace mode.

```
get_overview()
get_overview(include=["outline", "content"])
```

### `get_health`

Code-health scores and findings from the stored analysis, across defect risk, maintainability and performance. With no `targets` it returns a dashboard led by `fix_first`, one ranked queue of what to fix first. With `targets` it scores those files. It never recomputes: commit, then run `repowise update`, to see new numbers. No LLM calls.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `targets` | list[string] | none (dashboard) | File paths or `module:<name>`. Misses are named in `unresolved` |
| `include` | list[string] | none | Blocks: `biomarkers`, `refactoring`, `trend`, `coverage`, `accuracy`, `signals`, `churn_complexity`, `doc_drift`, `semantics`, `unverified`. Dimension filters: `performance`, `defect`, `maintainability`, `advisory` |
| `only` | list[string] | none | Keep just these top-level keys; identity, totals and recovery fields always survive |
| `limit` | int | `20` | Max rows in every ranked list, capped at 50; `0` for none |
| `cursor` | int | `0` | Offset into a ranked list; the `recovery` block names the next call |
| `fix_id` | string | none | Open one `fix_first` item in full |
| `finding_id`, `plan_id` | string | none | Open one finding or refactoring plan by id |
| `opportunity_id` | string | none | Open one opportunity: `perf...` for performance, `refop...` for a refactoring |
| `refactoring_view` | string | `"diversified"` | `diversified`, `canonical` or `file_spread` |
| `refactoring_scope` | string | `fix_first` without targets, `all` with | Which open refactoring opportunities to list |
| `refactoring_type`, `refactoring_confidence`, `refactoring_effort` | string | none | Refactoring queue filters |
| `performance_view` | string | `detail` | `detail` or `summary` |
| `performance_context` | string | `production` | `production`, `tooling`, `test`, `unknown` or `all` |
| `performance_boundary`, `performance_confidence`, `performance_actionability`, `performance_sort` | string | none | Performance queue filters |
| `scope` | string | `"all"` | `production` drops test files from every figure |
| `counts` | string | `"everything"` | `code_shape` drops the git-derived half of the score |
| `repo` | string | default repo | Workspace repo alias. `"all"` is not supported |

Only one of `fix_id`, `finding_id`, `plan_id`, `opportunity_id` per call; passing two returns `mode: "conflict"`.

**Key return fields:** `mode`, `fix_first` (`lead`, up to five `items` each with a `next_call`, `totals`), `kpis`, `gap_analysis`, `worst_files`, `high_leverage_files` (ranked by `weighted_deficit`), `top_findings`, `unresolved`, and the opt-in blocks you named. `_meta.health_analysis` says whether stored analysis exists and which commit it describes.

The response is bounded. Pair `include` with `only` to keep one block, e.g. `get_health(include=["refactoring"], only=["refactoring_opportunities"])`.

```
get_health(only=["fix_first"])
get_health(fix_id="fix1_...")
get_health(targets=["src/api/server.py"], include=["signals"])
get_health(include=["performance"], only=["performance_summary"])
get_health(include=["coverage"], only=["coverage"])
```

### `get_dead_code`

Unreachable files, unused exports, unused internals and zombie packages, tiered by confidence. Use it for a cleanup pass, not a targeted fix. `safe_only` leaves out anything with runtime-load risk such as dynamic imports and framework-registered functions.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `kind` | string | all | `unreachable_file`, `unused_export`, `unused_internal` or `zombie_package` |
| `min_confidence` | float or string | `0.4` | Confidence floor; also accepts `"high"` (0.7), `"medium"` (0.4), `"low"` (0.0) |
| `tier` | string | all | `high` (0.7 and up), `medium` (0.4 to 0.7) or `low` |
| `safe_only` | bool | `false` | Deletion-ready findings only |
| `limit` | int | `20` | Max findings per tier, clamped to 25 |
| `directory` | string | none | Path-prefix filter |
| `owner` | string | none | Primary-owner filter |
| `group_by` | string | none | `directory` or `owner` rollup |
| `include_internals` | bool | `false` | Also scan private symbols (more false positives) |
| `include_zombie_packages` | bool | `true` | Monorepo package findings |
| `no_unreachable` | bool | `false` | Skip unreachable-file findings |
| `no_unused_exports` | bool | `false` | Skip unused-export findings |
| `finding_id` | string | none | Open one finding by its id |
| `repo` | string | default repo | Workspace repo alias, or `"all"` to aggregate across repos |

**Key return fields:** `tiers` (findings per tier, each with path, kind, confidence, line count and cleanup impact), `summary` (totals over every open finding, `filtered_findings`, `filters`, `withheld_types`, `call_resolution_basis`), `by_directory` or by-owner rollups with `group_by`. In workspace mode, confidence drops on findings another repo still imports.

```
get_dead_code()
get_dead_code(tier="high", safe_only=true)
get_dead_code(kind="unused_export", group_by="owner")
```

---

## Workspace default

### `list_repos`

Lists the repos this server serves. On by default in workspace mode only; a single-repo server is bound to its one repository. No parameters.

**Key return fields:** `workspace` (bool), `workspace_root`, `default_repo`, and per repo its `alias`, config-relative `path` and `absolute_path`. Any of those can be passed unchanged as `repo` to other tools.

```
list_repos()
```

---

## Opt-in tools

Enable with `mcp.tools: ["+name"]` or `repowise mcp --tools "+name"` (see [Configuring the tool surface](#configuring-the-tool-surface)). The last three are workspace-only.

### `get_dependency_path`

The shortest dependency path between two files or modules. When none exists it returns context to debug the gap: nearest common ancestors, shared neighbours, community analysis and bridge suggestions.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `source` | string | required | Source file or module path |
| `target` | string | required | Target file or module path |
| `repo` | string | default repo | Workspace repo alias. `"all"` is not supported |

```
get_dependency_path(source="src/api/routes.py", target="src/db/models.py")
```

### `get_execution_flows`

The top-scored entry points and a breadth-first call trace from each, marking where a flow crosses community boundaries. Use it to see what runs end to end.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `top_n` | int | `10` | Entry points to trace |
| `max_depth` | int | `8` | Max trace depth per flow |
| `entry_point` | string | none | Trace from this symbol, overriding `top_n` scoring |
| `repo` | string | default repo | Workspace repo alias. `"all"` is not supported |

```
get_execution_flows(entry_point="src/cli/main.py::main", max_depth=4)
```

### `generate_refactoring_code`

Turns one refactoring plan from `get_health(include=["refactoring"])` into generated code and a unified diff, grounded on the plan and the source spans it names. Extract Class results include an LCOM4 before-and-after check. It calls the repo's configured LLM provider, and caches by content hash so an unchanged plan never regenerates.

Adding the tool to the surface is the opt-in step. Generation then runs unless `.repowise/config.yaml` sets `refactoring.llm.enabled: false`, in which case the plan is returned with `generation.available: false` and `reason: "disabled"`. With no provider configured it returns `error: "no_provider"`.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `suggestion_id` | string | required | A plan `id` from `get_health` |
| `repo` | string | default repo | Workspace repo alias. `"all"` is not supported |

```
generate_refactoring_code(suggestion_id="a1b2c3d4")
```

### `set_finding_status`

Records a durable verdict on one refactoring plan in the index, so the triage survives later analysis runs.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `suggestion_id` | string | required | A plan `id` or `public_id` from `get_health` |
| `status` | string | required | `open`, `acknowledged`, `resolved` or `false_positive` |
| `reason` | string | `"agent"` | Free-text audit note stored on the row |
| `repo` | string | default repo | Workspace repo alias. `"all"` is not supported |

`false_positive` plans are never re-emitted. `acknowledged` stays visible but stops counting as unheard. `resolved` stays resolved even if the detector still fires. `open` resets. Returns the new status and its timestamp.

```
set_finding_status(suggestion_id="a1b2c3d4", status="false_positive", reason="the class is a DTO")
```

### `get_blast_radius`

*Workspace only.* Which services in other repos are structurally reachable from a changed service, ranked by an uncalibrated 0 to 1 path weight. Structural edges (HTTP, gRPC, events, packages) outrank co-change. It is not a runtime-breakage probability.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `targets` | list[string] | required | Node ids (`repo` or `repo::service/path`), repo aliases, or the symbol id of a published symbol |
| `max_depth` | int | `3` | Reachability depth, 1 to 8 |
| `include_behavioral` | bool | `true` | Include co-change edges |

**Key return fields:** impacted services with `score`, `distance` and edge kinds, `impacted_repos`, `total_impacted`, `impact_score_semantics`, and `symbol_targets` naming the consuming symbols across each contract link, with the tests that guard them.

```
get_blast_radius(targets=["backend"])
```

### `get_architecture`

*Workspace only.* Whole-system structure: propagation cost (the share of other services the average service reaches), the largest cyclic core, service roles, and a deterministic 1 to 10 score. Structural edges only. No parameters.

**Key return fields:** `score`, `architecture_type`, `propagation_cost_pct`, `core_members`, `cycle_count`, `role_breakdown`, `summary`.

```
get_architecture()
```

### `get_conformance`

*Workspace only.* Cross-repo dependencies that break the rules declared under `conformance:` in `.repowise-workspace.yaml`, plus circular service dependencies. Without opting in, the same findings still reach `get_risk`'s PR-mode directive as `conformance_violations` and `dependency_cycles`.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `repo` | string | whole workspace | Limit findings to those involving this repo |

**Key return fields:** `violations` (source and target services, the rule that fired, `edge_kind`), `cycles`, `violation_count`, `cycle_count`, `rules_evaluated`.

```
get_conformance(repo="frontend")
```

---

## Workspace mode

A server started inside a [workspace](../scale/WORKSPACES.md) serves every repo in it from one process. `repowise mcp --no-workspace` forces single-repo mode for a nested repo. Every tool with a `repo` parameter takes an alias, path or absolute path from `list_repos`; omitting it targets the default repo.

Only four tools answer across every repo with `repo="all"`. The rest return an error naming the available aliases.

| Tool | `repo="all"` |
|------|--------------|
| `search_codebase` | Yes: federates the search and merges results |
| `get_overview` | Yes: the cross-repo topology (co-changes, package dependencies, API contracts), with no single-repo detail |
| `get_dead_code` | Yes: aggregates findings across repos |
| `get_why` | Only with a `query`; not for the dashboard or an `id` lookup |
| `get_answer`, `get_context`, `get_symbol`, `get_risk`, `get_change_risk`, `get_health` | No |
| `get_dependency_path`, `get_execution_flows`, `generate_refactoring_code`, `set_finding_status` | No |
| `get_conformance` | No `"all"` value; it covers the whole workspace when `repo` is omitted |
| `get_blast_radius`, `get_architecture`, `list_repos` | No `repo` parameter; they always read the whole workspace |

Single-repo tools also gain cross-repo evidence in workspace mode:

- `get_context` adds a per-target `cross_repo` block: co-change partners in other repos and contract consumers or providers.
- `get_risk` in PR mode adds `will_break_consumers` (structural reach only), `missing_cross_repo_cochanges`, `breaking_changes`, `conformance_violations` and `dependency_cycles` to its directive.
- `get_change_risk` adds `cross_repo`: consumers of the contracts the change touches, breaking changes, and the consumer-side tests to run.
- `get_dead_code` lowers confidence on findings another repo still imports.

See [WORKSPACES.md](../scale/WORKSPACES.md) for setup, contracts and the system graph.

---

Hooks complement these tools with passive context: see [HOOKS.md](HOOKS.md) and [CODEX.md](CODEX.md).
