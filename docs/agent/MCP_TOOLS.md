# MCP Tools Reference

repowise serves its codebase intelligence to AI coding assistants (Claude Code, Codex, Cursor, Cline, Windsurf and any other [Model Context Protocol](https://modelcontextprotocol.io) client) as MCP tools. They answer from the index: dependency graph, git history, generated docs, decision records, health and dead-code analysis. No tool edits your code.

Two opt-in tools go further: `set_finding_status` records a triage verdict in the index, and `generate_refactoring_code` calls your LLM to draft a diff.

18 tools are registered in total. A single-repo server advertises 10 by default: exactly the canonical tools. Workspace mode adds the `list_repos` discovery utility, for 11. 7 specialist tools are opt-in where eligible.

This page says which tool to call and with what arguments. Response fields, the `_meta` envelope and truncation rules are in [MCP_RESPONSE_FIELDS.md](../reference/MCP_RESPONSE_FIELDS.md).

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

- `repowise init` registers the server with Claude Code and installs its hooks; `repowise init --codex` writes project-local Codex MCP config and hooks.
- `--no-editor-setup` (or `REPOWISE_SKIP_EDITOR_SETUP=1`) skips registration for a scratch clone, worktree or CI run.
- Each Claude config holds one `repowise` entry, so indexing a second repo repoints it; `init` prints a notice first.

---

## Configuring the tool surface

| Tool | Default | Mode |
|------|---------|------|
| `get_answer`, `get_context`, `get_symbol`, `search_codebase`, `get_risk`, `get_change_risk`, `get_why`, `get_overview`, `get_health`, `get_dead_code` | on | single-repo and workspace |
| `list_repos` | on | workspace only |
| `get_dependency_path`, `get_execution_flows`, `generate_refactoring_code`, `set_finding_status` | opt-in | single-repo and workspace |
| `get_blast_radius`, `get_architecture`, `get_conformance` | opt-in | workspace only |

- **Default (single-repo):** 10 tools, exactly the canonical intelligence set.
- **Default (workspace):** those 10 plus `list_repos`, the workspace discovery utility.

Set `mcp.tools` in `.repowise/config.yaml`, or pass `--tools` per launch (it overrides the config):

```yaml
mcp:
  tools: ["+get_execution_flows", "-get_dead_code"]   # adjust the default set
# tools: ["get_answer", "get_context"]                 # explicit allowlist
# tools: all                                           # every tool usable in this mode
# tools: lean                                          # the agent-lean profile
```

```bash
repowise mcp --tools "+get_execution_flows"   # default set plus one
repowise mcp --tools "get_answer,get_context"  # explicit allowlist
repowise mcp --tools lean                      # agent-lean profile
repowise mcp --all                             # every tool usable in this mode
```

- `+` or `-` tokens adjust the default set; a list without prefixes is an allowlist.
- Unknown names are ignored with a warning, as are workspace-only tools outside workspace mode (no workspace graph to read).
- Clients see the surface resolved at server start; restart after changing it.
- The dashboard Settings page toggles each tool per repo and writes the same block. See [CONFIG.md](../reference/CONFIG.md#the-mcp-block).

**The `lean` profile** is `get_answer`, `get_context`, `get_symbol`, `search_codebase`, `get_risk` and `get_why`, plus `list_repos` in workspace mode. It is small enough to keep every schema loaded, so with `mcp.tools: lean`, `repowise init` skips the Claude Code tool-search recommendation.

---

## Reading a response

Every tool returns JSON with a `_meta` envelope ([fields](../reference/MCP_RESPONSE_FIELDS.md#the-_meta-envelope)). Check:

- `stale_warning`: the commit the facts describe and what differs from it; read the files this response served from disk. Absent means current.
- `complete`: content verified against the live file. Do not re-read it.
- `degraded`: the parts of the server that failed for this call. A degraded empty result is a failed read, not an empty repository.
- `get_answer`'s top-level `confidence` is its one trust signal.
- Empty fields, `null` and a `false` status flag are not sent; absence means nothing to report.
- Diagnostics (`timing_ms`, index commits and age, budget accounting) ride only on `get_overview`; `REPOWISE_MCP_DEBUG_META=1` restores them everywhere.

### Reversible truncation: `_meta.omitted`

Responses fit 24,000 characters (32,000 with a nonempty `include` for most tools). Cut content goes to the [omission store](DISTILL.md#the-omission-store); restore a ref from `_meta.omitted` with `repowise expand <ref>` or `get_symbol("repowise#<ref>")`. A list that was cut carries `<key>_total`, its size before the cut. [Details](../reference/MCP_RESPONSE_FIELDS.md#truncation-and-recovery).

### Unrecognised arguments: `ignored_arguments`

A value outside a closed vocabulary (a misspelled `kind`, `include` key or `mode`) is dropped, never applied as a filter matching nothing, and named in `ignored_arguments` with the valid values. [Per-tool shapes](../reference/MCP_RESPONSE_FIELDS.md#ignored-arguments).

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

Every `repo` parameter takes a workspace repo alias and defaults to the default repo; `"all"` support is in [Workspace mode](#workspace-mode).

---

## `get_answer`

Answers a how, where or why question in one call: hybrid retrieval over the wiki and symbol index, then a cited answer. Call it first for any question; skip it when you know the exact file or symbol (use `get_context` or `get_symbol`).

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `question` | string | required | The question, in plain language |
| `scope` | string | none | Path prefix to restrict retrieval, e.g. `"src/auth/"` |
| `include` | list[string] | none | `evidence` (full evidence, larger budget), `diagnostics` (adds `retrieval_quality`) |
| `repo` | string | default repo | `"all"` not supported |

**Returns** `answer`, `confidence`, `citations`, `symbol_bodies`, `candidate_files`, `best_guesses` ([fields](../reference/MCP_RESPONSE_FIELDS.md#get_answer)). On `low`, read the named rows, then `candidate_files`, before searching again. Without an LLM provider it answers from retrieval, marked `degraded`.

```
get_answer(question="How does the authentication flow work?")
```

---

## `get_context`

A triage card per file, module or symbol: summary, symbols with signatures, hotspot and fix-history cues, ownership, decisions. Relationships, not source. Use it before reading or editing, batching every target in one call; use `include=["skeleton"]` or read the file rather than calling `get_symbol` per signature.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `targets` | list[string] | required | Paths, module paths, `"path::Symbol"`, `"component:<name>"`, `"change:<id>"`, `"package:<name>"` |
| `include` | list[string] | none | `full_doc`, `ownership`, `last_change`, `callers`, `callees`, `references`, `metrics`, `community`, `decisions`, `skeleton`, `skeleton+`, `health`, `doc_drift`, `symbols`, `changes`, `provenance`, `suspects`, `files` ([what each adds](../reference/MCP_RESPONSE_FIELDS.md#context-opt-in-blocks)) |
| `compact` | bool | `true` | `false` adds the structure block, imports and docstrings |
| `symptom` | string | none | Issue text, error or stack trace for `suspects` to rank against instead of path words |
| `repo` | string | default repo | `"all"` returns an error |

Target kinds:

- `path::Symbol`: a miss falls back to the file's card.
- A directory: a module card from the indexed files under it, with or without a module page (none is `index_status: "directory_without_module_page"`, with up to 15 `docs.files`, and `docs.files_all` naming the opt-in when more exist). `include=["files"]` lists every file under it with `has_test` and the tests that cover each, for questions like "which files here have no test". Git history is stored per file, so `ownership` counts the files each person is primary owner of (`owners_by_files`), `last_change` gives the newest commit's date and file (and its author when a stored author matches), and `hotspot_files` counts hotspot files; no commit share, contributor count or bus factor is claimed.
- `component:<name>`: declared context and rolled-up facts, from [`.repowise/components.yaml`](../reference/CONFIG.md#repowisecomponentsyaml) or derived.
- `change:<id>` (`change:pr-123` or a commit sha): one [Change Record](../reference/CLI_REFERENCE.md#repowise-changes).
- `package:<name>` (`package:requests`, `package:npm:lodash`): where a package is imported ([PACKAGE_USAGE.md](../reference/PACKAGE_USAGE.md)).

**Returns** `targets` (one card each), plus `dropped_targets` and `recovery` when the budget forced targets out ([fields](../reference/MCP_RESPONSE_FIELDS.md#get_context)). Pass a symbol row's `symbol_id` to `get_symbol`. Before concluding nothing calls a symbol, read the `*_basis` beside an empty `callers` list. `references` gives a rename's edit set.

```
get_context(targets=["src/auth/middleware.ts", "src/api/routes.ts"], include=["callers"])
get_context(targets=["src/big_module.py"], include=["skeleton"])
```

---

## `get_symbol`

Verified, line-numbered source for one symbol, a live line range, or an omission ref. A follow-up read, not an entry point: use it on an id another response gave you, an elided body, or truncated output. `get_answer` already ships `symbol_bodies`.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `symbol_id` | string | none | `"path/file.py::Name"`, `"path/file.py:140-180"` (live range, 200 lines max) or `"repowise#<ref>"` |
| `id` | string | none | Alias for `symbol_id` |
| `reference` | object | none | A `continuation_reference` or `fetch_reference` this tool emitted, unchanged |
| `context_lines` | int | `0` | Extra lines before and after, 0 to 50 |
| `depth` | int | `1` | 2 or 3 adds the bodies it calls, transitively |
| `query` | string | none | Omission refs: keep lines matching this regex or substring. Read as `symbol_id` when none is given |
| `repo` | string | default repo | `"all"` not supported |

**Returns** `source` (about 600 lines max), bounds, `kind`, `truncated`, `candidates`, `callee_bodies`, `outlined` ([fields](../reference/MCP_RESPONSE_FIELDS.md#get_symbol)).

```
get_symbol(symbol_id="src/auth/service.py::login", depth=2)
get_symbol(symbol_id="repowise#a1b2c3d4e5f6", query="FAILED")
```

---

## `search_codebase`

Hybrid search routed by query shape: identifiers hit the symbol index, paths resolve files, prose runs wiki-semantic search, and mixed queries run both, keeping only pages that name a file. Use it for ranked hits (enumerating matches, resolving an identifier to a `symbol_id`, scoping a `get_context`); for a question, use `get_answer`, which runs this retrieval.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `query` | string | `""` | Identifier, path or natural language. Required unless `pattern` is given |
| `pattern` | string | none | Alias for `query`; when both are given `query` wins and `pattern` lands in `ignored_arguments` |
| `limit` | int | `5` | Max results; outside `symbol` mode, max distinct files |
| `mode` | string | `"auto"` | `auto`, `concept`, `symbol`, `path`, `hybrid`; unknown runs as `auto` |
| `kind` | string | none | `implementation`, `test`, `config` or `doc` |
| `symbol_kind` | string | none | Symbol filter, e.g. `function`, `class`, `method` |
| `page_type` | string | none | One page type, usually `file_page` or `module_page` |
| `repo` | string | default repo | Or `"all"` for every repo |

**Returns** `results`, `candidates` (openable file paths), and `lines` with `complete` for identifier queries ([fields](../reference/MCP_RESPONSE_FIELDS.md#search_codebase)). Before a Read, read `candidates`: some `results` are pages, not files.

```
search_codebase(query="GitIndexer index_repo")
search_codebase(query="login", mode="symbol", symbol_kind="method")
```

---

## `get_risk`

What history says about touching a file: hotspot, bug-fix record, owners, co-change partners, dependents and security signals. With `changed_files` (PR mode) it leads with a `directive`: what may break and which tests to run. To review the diff itself, use `get_change_risk`.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `targets` | list[string] | `changed_files` | File paths to assess |
| `changed_files` | list[string] | none | Files in a change; switches on PR mode |
| `include` | list[string] | none | `graph`, `churn`, `owners`, `tests`, `blast`, `scales`, `changes` ([what each adds](../reference/MCP_RESPONSE_FIELDS.md#get_risk)) |
| `repo` | string | default repo | `"all"` not supported |

**Returns** per-file `hotspot_score`, `health_score`, `dependents_count`, `co_change_partners`, `primary_owner`, `security_signals`; in PR mode `directive` with `may_break`, `tests_to_run`, `tests_to_update`, `reach` ([fields](../reference/MCP_RESPONSE_FIELDS.md#pr-mode-directive)). Dependent counts are a floor; `reach` is an uncalibrated band, not a probability.

```
get_risk(targets=["src/auth/middleware.ts"])
get_risk(changed_files=["src/api/routes.ts", "src/middleware/cors.ts"])
```

---

## `get_change_risk`

Reviews a commit, a range, or uncommitted work by comparing the two revisions directly, with no index refresh: what it made worse (defect, maintainability, performance), which tests cover the changed lines, and the files' fix record. Use it before merging; use `get_risk` for an indexed file's history.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `revspec` | string | uncommitted work | A commit, `base..head`, or `base...head` (from the merge-base). Pass `HEAD` when the tree is clean |
| `extensions` | list[string] | all | File suffixes to count, e.g. `[".py", ".ts"]` |
| `exclude_patterns` | list[string] | none | Gitignore-style paths to omit, plus a root `.riskignore` |
| `include_paths` | list[string] or string | all | Gitignore-style paths to keep, as a list or comma-separated string |
| `baseline` | int | `200` | Recent commits sampled for percentiles; `0` disables |
| `include` | list[string] | none | `findings`, `diagnostics`, `scales`, `evidence` (the record `repowise changes show` prints), `tests` |
| `finding_id` | string | none | Expand one `health_delta` finding |
| `per_commit` | bool | `false` | With a range, one compact row per commit (up to 30) in place of the review |
| `repo` | string | default repo | `"all"` not supported |

`include_paths` and `exclude_patterns` scope the score, health delta and per-file blocks; `impacted_tests` still selects over the whole change, less repowise's own output.

**Returns** `directive.status` (the verdict), `health_delta`, `impacted_tests`, `patch_coverage`, `fix_history`, `diff_shape` ([fields](../reference/MCP_RESPONSE_FIELDS.md#get_change_risk)).

```
get_change_risk()
get_change_risk(revspec="main..HEAD", exclude_patterns=["tests/"])
get_change_risk(revspec="main..HEAD", finding_id="chf_27a13be11e7ee33f")
get_change_risk(revspec="main..HEAD", per_commit=True)
```

---

## `get_why`

Why code is shaped as it is: decision records, falling back to git archaeology and mined rationale comments when no decision governs a path. Call it before a refactor or before diverging from a pattern.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `query` | string | none | A question or a path. Omit for the decision health dashboard |
| `targets` | list[string] | none | Paths to anchor a question to, or to ask about alone |
| `id` | string | none | A decision id or `ev_...` evidence id from an earlier response |
| `reference` | object | none | An evidence reference this tool emitted, unchanged |
| `repo` | string | default repo | Or `"all"`, only with a `query` |

Modes: a question searches decisions; a path (or `targets` alone) returns `decisions`, `candidates`, `history`, its origin story and `alignment`; `id` resolves one record; no arguments returns the dashboard (stale decisions, conflicts, ungoverned hotspots).

A directory's origin story (`scope: "directory"`) is built from the files under it: how many have history, the first and last commit dates, and their stored key commits, each listed once. Git history is stored per file, so it carries no commit count or owner for the directory.

**Returns** `decisions` (binding), `candidates` (hints, not rules), `history`, `answer_basis` ([fields](../reference/MCP_RESPONSE_FIELDS.md#get_why)).

```
get_why(query="why JWT over sessions?")
get_why(query="src/payments/processor.ts")
get_why()
```

---

## `get_overview`

Orientation for an unfamiliar repo: summary, key modules, entry points, layers, code health, riskiest files and this week's top actions. Call it once, first; it does not change within a session.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `include` | list[string] | none | `content` (full essay), `outline`, `tour`, `decisions`, `graph`, `ownership`, `components` (with file counts), `changes`, `actions` (the quarter too), `tools` (enabled tools, opt-ins, recipes), `scope` |
| `repo` | string | default repo | Or `"all"` for the cross-repo topology, where only `include=["tools"]` applies |

**Returns** `content_md`, `key_modules`, `entry_points`, `commands`, `architecture`, `code_health`, `git_health`, `next_actions`, `more` ([fields](../reference/MCP_RESPONSE_FIELDS.md#get_overview)).

```
get_overview()
get_overview(include=["outline", "content"])
```

---

## `get_health`

Code-health scores and findings from stored analysis, across defect risk, maintainability and performance. No `targets` gives a dashboard led by `fix_first`, one ranked queue; `targets` scores those files. It never recomputes (commit, then `repowise update`) and makes no LLM calls.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `targets` | list[string] | none (dashboard) | File paths or `module:<name>`; misses go to `unresolved` |
| `include` | list[string] | none | Blocks: `biomarkers`, `refactoring`, `trend`, `coverage`, `accuracy`, `signals`, `churn_complexity`, `untested_complexity`, `doc_drift`, `semantics`, `unverified`, `recipe`, `what_if` ([what each adds](../reference/MCP_RESPONSE_FIELDS.md#opt-in-blocks)). Dimensions: `performance`, `defect`, `maintainability`, `advisory` |
| `only` | list[string] | none | Keep these top-level keys; identity, totals and recovery always survive |
| `limit` | int | `20` | Rows per ranked list, max 50; `0` for none |
| `cursor` | int | `0` | Offset into a ranked list (or `fix_first` when named in `only`); `recovery` names the next call |
| `fix_id` | string | none | One `fix_first` item (`fix1_...`) or `next_actions` action (`act_...`) in full |
| `finding_id` | string | none | One finding |
| `plan_id` | string | none | One refactoring plan; `include=["recipe"]` adds its preconditions, ordered steps, postconditions and what it must not do |
| `opportunity_id` | string | none | `perf...` or `refop...` |
| `refactoring_view` | string | `"diversified"` | `diversified`, `canonical`, `file_spread` |
| `refactoring_scope` | string | `fix_first` without targets, `all` with | Which opportunities, and their plans, to list |
| `refactoring_type` | string | none | `break_cycle`, `extract_class`, `extract_helper`, `extract_method`, `move_method`, `split_file` |
| `refactoring_confidence` | string | none | `high`, `medium`, `low` |
| `refactoring_effort` | string | none | `S`, `M`, `L`, `XL` |
| `performance_view` | string | `detail` | `detail` or `summary` |
| `performance_context` | string | `production` | `production`, `tooling`, `test`, `unknown`, `all` |
| `performance_boundary` | string | none | `db`, `network`, `filesystem`, `subprocess`, `lock`, `none` |
| `performance_confidence` | string | none | `high`, `medium`, `low` |
| `performance_actionability` | string | none | `plan_ready`, `advisory`, `investigate`, `expected` |
| `performance_sort` | string | `rank` | `rank`, `leverage`, `observations` |
| `scope` | string | `"all"` | `production` drops test, tooling, vendored and generated files from every figure |
| `counts` | string | `"everything"` | `code_shape` drops the git-derived half of the score |
| `repo` | string | default repo | `"all"` not supported |

- Pass one of `fix_id`, `finding_id`, `plan_id`, `opportunity_id`; two return `mode: "conflict"`. A miss on an id minted for another selector (a `fix1_` id passed as `plan_id`) carries a `hint` naming the right one.
- `fix_first` shows five items unless `limit` comes with `only=["fix_first"]`, which pages up to 25.
- The response is bounded: pair `include` with `only` to keep one block, e.g. `get_health(include=["refactoring"], only=["refactoring_opportunities"])`.

**Returns** `mode`, `fix_first`, `kpis`, `gap_analysis`, `worst_files`, `high_leverage_files`, `top_findings`, `unresolved` ([fields](../reference/MCP_RESPONSE_FIELDS.md#get_health)). A due `fix_first` lead carries `first_step` and `verify`, so it can be started without a `fix_id` call.

```
get_health(only=["fix_first"], limit=25)
get_health(only=["fix_first"], limit=25, cursor=25)
get_health(fix_id="fix1_...")
get_health(fix_id="act_...")
get_health(include=["refactoring"], only=["refactoring_plans"], limit=15, refactoring_scope="all")
get_health(targets=["src/api/server.py"], include=["signals"])
get_health(include=["performance"], only=["performance_summary"])
get_health(include=["coverage"], only=["coverage"])
```

---

## `get_dead_code`

Unreachable files, unused exports, unused internals and zombie packages, tiered by confidence. For a cleanup pass, not a targeted fix.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `kind` | string | all | `unreachable_file`, `unused_export`, `unused_internal`, `zombie_package` |
| `min_confidence` | float or string | `0.4` | Floor; or `"high"` (0.7), `"medium"` (0.4), `"low"` (0.0) |
| `tier` | string | all | `high` (0.7+), `medium` (0.4 to 0.7), `low` |
| `safe_only` | bool | `false` | Deletion-ready only: drops runtime-load risk such as dynamic imports and framework-registered functions |
| `limit` | int | `20` | Per tier, clamped to 25 |
| `directory` | string | none | Path-prefix filter |
| `owner` | string | none | Primary-owner filter |
| `group_by` | string | none | `directory` or `owner` rollup |
| `include_internals` | bool | `false` | Also scan private symbols (more false positives) |
| `include_zombie_packages` | bool | `true` | Monorepo package findings |
| `no_unreachable` | bool | `false` | Skip unreachable-file findings |
| `no_unused_exports` | bool | `false` | Skip unused-export findings |
| `include_unverified` | bool | `false` | Also show provisional types and gated-language findings |
| `finding_id` | string | none | Open one finding |
| `repo` | string | default repo | Or `"all"` to aggregate across repos |

**Returns** `tiers`, `summary`, and `by_directory` or `by_owner` with `group_by` ([fields](../reference/MCP_RESPONSE_FIELDS.md#get_dead_code)).

```
get_dead_code()
get_dead_code(tier="high", safe_only=true)
get_dead_code(kind="unused_export", group_by="owner")
```

---

## `list_repos`

Lists the repos this server serves, to find the `repo` value for other tools. Default in workspace mode only (a single-repo server is bound to one repository). No parameters.

**Returns** `workspace`, `workspace_root`, `default_repo`, `repos` ([fields](../reference/MCP_RESPONSE_FIELDS.md#list_repos)).

```
list_repos()
```

---

## `get_dependency_path`

Opt-in (`mcp.tools: ["+get_dependency_path"]` or `--tools "+get_dependency_path"`; the same for every opt-in). The shortest dependency path between two files or modules; with none, context to debug the gap.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `source` | string | required | Source file or module path |
| `target` | string | required | Target file or module path |
| `repo` | string | default repo | `"all"` not supported |

```
get_dependency_path(source="src/api/routes.py", target="src/db/models.py")
```

---

## `get_execution_flows`

Opt-in. The top-scored entry points with a breadth-first call trace from each, marking community-boundary crossings: what runs end to end.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `top_n` | int | `10` | Entry points to trace, 1 to 50 |
| `max_depth` | int | `8` | Trace depth per flow, 1 to 20 |
| `entry_point` | string | none | Trace from this symbol, overriding `top_n` |
| `repo` | string | default repo | `"all"` not supported |

```
get_execution_flows(entry_point="src/cli/main.py::main", max_depth=4)
```

---

## `generate_refactoring_code`

Opt-in. Turns one plan from `get_health(include=["refactoring"])` into code and a unified diff, grounded on the plan and its source spans. Extract Class results include an LCOM4 before-and-after check. It calls the repo's LLM and caches by content hash, so an unchanged plan never regenerates. Adding it to the surface is the opt-in step; generation also needs `refactoring.llm.enabled: true` in `.repowise/config.yaml` (off by default).

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `suggestion_id` | string | required | A plan `id` from `get_health` |
| `repo` | string | default repo | `"all"` not supported |

**Returns** the plan, code and diff; `generation.available: false` with `reason: "disabled"`, or `error: "no_provider"` ([fields](../reference/MCP_RESPONSE_FIELDS.md#generate_refactoring_code)).

```
generate_refactoring_code(suggestion_id="a1b2c3d4")
```

---

## `set_finding_status`

Opt-in. Records a durable verdict on one refactoring plan, surviving later analysis runs. Returns the new status and its timestamp.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `suggestion_id` | string | required | A plan `id` or `public_id` from `get_health` |
| `status` | string | required | `false_positive` (never re-emitted), `acknowledged` (visible, no longer unheard), `resolved` (stays resolved if the detector still fires), `open` (resets) |
| `reason` | string | `"agent"` | Free-text audit note |
| `repo` | string | default repo | `"all"` not supported |

```
set_finding_status(suggestion_id="a1b2c3d4", status="false_positive", reason="the class is a DTO")
```

---

## `get_blast_radius`

*Workspace only, opt-in.* Services in other repos structurally reachable from a changed service, ranked by an uncalibrated 0 to 1 path weight (not a breakage probability). Structural edges (HTTP, gRPC, events, packages) outrank co-change.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `targets` | list[string] | required | Node ids (`repo` or `repo::service/path`), repo aliases, or a published symbol's id |
| `max_depth` | int | `3` | Reachability depth, 1 to 8 |
| `include_behavioral` | bool | `true` | Include co-change edges |

**Returns** impacted services with `score` and `distance`, `impacted_repos`, `symbol_targets` with guarding tests ([fields](../reference/MCP_RESPONSE_FIELDS.md#get_blast_radius)).

```
get_blast_radius(targets=["backend"])
```

---

## `get_architecture`

*Workspace only, opt-in.* Whole-system structure from structural edges: propagation cost, the largest cyclic core, service roles, and a deterministic 1 to 10 score. No parameters.

**Returns** `score`, `architecture_type`, `propagation_cost_pct`, `core_members`, `role_breakdown` ([fields](../reference/MCP_RESPONSE_FIELDS.md#get_architecture)).

```
get_architecture()
```

---

## `get_conformance`

*Workspace only, opt-in.* Cross-repo dependencies breaking the rules under `conformance:` in `.repowise-workspace.yaml`, plus circular service dependencies. Without opting in, the same findings reach `get_risk`'s PR directive as `conformance_violations` and `dependency_cycles`.

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| `repo` | string | whole workspace | Limit to findings involving this repo |

**Returns** `violations`, `cycles`, `violation_count`, `rules_evaluated` ([fields](../reference/MCP_RESPONSE_FIELDS.md#get_conformance)).

```
get_conformance(repo="frontend")
```

---

## Workspace mode

A server started inside a [workspace](../scale/WORKSPACES.md) serves every repo from one process; `repowise mcp --no-workspace` forces single-repo mode for a nested repo. A `repo` parameter takes an alias, path or absolute path from `list_repos`; omitted, it targets the default repo.

| Tool | `repo="all"` |
|------|--------------|
| `search_codebase` | Yes: federated search, merged results |
| `get_overview` | Yes: cross-repo topology (co-changes, package dependencies, API contracts), no single-repo detail |
| `get_dead_code` | Yes: aggregated findings |
| `get_why` | Only with a `query`, not the dashboard or an `id` |
| `get_answer`, `get_context`, `get_symbol`, `get_risk`, `get_change_risk`, `get_health` | No |
| `get_dependency_path`, `get_execution_flows`, `generate_refactoring_code`, `set_finding_status` | No |
| `get_conformance` | No `"all"`; omitting `repo` covers the workspace |
| `get_blast_radius`, `get_architecture`, `list_repos` | No `repo` parameter; always the whole workspace |

Unsupported tools return an error naming the aliases. A `repo="all"` call queries 4 repos at a time with 60 seconds each (`REPOWISE_WORKSPACE_CONCURRENCY`, `REPOWISE_WORKSPACE_REPO_TIMEOUT_S`). Failed or timed-out repos are listed in `repos_failed` with a reason while the rest answer; repos behind their checkout are in `repos_stale` with `indexed_commit` and `live_head`, their rows marked `stale: true`. Both keys appear only when non-empty.

### Routing without `repo`

- `get_context`, `get_symbol`, `get_risk` and `get_health` route each path or `path::Name` target: the default repo if the file is there, else the one other repo that has it. Routed results carry `repo`.
- `<alias>:<target>` (e.g. `web:src/app.ts::App`) names the repo outright.
- A path in several other repos returns `candidates`; a path in none gets the default repo's not-found answer plus a `hint` naming `search_codebase(repo="all")` and the `<alias>:<path>` form.
- `get_context` takes a batch spanning repos; `get_risk` and `get_health` answer one repo per call and name the per-repo calls.
- `get_context` and `get_symbol` route a bare symbol name (`render`, `Client.send`) through the workspace symbol index the same way: the default repo keeps a name it has, one other repo gets the call, several return `candidates`.
- A `repowise#<ref>` ref is found in whichever repo's omission store holds it.

Single-repo tools also gain cross-repo evidence: `get_context` (`cross_repo`), `get_risk` PR mode (`will_break_consumers` and more), `get_change_risk` (`cross_repo`, with consumer-side tests), and `get_dead_code` (lower confidence on findings another repo imports). Fields: [MCP_RESPONSE_FIELDS.md](../reference/MCP_RESPONSE_FIELDS.md). Setup, contracts and the system graph: [WORKSPACES.md](../scale/WORKSPACES.md).

---

Hooks complement these tools with passive context: see [HOOKS.md](HOOKS.md) and [CODEX.md](CODEX.md).
