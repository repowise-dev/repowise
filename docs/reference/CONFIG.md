# Configuration

The `.repowise/` directory, `config.yaml` and its blocks, providers, keys and
environment variables.

## The `.repowise/` directory

Everything Repowise knows about your repository, created at the repo root by
`init`.

```
.repowise/
├── wiki.db             # SQLite database: all pages, symbols, graph, git metadata, decisions
├── lancedb/             # Vector search index (LanceDB)
├── omissions/           # Distill omission store + savings ledger (omissions.db)
├── config.yaml          # Provider, model, embedder, exclude patterns, distill/mcp/refactoring blocks
├── events.jsonl         # Typed events as CloudEvents JSON lines (the jsonl event sink)
├── events.key           # Random key the owner digests in component snapshots are made with
├── health-rules.json    # Per-file code-health marker overrides
├── state.json           # Last sync commit, page counts, token usage
├── mcp.json             # MCP server configuration
└── .env                 # API keys (gitignored automatically)
```

`.repowise/` is a local cache, not a source of truth: add it to your
`.gitignore` if you like, as Repowise does not edit that file during `init`. Only
[`decisions.yaml`](#repowisedecisionsyaml) is meant to be committed. The secrets
`.env` and `events.key` are ignored by `.repowise/.gitignore`, so they stay out
of git even where `.repowise/` is committed.

## `config.yaml`

`init` creates it; flags such as `--commit-limit`, `--follow-renames` and
`--wiki-style` update it. Hand edits apply on the next `init`, `update` or
`serve`.

The key and variable tables in this file are generated from the settings
registry in `repowise.core.settings`, so each lists every key or variable
its block reads. Where an environment variable sets a key, the `Env var`
column names it and says whether it or the file wins.

> **Limited validation.** The file loads as a plain YAML dict, so unknown or
> misspelled keys are silently ignored. Exceptions: they stop
> `repowise security check` in the `security:` block; `max_tokens` must be a
> positive integer when documentation is generated; `repowise doctor` validates
> the `distill:` block. Check spelling and indentation first.

```yaml
provider: anthropic
model: claude-sonnet-4-6
embedder: mock
embedding_model: text-embedding-3-small
reasoning: auto
max_tokens: 16384
commit_limit: 5000
follow_renames: false
wiki_style: comprehensive
language: en
enable_onboarding: true
max_file_pages: 2000
folder_coverage: ["src/core=1.0", "src/legacy=0.5"]
exclude_patterns: [vendor/, "*.generated.*", proto/]
distill: {enabled: true}
mcp: {tools: ["+get_execution_flows", "-get_dead_code"]}
```

<!-- BEGIN GENERATED settings:config-yaml (python scripts/gen_config_docs.py) -->
| Key | Default | Values | What it does | Env var |
|-----|---------|--------|--------------|---------|
| `provider` | unset |  | Which model service writes docs and answers (anthropic, openai, gemini, ollama, claude_cli and others). Unset: the saved global choice (provider_config.json), then REPOWISE_PROVIDER, then the first provider whose API key is present. | `REPOWISE_PROVIDER` (when the file is unset) |
| `model` | unset |  | The model id to use with the provider. Unset: the saved global choice (provider_config.json), then REPOWISE_MODEL, then the provider's default model. | `REPOWISE_MODEL` (when the file is unset) |
| `reasoning` | `auto` | `auto`, `off`, `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | How much the model thinks before writing. | `REPOWISE_REASONING` (wins over the file) |
| `max_tokens` | `16384` | > 0 | Most output tokens for one generated page. |  |
| `context_window` | unset | > 0 | Tokens (prompt plus answer) the model runs with; page material shrinks to fit. Unset: no limit for hosted models, 16384 for a local server (Ollama reports its own num_ctx). |  |
| `wiki_style` | `comprehensive` |  | How the wiki reads: comprehensive, caveman, reference, tutorial, or a custom style under .repowise/styles/. |  |
| `language` | `en` | `en`, `ru`, `es`, `fr`, `de`, `zh`, `ja`, `ko`, `it`, `pt`, `nl`, `pl`, `tr`, `ar`, `hi` | Language the docs are written in. |  |
| `max_file_pages` | unset | >= 0 | Most per-file pages to write; 0 means every file. Unset lets the repo size decide. |  |
| `folder_coverage` | `[]` |  | Least share of files per folder that get their own page, as "glob=0.5". |  |
| `enable_onboarding` | `true` |  | Write the onboarding pages. |  |
| `embedder` | unset |  | Which embedding service powers semantic search: openai, gemini, ollama, openrouter, edenai, a plugin's, or mock for none. Unset: the first embedder whose API key is present, then the embedder in ~/.repowise/config.yaml, then mock. | `REPOWISE_EMBEDDER` (wins over the file) |
| `embedding_model` | unset |  | The embedding model id. Unset uses the embedder's default. | `REPOWISE_EMBEDDING_MODEL` (wins over the file) |
| `exclude_patterns` | `[]` |  | Extra paths to skip, as gitignore patterns. |  |
| `commit_limit` | `5000` | 1-10000 | How many of the newest commits are analysed. Above 10000 reads as 10000. |  |
| `follow_renames` | `false` |  | Follow files across renames in git history. |  |
| `sql_dialect` | unset |  | SQL dialect for parsing .sql files (a sqlglot name such as postgres). |  |
| `forges.hosts` | `{}` |  | Which forge a self-hosted git host is: host to github, gitlab, azure, bitbucket or generic. |  |
| `editor_setup` | `true` |  | Let update keep agent and editor files current. init --no-editor-setup writes false, which leaves them alone. |  |
| `changes.outcome_fix_days` | `30` | 1-365 | Days after a change in which fix commits to its files are counted. |  |
<!-- END GENERATED settings:config-yaml -->

More on [`commit_limit`](#commit_limit), [`reasoning`](#reasoning),
[`max_tokens`](#max_tokens), [`max_file_pages`](#max_file_pages),
[`folder_coverage`](#folder_coverage), [`wiki_style`](#wiki_style) and
[`language`](#language) below; `exclude_patterns` in
[Exclude patterns](#exclude-patterns), `embedder` in
[Embeddings](#embeddings-for-semantic-search), `forges.hosts` in
[GIT_HOSTS.md](../start/GIT_HOSTS.md#self-managed-hosts), and
`changes.outcome_fix_days` under [`repowise changes`](CLI_REFERENCE.md#repowise-changes).

Blocks, each with its own section: [`generation_context`](#grounded-generation-context),
[`distill`](#the-distill-block), [`hooks`](#the-hooks-block), [`mcp`](#the-mcp-block),
[`editor_files`](#the-editor_files-block), [`decisions`](#the-decisions-block),
[`coverage`](#the-coverage-block), [`security`](#the-security-block),
[`attribution`](#the-attribution-block), [`agent_provenance`](#the-agent_provenance-block),
[`events`](#the-events-block), [`refactoring`](#the-refactoring-block),
[`assertions`](#the-assertions-block), [`tests`](#the-tests-block), and
[`<provider>.base_url`](#provider-endpoints-in-configyaml).
Code-health rules live in [`health-rules.json`](#the-health-rulesjson-file).

`--skip-tests`, `--skip-infra` and `--include-submodules` apply only to their
run and are not stored here; `--include-submodules` is recorded in `state.json`
(workspace state).

### `commit_limit`

- Sets how far back churn, co-change, the commit list and change records see.
- The first `repowise update` after upgrading re-reads history once, with no
  model calls.
- A value written here is kept; below the default, `update` says so once.

### `reasoning`

Applies to documentation-generation calls on reasoning-capable chat models.

- `auto` keeps provider defaults, except on Ollama, where it turns thinking off.
- `off` / `none` disable Qwen3-style thinking on OpenAI-compatible vLLM/SGLang
  (sends `extra_body.chat_template_kwargs.enable_thinking=false`) and map to
  OpenRouter's `reasoning.effort=none` for effort-capable families.
- `minimal` to `max` request that effort where supported (e.g. OpenAI
  reasoning models, OpenRouter's `reasoning.effort`).
- A provider or model that cannot translate an explicit mode fails before any
  API call.

### `max_tokens`

- One persistent setting for `init`, `update`, `generate`, `restyle`,
  workspace and server-triggered generation; providers may enforce less.
- A page that hits the limit before completing is rejected, not saved.

### `max_file_pages`

- Unset: the size policy decides, untouched below 4,500 documentable files and
  held to 4,500 above (about 1 repo in 100).
- `0`: one page per eligible file. Positive: a hard cap.
- `init` offers a tighter cap in advanced mode above 2,000 documentable files;
  `--max-file-pages N` sets it non-interactively. `update --full` and
  `generate` honour the recorded value.
- File pages are rendered from structure, so a cap does not reduce model spend.

### `folder_coverage`

- `"src/core=1.0"` gives every eligible code file under `src/core` a file page
  whatever its importance or the cap; `"src/legacy=0.5"` gives the top half.
- A floor, not a cap: pinned files are added to the selection and never
  displace a global pick, so the estimate rises with them.
- `init --folder-coverage GLOB=PCT` (repeatable) sets it. Every run reads it,
  and editing it re-renders the wiki on the next `update`.

### `wiki_style`

- Set with `init --wiki-style`, or `repowise restyle <style>`, which also
  regenerates the wiki. Hand-editing it and running `update` does not.
- A custom style lives in `.repowise/styles/<name>/style.yaml`
  ([WIKI.md](../layers/WIKI.md#styles)). An unknown name falls back to
  `comprehensive` in generation; the CLI and API validate it up front.

### `language`

- Code, paths and symbol names stay untranslated.
- Reaches model-written pages, and the headings and fixed sentences of
  template-rendered ones (file, symbol, infrastructure, cycle, API-contract)
  for languages with a label catalog; others keep English headings there.
- Set with `init --language <code>` or advanced interactive mode; `update`
  regenerates changed pages in it. Unknown codes fall back to English with a
  warning.
- Changing it does not retranslate existing pages: run
  `init --force --language <code>`.

## Grounded generation context

`generation_context` adds chosen repository files as evidence to model-written
synthesis pages. It is explicit selection, not discovery.

```yaml
generation_context:
  token_budget: 8000
  files:
    repo_overview: [docs/ARCHITECTURE.md]
    onboarding/how_it_works: [docs/runtime-flow.md]
```

<!-- BEGIN GENERATED settings:generation_context (python scripts/gen_config_docs.py) -->
| Key | Default | Values | What it does |
|-----|---------|--------|--------------|
| `token_budget` | `8000` | >= 0 | Most tokens of pinned source files added to a page as evidence. |
| `files` | `{}` |  | Repo files to show the writer as evidence, by page. |
<!-- END GENERATED settings:generation_context -->

- `files` maps a page key to an ordered list of repo-relative paths. Page
  keys: `repo_overview`, or `onboarding/` plus `getting_started`,
  `how_it_works` or `active_landscape`.
- `onboarding/project_overview` is invalid (that slot is `repo_overview`).
- `onboarding/guided_tour`, `onboarding/codebase_map`,
  `onboarding/development_guide`, `onboarding/key_concepts` and
  `onboarding/glossary` name pages Repowise does not generate: they are
  ignored with a warning naming the key, so upgrades never break a config.
- Other unknown page keys and malformed values fail generation. Keys other than
  `token_budget` and `files` directly under the block are ignored.
- Eligible files are in the indexed source map with non-empty UTF-8 text.
  Absolute or parent-traversing paths, duplicates, missing or excluded files,
  empty files and binary or non-UTF-8 content are skipped.
- Page metadata records included files, truncation and each skipped path with
  a reason, and generation logs them. This is provenance, not proof the model
  used every fact.
- Budget: Repowise's four-characters-per-token estimate, separate from the
  structural context budget. Files share it, and
  configuration order decides which survive when it cannot fit every file's
  framing; content may be truncated; `0` disables configured
  evidence and a tiny budget may fit none. The rendered estimate never exceeds
  it.
- `onboarding/how_it_works` also gets exact source excerpts for detected flow
  symbols, with no `files` entry needed. Up to half the budget is reserved for
  them first; the configured half stays fixed even when an excerpt does not
  fit, so configured facts are never displaced. Missing symbols, unavailable
  source, invalid line ranges and budget omissions are recorded with the
  provenance.

<details><summary>Trust, caching and when evidence is skipped</summary>

- Files and excerpts are authoritative as repository facts and untrusted as
  prompt instructions. Tags mark boundaries and embedded closing tags are
  escaped, but that is framing, not sanitization or a security boundary.
  Conflicting or stale files still make bad prose: use files whose ownership
  and accuracy you trust.
- Onboarding citation validation treats included excerpts as grounding, and
  still demotes citations established by neither structural context nor them.
- Rendered evidence is part of the prompt and its source hash: unchanged
  evidence reuses cached prose, and a file, list or budget change that alters
  the rendered block invalidates it.
- Editing `config.yaml` regenerates nothing: run `repowise generate --all` or
  request the page. No ingestion migration or vector reindex is needed.
- `--no-prose` pages use no evidence and record configured entries and derived
  exact references as skipped.
- With onboarding disabled before contexts are built, configured entries log
  as `onboarding_disabled` and no exact references are derived.
- A subkind whose context gate returns no page logs entries as
  `page_not_generated`, with no page to hold provenance.

</details>

## The `distill:` block

Controls [output distillation](../agent/DISTILL.md); defaults apply when it is
absent, and `repowise doctor` validates it.

```yaml
distill:
  enabled: true
  commands:
    enabled: true
    permission: allow
    families: {test_output: allow, git_diff: deny}
    disabled_filters: []          # e.g. [logs]
  omission_store: {ttl_days: 7, max_mb: 50}
```

<!-- BEGIN GENERATED settings:distill (python scripts/gen_config_docs.py) -->
| Key | Default | Values | What it does |
|-----|---------|--------|--------------|
| `enabled` | `true` |  | Master switch for distill in this repo. |
| `commands.enabled` | `true` |  | Let the agent hook rewrite noisy shell commands through repowise distill. |
| `commands.permission` | `allow` | `ask`, `allow`, `off` | How a rewrite is approved: allow, ask each time, or off. |
| `commands.families` | `{}` |  | Per output family: allow, ask, off or deny. |
| `commands.disabled_filters` | `[]` |  | Filters never applied, by name (for example logs). |
| `omission_store.ttl_days` | `7` | > 0 | Days a dropped piece of output stays recoverable. |
| `omission_store.max_mb` | `50` | > 0 | Size cap of the store of dropped output; oldest goes first. |
<!-- END GENERATED settings:distill -->

- `allow` auto-approves rewrites for the main agent and every subagent. It is
  not an escalation: a rewrite is always `repowise distill <one recognized
  command>` from a closed family set. `ask` shows each for approval; `off`
  disables rewrites here.
- Filter names: `test_output`, `build_output`, `lint_output`, `install_output`,
  `infra_plan`, `git_status`, `git_log`, `git_diff`, `git_diff_stat`,
  `search_results`, `file_listing`, `file_read`, `logs`. `repowise doctor` checks them
  against the live registry.
- Declining the `repowise init` opt-in writes `commands.enabled: false`, so a
  rewrite hook installed globally from another repo stays inert here.

## The `hooks:` block

Opt-in behaviour for the [agent hooks](../agent/HOOKS.md); every key is off
when absent. `REPOWISE_HOOK_<KEY>` (e.g. `REPOWISE_HOOK_READ_REREAD=1`)
overrides a key for one session.

<!-- BEGIN GENERATED settings:hooks (python scripts/gen_config_docs.py) -->
| Key | Default | Values | What it does | Env var |
|-----|---------|--------|--------------|---------|
| `read_skeleton` | `false` |  | Serve a whole-file Read of a large indexed file as its outline. | `REPOWISE_HOOK_READ_SKELETON` (wins over the file) |
| `read_reread` | `false` |  | Serve an unchanged repeat Read as a short notice. | `REPOWISE_HOOK_READ_REREAD` (wins over the file) |
| `search_digest` | `false` |  | Serve a search that floods many files as a digest. | `REPOWISE_HOOK_SEARCH_DIGEST` (wins over the file) |
| `coverage_reingest` | `false` |  | Re-read coverage reports after an agent's full test run. | `REPOWISE_HOOK_COVERAGE_REINGEST` (wins over the file) |
| `measure` | `false` |  | On update, check locally whether agents acted on each hook firing. | `REPOWISE_HOOK_MEASURE` (wins over the file) |
| `health_gate` | `false` |  | Block an agent's commit or push that fails repowise health check. | `REPOWISE_HOOK_HEALTH_GATE` (wins over the file) |
| `read_skeleton_mode` | `smart` | `smart`, `plus` | What a served outline keeps: smart, or plus to keep all code outside function bodies. | `REPOWISE_HOOK_READ_SKELETON_MODE` (wins over the file) |
<!-- END GENERATED settings:hooks -->

- `coverage_reingest` starts a process per shell command; see
  [test intelligence](../layers/TEST_INTELLIGENCE.md).
- `measure` reads this repo's recent agent transcripts locally, with no model,
  for `repowise hook stats`. `repowise hook backfill` does the same on demand
  either way.
- `health_gate` is set with `repowise hook health-gate install`
  ([CI](../start/CI.md#before-an-agent-commits-or-pushes)).

**`read_skeleton`**

- Once per file per session, the PostToolUse Read hook returns signatures with
  bodies as `... N lines (a-b)` markers (1-indexed), so a ranged Read recovers
  any span and a second Read returns the whole file.
- `plus` mode keeps imports, constants, class fields, decorators and comments,
  eliding only function and method bodies.
- A file whose skeleton is over the 10,000-character hook output cap is served
  as an outline: one line per symbol, long signatures cut, the same range
  markers. When the outline is still over the cap, it lists as many symbols as
  fit (top-level types first, then the longest functions) and says how many.
- A file edited since the last index is passed through whole, so the line
  numbers are never off.
- Savings show in `repowise saved` under `read_skeleton`. Needs Claude Code
  2.1.218+; older clients get the one-line pointer at
  `get_context(include=["skeleton"])`.
- The `repowise init` rewrite-hook question turns it on too (that question
  already asks whether Repowise's hooks may intervene, and a shell rewrite is
  the larger intervention); `--no-editor-setup` and `--no-distill-hook` turn
  it off. Per repo: `repowise hook read-skeleton install | uninstall | status`.
- While off, skeleton-eligible Reads are measured and `repowise saved` shows
  them under "Not saved": what replacement would have saved, and nothing about
  whether the agent wanted the whole file.

**`read_reread`**

- Answers a repeat Read with a notice naming the earlier read and its tool
  call, when the same range was served this session, no `Edit` or `Write`
  came between, and the bytes hash the same.
- A changed file is served in full, with a line saying it changed on disk
  outside this session's edits, which nothing else in the session can detect.
- Never twice in a row for one file: compaction can drop the earlier copy and
  a hook cannot tell, so the next Read returns content, as the notice says.
- Savings show under `read_reread`. Needs Claude Code 2.1.218+; older clients
  are untouched.

## The `mcp:` block

Chooses the tools the MCP server advertises. The default is curated: 10 tools
in single-repo mode, plus `list_repos` in workspace mode. `repowise mcp
--tools` / `--all` override it for one launch.

<!-- BEGIN GENERATED settings:mcp (python scripts/gen_config_docs.py) -->
| Key | Default | What it does |
|-----|---------|--------------|
| `tools` | unset | Which MCP tools are offered: all, lean, +name or -name to adjust the default, or plain names as the exact list. Unset offers the default set. |
<!-- END GENERATED settings:mcp -->

| `tools` value | Effect |
|---------------|--------|
| `["+name", "-name"]` | Add to or remove from the default set. |
| `["get_answer", "get_context"]` | Unprefixed: an explicit allowlist. |
| `all` | Everything available. |
| `lean` | `get_answer`, `get_context`, `get_symbol`, `search_codebase`, `get_risk`, `get_why` (plus `list_repos` in workspace mode), small enough for Claude Code to keep every schema loaded. |

The seven opt-in tools are `get_dependency_path`, `get_execution_flows`,
`generate_refactoring_code`, `set_finding_status` and the workspace-only
`get_blast_radius`, `get_conformance` and `get_architecture` (ignored in
single-repo mode). See [MCP_TOOLS.md](../agent/MCP_TOOLS.md#configuring-the-tool-surface).

## The `editor_files:` block

The generated block in `.claude/CLAUDE.md` and `AGENTS.md`, read into the
agent's context every turn, so the default is lean.

```yaml
editor_files:
  claude_md: true
  sections: ["+commands", "+fix_first"]
```

<!-- BEGIN GENERATED settings:editor_files (python scripts/gen_config_docs.py) -->
| Key | Default | What it does |
|-----|---------|--------------|
| `claude_md` | `true` | Write .claude/CLAUDE.md. |
| `agents_md` | `true` | Write AGENTS.md. |
| `cursor_rules` | `true` | Write the Cursor rules file. |
| `kiro_steering` | `true` | Write the Kiro steering file. |
| `vscode_mcp` | `true` | Write the VS Code MCP entry. |
| `sections` | `[architecture, decisions, entry_points, health, hotspots, how_to_work, key_modules]` | Sections of the generated agent block. +name or -name adjusts the default; plain names replace it. Extra: code_health, fix_first, commands, how_to_work_full, tools. |
<!-- END GENERATED settings:editor_files -->

- `false` on a file key stops Repowise writing that file.
- Default sections: `architecture`, `key_modules`, `entry_points`,
  `hotspots`, `health` (score and worst file), `decisions` (top three titles),
  `how_to_work` (which task goes to Grep and which to a Repowise call).
- Opt-in: `code_health` (all health signals, replaces `health`), `fix_first`,
  `commands` (build, test and lint from manifests), `how_to_work_full`,
  `tools` (the MCP tool table).

## The `decisions:` block

The decisions layer: one `mode` for the whole layer, then which sources
`init` mines, whether any may call a model, and the switches for each hook.
One resolved policy backs the CLI, API, hooks, MCP tools and index pipeline.
Every key is optional.

```yaml
decisions:
  mode: full
  preset: balanced
  sources:
    comment: false
    session: {enabled: true, llm: false}
    session_discovery: true
  discovery: {max_sessions: 12, max_input_tokens: 30000}
  harnesses: [claude_code, codex]
```

<!-- BEGIN GENERATED settings:decisions (python scripts/gen_config_docs.py) -->
| Key | Default | Values | What it does |
|-----|---------|--------|--------------|
| `mode` | `full` | `off`, `read_only`, `full` | The one switch for decisions: full (capture and show), read_only (show what exists, capture nothing) or off. |
| `preset` | unset | `default`, `off`, `local_only`, `balanced`, `full` | A named set of capture sources to start from. Unset reads as default. |
| `enabled` | `true` |  | Master switch for automatic capture. Stored decisions and manual entry work either way. |
| `llm` | `true` |  | Allow a model call while capturing decisions. Off keeps capture local. |
| `agent_acceptance` | unset |  | Let a coding agent accept a decision, signed as the agent's. Unset: depends on decisions.mode: off under off, read_only; on under full. |
| `capture_prompt` | unset |  | Ask the agent to record a decision when a commit states a choice. Unset: depends on decisions.mode: off under off, read_only; on under full. |
| `require_approval` | `false` |  | Make every new decision wait for a person before it applies. |
| `sync` | `false` |  | Send this repo's decisions to and from its copy on repowise.dev on update. |
| `session_start` | `false` |  | Open each agent session with the decisions relevant to its work. |
| `upkeep_prompt` | `false` |  | After a commit, ask the agent to settle the decisions it touched. |
| `update_mining` | unset |  | Let repowise update call a model to find decisions in new commits and PRs. Unset: off, unless the decisions block names a preset or lists sources, which keeps an older setup mining. |
| `discovery.max_sessions` | `12` | 1-24 | Most sessions read in one discovery pass. |
| `discovery.max_input_tokens` | `30000` | 2000-60000 | Most input tokens one discovery pass may send. |
| `harnesses` | `[claude_code]` |  | Which coding agents' local session transcripts may be read. |
<!-- END GENERATED settings:decisions -->

More on [`mode`](#mode), [sources](#sources), [presets](#presets),
[`require_approval`](#require_approval), [`sync`](#sync),
[`update_mining`](#update_mining), [`session_start`](#session_start) and
[`upkeep_prompt`](#upkeep_prompt) below.

### `mode`

The one switch for the layer. Each position sets the defaults of the finer
switches below; a switch you set explicitly wins over its mode's default,
except under `off`.

| Mode | Shows decisions | Records decisions | Defaults it sets |
|------|-----------------|-------------------|------------------|
| `full` | Yes | Yes | The defaults in the table above |
| `read_only` | Yes: the edit notice, `get_why`, `get_answer`, the agent-file pointer and pins, and pulls from repowise.dev when `sync` is on | No: no mining, no capture prompt, no agent putting a decision in force, nothing sent to repowise.dev, no landing granting force | `capture_prompt`, `agent_acceptance` off |
| `off` | No: every hook, MCP surface and the agent-file block stay silent; `get_why` answers that decisions are off | No | Everything off; explicit switches are ignored |

`repowise decision config mode off|read-only|full` sets it and resets the
finer switches to that mode's defaults; set one afterwards to differ. A bare
`mode: off` in YAML reads as off, and an unknown value reads as `read_only`
with a warning. Manual entry (`repowise decision add` by a person) works in
every mode.

### Sources

Keys under `decisions.sources.`:

<!-- BEGIN GENERATED settings:decisions-sources (python scripts/gen_config_docs.py) -->
| Key | Default | What it does |
|-----|---------|--------------|
| `inline_marker.enabled` | `true` | Capture from inline markers. WHY:/DECISION:/TRADEOFF: comment markers in source files. A preset may change the default. |
| `inline_marker.llm` | `true` | Allow a model call when capturing from inline markers. |
| `git_archaeology.enabled` | `true` | Capture from git archaeology. Commit messages carrying one of the decision verbs. A preset may change the default. |
| `git_archaeology.llm` | `true` | Allow a model call when capturing from git archaeology. |
| `adr.enabled` | `true` | Capture from ADR files. Nygard and MADR architecture decision records. A preset may change the default. |
| `adr.llm` | `true` | Allow a model call when capturing from ADR files. |
| `pr.enabled` | `true` | Capture from pull request bodies. Squash-merge and PR bodies that read as a description. A preset may change the default. |
| `pr.llm` | `true` | Allow a model call when capturing from pull request bodies. |
| `comment.enabled` | `true` | Capture from comment archaeology. Rationale prose in comments on high-centrality files. A preset may change the default. |
| `comment.llm` | `true` | Allow a model call when capturing from comment archaeology. |
| `session.enabled` | `false` | Capture from agent sessions. Durable choices mined from local coding-agent transcripts. A preset may change the default. |
| `session.llm` | `true` | Allow a model call when capturing from agent sessions. |
| `session_discovery.enabled` | `false` | Capture from session discovery. One broad model pass over new transcript prose each update. A preset may change the default. |
| `session_discovery.llm` | `true` | Allow a model call when capturing from session discovery. |
| `conventions.enabled` | `false` | Capture from conventions. Import patterns the graph proves: a wrapper most files reach a library through. A preset may change the default. |
<!-- END GENERATED settings:decisions-sources -->

- [Session mining](#session-mining) and [session discovery](#session-discovery)
  have their own sections below. `git_archaeology`, `pr`, `comment` and
  `session_discovery` are model-only.
- A source is a boolean or `{enabled, llm}`. `llm: false` matters only for
  sources with a deterministic stage (`inline_marker`, `adr`, `session`): it
  keeps the parse and skips the model.
- A model-only source with its model stage off is skipped entirely: it would
  produce a zero indistinguishable from an empty repository.
- Unknown keys warn instead of silently reading as a switch; `code_comment`,
  `changelog` and `readme_mining` are unknown keys.
- With no block, the policy is `default`: long-standing sources and model
  stages on, except `session`. `session_discovery` and `conventions` stay off
  until asked for, so upgrading never starts an unrequested model call.
- `session` ships off because its captures read as working agreements, not
  decisions. `repowise decision source set session --on` enables it;
  `local_only` includes it.
- A config naming a preset and listing sources keeps that list, so a source
  later added to the preset does not join; re-apply the preset.
- Legacy `decisions.session_mining: true|false` maps to `session` both ways;
  an explicit `sources.session` may narrow, never widen it. The first CLI or
  API write replaces it with `sources.session`.

### Presets

| Preset | Effect |
|--------|--------|
| `default` | No block. Long-standing sources on; session mining and broad discovery off. |
| `off` | No automatic capture; stored decisions and manual entry still work. |
| `local_only` | Deterministic only, session mining included (the only lane that produces without a key); zero extraction model calls. |
| `balanced` | High-signal sources, session mining and the discovery it feeds; comment archaeology off. |
| `full` | Every source and model stage. |

Editing a key after a preset drops the `preset` line; the policy reads `custom`.

Sources say what is mined at `init` (and `update --full`). Whether `update`
mines too is [`update_mining`](#update_mining), which no preset changes.

`llm: false` is complete, not degraded: transcripts are read, markers and ADRs
parsed, episodes recorded, manual decisions work and accepted ones govern. It
is the one switch proving no extraction reaches a model.

### `agent_acceptance` and `capture_prompt`

Neither is about capture, and no preset changes them.

- `agent_acceptance` on (default): an agent may accept, recorded as an
  agent's with the signing session, on every surface, never as yours. Off: an
  agent can propose and withdraw authority, never grant it. `false` is written
  to the config so it survives a reload.
- `capture_prompt` on (default): a successful `git commit` with two or more
  decision signals prompts the agent, once per session, to run
  `repowise decision add`; never for a commit a record already cites, and only
  as a proposal. `false` is written to the config so it survives a reload.
- It fires from a separate `Bash|PowerShell` PostToolUse entry, which `init`
  and the Claude Code plugin install. The entry tests the payload for
  `git commit` in the shell, so other shell calls never start Python.
- `repowise decision config capture-prompt --off` switches the prompt off for
  one repository and leaves the per-install entry, which stays silent there.
  `--on` reinstalls the entry if it is missing.

### `require_approval`

Off by default: a decision an agent records comes into force when the commit
it cites lands on the default branch. On, the landing grants nothing and an
agent's own acceptance is refused; a person puts each new decision in force
with `repowise decision confirm`. `repowise decision config approval --on`
writes it. A value that is not a boolean reads as on.

### `sync`

Off by default, and no preset changes it. On, and with this machine signed in
(`repowise login`) and a GitHub `origin`, every `update` runs
`decision push` then `decision pull` against the repo on repowise.dev. No
model runs, and a failure prints one line without failing the update. Under
`mode: read_only` only `pull` runs. A value that is not a boolean reads as off. `repowise decision config sync --on|--off`
writes it.

### `update_mining`

Off by default: `init` reads PR bodies, commit messages and comments once,
and the records it mines are background, served by `get_why` when asked.
`update` then makes no model call for decisions. Inline markers are still read
on changed files, and decisions an agent recorded still come into force when
their commit lands.

- On: each `update` also mines new PR bodies and commits, structures new
  inline markers, and asks the model whether a change replaced a decision it
  touches. `repowise decision config update-mining --on`.
- With no `update_mining` key, a `decisions:` block that names a `preset` or
  lists `sources` reads as on, so a repository configured before this setting
  keeps mining on update. No block reads as off. The key is written on every
  CLI or API write.
- While off, each source's mining pointer stays where it was; switching on
  mines the missed range, bounded by each miner's cap.

### `session_start`

Off by default: an agent sees a decision when it edits a file the decision
governs, and `get_why` answers for any path, so a session that never touches
governed code pays nothing for decisions.

- On: the session start hook also lists up to six decisions in force over the
  files the session is likely to touch (changed, staged, on the branch, or
  edited last session), plus at most two labelled background records.
  `repowise decision config session-start --on`.

### `upkeep_prompt`

Off by default. On, a successful `git commit` that changes files governed by
decisions in force gets one line naming up to three of them, each once per
session, with the one command for each outcome:

- still holds: `repowise decision reaffirm <id> --agent <slug>`
- replaced by this change: `repowise decision deprecate <id> --superseded-by <new id> --agent <slug>`
- knowingly broken: `repowise decision deprecate <id> --reason '<why>' --agent <slug>`

It fires from the same shell entry as `capture_prompt`; `repowise decision
config upkeep-prompt --on` installs it if missing. Every firing is logged, and
`repowise hook stats` reports how often a session settled a decision it named.

### From the CLI

```bash
repowise decision config show          # the resolved policy, per source
repowise decision config mode read-only  # off | read-only | full
repowise decision config preset local_only
repowise decision source set comment --off
repowise decision source set adr --no-llm     # keep the parse, skip the model
repowise decision source set session_discovery --on
repowise decision config discovery --max-sessions 6 --max-input-tokens 12000
repowise decision llm --off
```

Mutating commands take `--dry-run` and `--format json`; writes are atomic and
keep unrelated keys.

### Session mining

`session` (off by default) lets `repowise update` read coding-agent transcripts
(Claude Code's `~/.claude/projects/`) for user corrections, explicit choices
with a stated reason, and failed approaches replaced by working ones.

- Deterministic gates first, then one batched LLM structuring call per update;
  any field not quoting the transcript verbatim is dropped.
- Mined records are background however often they recur; `repowise decision
  confirm` puts one in force.
- All local: transcripts are read on your machine, staging is
  `.repowise/sessions/sessions.db`, and only distilled decision text is stored.

### Session discovery

`session_discovery` sends the prose an update newly read (bounded by
`discovery.max_sessions` and `discovery.max_input_tokens`) to the model in one
call per update, for decisions the gates missed. It reuses the `session`
source's read and needs it on.

- Each candidate cites span ids, and each quote is verified against the span.
  Citing an unsent span, quoting nothing a span says, or naming a file the
  cited turns never touched is rejected and counted.
- Scope is the files those turns' tools touched; the model may pick among them,
  not add.
- Output is background (`proposed`); only `repowise decision confirm` puts it
  in force.
- Prose over budget queues in `sessions.db` and goes oldest-first next update,
  never aged out unread. A provider failure retries it on the next two updates
  before dropping it.
- Switching off keeps the queue; prose read while off is never backfilled.

Dismissals are sticky: `repowise decision dismiss` keeps a `dismissed`
tombstone, so reindexing never re-proposes it, and re-extraction never moves a
confirmed (`active`) decision back to `proposed`.

## `.repowise/decisions.yaml`

The committed record of decisions in force; the rest of `.repowise/` can be
deleted and rebuilt.

- `repowise decision export` writes it and un-ignores it if a `.repowise/`
  rule hid it; `repowise decision import` reconciles the store to it.
- It carries a `version`; a file from a newer Repowise is refused, not
  downgraded. Round trip: [DECISIONS.md](../layers/DECISIONS.md).

## `.repowise/components.yaml`

Components are what health, hotspots, bus factor, test reach and dead code roll
up to. The file is multi-document Backstage YAML (`Component`, `System`,
`Group` on `backstage.io/v1alpha1`); a repo-root `catalog-info.yaml` is read
the same way, so a developer portal shares it. See
[examples/components](../../examples/components/components.yaml) and
`repowise components check`.

| Annotation | Holds |
|------------|-------|
| `repowise.dev/paths` | Gitignore-style globs with `/`, case-sensitive. |
| `repowise.dev/criticality` | `low`, `medium`, `high`, `critical`. |
| `repowise.dev/tags`, `repowise.dev/capability` | Tags and capability. |

- Without a file, components derive from workspace services, then module pages,
  then top-level directories.
- A declared component claims files its globs match; the first declared wins.
  One named like a derived component replaces it and, without paths, keeps its
  directory.
- Derived names come from the directory, skipping route parameters and groups
  (`[id]`, `(group)`). On a clash the shallowest keeps the name and the others
  add their nearest parent other than `src`, `lib` or `app` (`workspace-web`),
  so catalog names stay stable.

## `.repowise/policies.yaml`

The change policy `repowise check` evaluates.

```yaml
version: 1
rules:
  - name: agent-pci-needs-owner
    match:
      components: {tags: [pci]}          # also names, criticality; each list is any-of
    when:
      attribution: agent                 # agent, human_attested or unknown
      reaching_tests: missing            # a changed file in scope has no reaching test
    action: require_owner_review
  - name: no-new-secrets
    gate: security
    with: {fail_on: high, baseline: .security-baseline.json}
    action: fail                         # the default for a gate
```

| Rule key | What it does |
|----------|--------------|
| `name` | Unique; lowercase letters, digits, `_`, `-`, starting alphanumeric, at most 63 characters. |
| `description`, `message` | Optional text. |
| `match`, `exclude` | Select components by `names`, `tags`, `criticality`. |
| `when` | Conditions; all must hold. |
| `action` | `warn`, `require_owner_review`, `require_tests`, `fail`. |
| `gate`, `with` | Wrap `security`, `coverage`, `doc_drift` or `health` with that command's options. |

- Conditions: `attribution`, `agent_tier` (1, 2 or 3), `reaching_tests`
  (`missing` or `present`), `contract_break`, `decision_crossed`,
  `bus_factor_below`. No risk-score condition.
- Missing evidence (no index, attribution off) leaves a rule unevaluated; it
  never fires.
- A wrapped gate runs exactly as the command alone; `doc_drift` is scoped to
  the change (`since`) unless set.
- `require_owner_review` and `require_tests` are advisory: reported in every
  format, exit 0. Only `fail` exits 1.
- Exit 2 (`policy_invalid`, all problems listed): unknown key, condition or
  gate option, bad action, duplicate name, or a file over 256,000 bytes. Also
  exit 2 when a `fail` gate cannot be evaluated.

## `.repowise/teams.yaml`

Teams whose members count as the team's activity, for `repowise components
ownership` and `get_context(include=["ownership"])` on a component.

```yaml
apiVersion: backstage.io/v1alpha1
kind: Group
metadata:
  name: payments
  annotations:
    repowise.dev/member-emails: "ana@acme.com, li@acme.com"
spec:
  type: team
  children: []
  members: [ana-acme, li-acme]
```

- `spec.members` lists forge logins; `repowise.dev/member-emails` lists git
  author emails. Members only match commits and never appear in output.
- CODEOWNERS `@org/team` (GitHub or GitLab, sections included) and a catalog
  `spec.owner` of `group:default/team` resolve to the Group named `team`.
- Group entities in catalog files add team names; their `spec.members` is not
  read.

## The `coverage:` block

Coverage reports and gates; behaviour in
[test intelligence](../layers/TEST_INTELLIGENCE.md#configuration), gates in
[CI.md](../start/CI.md#gate-on-coverage).

<!-- BEGIN GENERATED settings:coverage (python scripts/gen_config_docs.py) -->
| Key | Default | Values | What it does |
|-----|---------|--------|--------------|
| `auto_discover` | `true` |  | Look for coverage reports in the usual places. |
| `artifacts` | `[]` |  | Where to look for coverage reports instead of the usual places. |
| `paths` | `[]` |  | Report paths or globs from the repo root, read without discovery. An entry may be {path, path_prefix}. |
| `ignore` | `[]` |  | Files coverage leaves out. |
| `format` | unset |  | Force a report format instead of detecting it. |
| `strip_prefix` | unset |  | Path prefix to strip from report paths. |
| `path_prefix` | unset |  | Path prefix to add to report paths. |
| `reingest_on_update` | unset |  | Re-read reports on every update. Unset re-reads only newer reports. |
| `fail_under` | unset | 0-100 | The coverage gate fails below this percent. |
| `fail_under_risky` | unset | 0-100 | The coverage gate fails when risky files are below this percent. |
| `max_drop` | unset | 0-100 | The coverage gate fails when coverage falls from the base by more than this many points. |
| `fail_under_branches` | unset | 0-100 | The coverage gate fails below this branch percent. |
| `min_coverable_lines` | unset | >= 0 | Changes with fewer coverable lines than this pass the gate. |
| `gates` | `[]` |  | Coverage gates scoped to paths. |
<!-- END GENERATED settings:coverage -->

`gates` holds [path-scoped gates](../start/CI.md#path-scoped-gates).

Unset `reingest_on_update`: `repowise update` re-reads when a report in
`coverage.paths` or at a fixed discovery location (e.g. `coverage/lcov.info`)
is newer than the last ingest, replacing hand-added coverage. A report only a
`**` glob finds is not watched; list it in `paths`.

## The `security:` block

Your own secret shapes for `repowise security check`.

```yaml
security:
  patterns:
    - name: internal_token
      regex: 'itk_[A-Za-z0-9]{32}'
      severity: high
```

<!-- BEGIN GENERATED settings:security (python scripts/gen_config_docs.py) -->
| Key | Default | What it does |
|-----|---------|--------------|
| `patterns` | `[]` | Your own secret shapes for security check, each {name, regex, severity}. |
<!-- END GENERATED settings:security -->

Each pattern:

| Field | Default | What it does |
|-------|---------|--------------|
| `patterns[].name` | required | 1 to 40 of `a-z`, `0-9`, `_`, `-`; unique. |
| `patterns[].regex` | required | The secret shape. |
| `patterns[].severity` | `high` | `low`, `med`, `high`. |

- Each is secret kind `custom:<name>` (SARIF rule `custom/<name>`): changed
  lines only, every commit of the change, matches masked to four characters
  and `****` in every format, severity kept under test, fixture and example
  paths.
- Findings key on file, kind and masked line, so same-prefix matches on one
  line share a baseline entry.
- At most 50 patterns of 500 characters. Lines over 4096 characters are skipped
  and counted (`long_lines_skipped`); a slow regex that passes the checks stays
  slow.
- Exit 2 (`config_invalid`, all problems listed): unknown key, missing name or
  regex, duplicate or badly shaped name, bad severity, or a regex that does not
  compile, can match empty, or nests repetition like `(a+)+` or `(a|aa)+`
  (best effort).

## The `attribution:` block

<!-- BEGIN GENERATED settings:attribution (python scripts/gen_config_docs.py) -->
| Key | Default | Values | What it does |
|-----|---------|--------|--------------|
| `enabled` | `true` |  | Read public AI-authorship markers: bot authors, commit trailers and footers, git-ai notes, Agent Trace files. The same key in ~/.repowise/config.yaml can turn it off for every repo. |
| `capture` | `off` | `off`, `local`, `notes` | Record listed agents' edits as they write them: local keeps line hashes in .repowise/attribution/, notes also writes git-ai notes on refs/notes/ai. The lower of this and the same key in ~/.repowise/config.yaml applies, so both must turn it on; DO_NOT_TRACK turns it off. |
| `agents` | `[]` |  | Agent target ids whose edits are recorded, such as claude-code or codex. Only agents named both here and in ~/.repowise/config.yaml are recorded. |
<!-- END GENERATED settings:attribution -->

- `~/.repowise/config.yaml` may hold the same block for every repository; the
  stricter value wins per key (`enabled: false` in either, the lower capture,
  agents both list). A key unset in one file takes its default first, so
  capture needs both files: repo-only `capture: local` resolves to `off`.
- `enabled: false` stores and shows nothing about AI in the API, CLI, MCP, web
  or wiki. `DO_NOT_TRACK=1` keeps capture off.
- `repowise attribution status` shows effective, repo and user values and
  history coverage. What leaves the machine: [ATTRIBUTION.md](../layers/ATTRIBUTION.md).
- The same consent lets `repowise attribution backfill --agent
  claude-code,codex` bind past commits from those agents' local transcripts.

## The `agent_provenance:` block

Your own bots and footers, added to the built-in agent markers
([ATTRIBUTION.md](../layers/ATTRIBUTION.md)).

```yaml
agent_provenance:
  service_emails:
    mybot@example.com: {agent: mybot, tier: 1}
  footer_patterns:
    - {pattern: "Generated by MyBot", agent: mybot}
  coauthor_patterns:
    - {pattern: "^co-authored-by: mybot <[^>]*@example\\.com>", agent: mybot}
```

<!-- BEGIN GENERATED settings:agent_provenance (python scripts/gen_config_docs.py) -->
| Key | Default | What it does |
|-----|---------|--------------|
| `service_emails` | `{}` | Your bots' commit emails, each mapped to {agent, tier}, so their commits count as that agent's. |
| `footer_patterns` | `[]` | Commit-message footer regexes that mark an agent's commit, as {pattern, agent}. |
| `coauthor_patterns` | `[]` | Co-author trailer regexes that mark an agent's commit, as {pattern, agent}. |
<!-- END GENERATED settings:agent_provenance -->

## The `events:` block

<!-- BEGIN GENERATED settings:events (python scripts/gen_config_docs.py) -->
| Key | Default | Values | What it does |
|-----|---------|--------|--------------|
| `enabled` | `true` |  | Snapshot components each index run and record what changed. |
| `retention_days` | `365` | > 0 | Days runs and their events are kept. |
| `compact_after_days` | `30` | > 0 | Days after which only one run a week is kept. |
| `kinds.ownership_shift` | `true` |  | Record when a file's main owner changes. |
| `kinds.owner_inactive` | `true` |  | Record when a file's owner goes quiet. |
| `kinds.bus_factor_drop` | `true` |  | Record when fewer people know a component. |
| `kinds.new_hotspot` | `false` |  | Record when a file becomes a hotspot. |
| `kinds.contract_added` | `true` |  | Record when a new API contract appears. |
| `kinds.contract_broken` | `true` |  | Record when an API contract breaks. |
| `kinds.decision_stale` | `true` |  | Record when a decision goes stale. |
| `kinds.dead_code_growth` | `true` |  | Record when dead code grows. |
| `kinds.test_reach_drop` | `true` |  | Record when test reach drops. |
| `sinks` | `[]` |  | Where events go: jsonl, stdout or a plugin sink. Empty writes jsonl. |
<!-- END GENERATED settings:events -->

- A run diffs against the previous one when both share the analyzer and parser
  stamp; a stamp change or an intervening run without a snapshot starts a new
  baseline.
- Owners compare as digests keyed by `.repowise/events.key`; a declared owner
  is named only when it is a group. Severity follows `repowise.dev/criticality`.
- `repowise doctor` names values that fell back to defaults. Read events with
  `repowise events` or `GET /api/repos/{id}/events`.

## The `refactoring:` block

Tunes the Extract Class / Extract Method / Extract Helper / Move Method / Break
Cycle / Split File plans behind `repowise health --refactoring-targets`,
`get_health(include=["refactoring"])` and the web Refactoring tab.

<!-- BEGIN GENERATED settings:refactoring (python scripts/gen_config_docs.py) -->
| Key | Default | Values | What it does |
|-----|---------|--------|--------------|
| `enabled` | `true` |  | Work out refactoring plans in the health pass. |
| `detectors.disabled` | `[]` |  | Refactoring detectors to silence, such as extract_class or split_file. |
| `min_confidence` | unset | `low`, `medium`, `high` | Least confidence a refactoring plan needs to show. |
| `llm.enabled` | `false` |  | Let a model write refactoring code when asked; indexing never calls it. |
<!-- END GENERATED settings:refactoring -->

- Code generation uses chat's provider and model, and is the only part that
  calls a provider.
- `min_confidence` applies at detection, so plans below it are never persisted
  (like a disabled marker); changes apply on the next `init` / `update`. A
  `min_confidence` query parameter can only narrow further.
- Per-path disables use the `health-rules.json` glob mechanism markers use.
  Full reference: [REFACTORING.md](../layers/REFACTORING.md).

## The `assertions:` block

Your own assertion helpers, for test-quality markers that divide by a test's
assertion count.

```yaml
assertions:
  extra_names: [ensureInvariant, mustMatch]   # exact callee names, not prefixes
```

<!-- BEGIN GENERATED settings:assertions (python scripts/gen_config_docs.py) -->
| Key | Default | What it does |
|-----|---------|--------------|
| `extra_names` | `[]` | Your own assertion helpers, by exact name, counted as assertions. |
<!-- END GENERATED settings:assertions -->

Repowise knows the xUnit and BDD families (`assert*`, `expect*`) and
per-language idioms such as Go's `t.Fatalf` and testify's `require`. A house
helper outside those is invisible, and a test of only such helpers reads as
assertion-free; this block is the escape hatch other test linters also offer.

- Names are exact and case-insensitive, never prefixes: `ensureInvariant`
  does not match `ensureConnectionPool`. Prefixes, measured on three real test
  corpora, matched production functions more often than assertions.
- A name counts as the call or as its receiver (`ensureInvariant(x).isTrue()`).
- These names reach only the advisory assertion total; the calibrated
  `large_assertion_block` and `duplicated_assertion_block` markers use a
  separate count, so a wrong name costs signal, never a score.
- Changes re-walk affected files on the next `init` / `update`.

## The `tests:` block

Rules `repowise impacted-tests` adds to its selection; see
[CI.md](../start/CI.md#selecting-the-tests-a-change-needs).

```yaml
tests:
  full_run_on: [schema/**, "*.proto"]
  always_run: [tests/smoke/test_boot.py]
```

<!-- BEGIN GENERATED settings:tests (python scripts/gen_config_docs.py) -->
| Key | Default | What it does |
|-----|---------|--------------|
| `full_run_on` | `[]` | Gitignore patterns whose change makes impacted-tests select every test. |
| `always_run` | `[]` | Tests added to every impacted-tests selection, as written. |
<!-- END GENERATED settings:tests -->

## The `health-rules.json` file

A separate JSON file tuning code-health biomarkers: which run, their severity,
and per-path overrides. Malformed JSON warns and falls back to an empty config.

```json
{
  "profile": null,
  "disabled_biomarkers": [],
  "severity_overrides": {"high_churn": "low"},
  "rules": [
    {
      "path": "legacy/**",
      "disabled_biomarkers": ["long_function"],
      "severity_overrides": {"duplication": "critical"}
    }
  ]
}
```

| Key | Default | What it does |
|-----|---------|--------------|
| `profile` | `null` | Calibration profile; only `"small-team"` exists. |
| `disabled_biomarkers` | `[]` | Biomarkers skipped repo-wide. |
| `severity_overrides` | `{}` | Biomarker name to severity label. |
| `rules[]` | `[]` | Per-path overrides, in order. |
| `rules[].path` | required | Gitignore-style glob (`*` stops at `/`; use `**` for nested files); aliases `path_glob`, `glob`. |
| `rules[].disabled_biomarkers`, `rules[].severity_overrides` | `[]`, `{}` | As above, for matching files. |

Labels are `low`, `medium`, `high`, `critical`; only the label is overridable,
never the weights or caps behind the score.
Full reference: [CODE_HEALTH.md](../layers/CODE_HEALTH.md#configuration).

## `~/.repowise/platform.json`

Choices for this machine's user, read in every repository.
`repowise telemetry disable` writes `telemetry_enabled`; the variables under
[Telemetry and account](#telemetry-and-account) override both keys.

<!-- BEGIN GENERATED settings:platform-json (python scripts/gen_config_docs.py) -->
| Key | Default | What it does |
|-----|---------|--------------|
| `telemetry_enabled` | `true` | Send anonymous usage counts. DO_NOT_TRACK, REPOWISE_TELEMETRY_DISABLED and REPOWISE_OFFLINE turn it off whatever this says. |
| `hints_enabled` | `true` | Show occasional repowise.dev tips in the CLI and web UI. |
<!-- END GENERATED settings:platform-json -->

## LLM providers

### Anthropic (Claude)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
repowise init --provider anthropic --model claude-haiku-5-5
```

Models: `claude-haiku-5-5` (default, fastest, cheapest), `claude-sonnet-5-5`
(best balance), `claude-opus-5-5` (highest quality, higher cost).

### OpenAI (GPT)

```bash
export OPENAI_API_KEY="sk-..."
repowise init --provider openai --model gpt-5.6-luna
```

An OpenAI-compatible Qwen3 endpoint on vLLM or SGLang:

```bash
export OPENAI_BASE_URL="http://localhost:8000/v1"
repowise init --provider openai --model qwen3 --reasoning off
```

The same adapter serves local gateways such as 9router and any
OpenAI-compatible provider: the gateway key in `OPENAI_API_KEY`,
`OPENAI_BASE_URL` at its `/v1` endpoint, and a model id from its `/models`
(namespaced ids work as-is, no `gpt-` prefix needed). For another local
provider, swap the key, URL and model.

```bash
export OPENAI_API_KEY="your-9router-dashboard-key"
export OPENAI_BASE_URL="http://localhost:20128/v1"
repowise init --provider openai --model ag/gemini-3.7-flash-medium
```

<details><summary>Interactive gateway setup</summary>

In interactive `repowise init`, choose **OpenAI-compatible (Custom / local
gateway)**. Repowise validates the endpoint, takes the key without echo,
verifies it through `/models`, and lets you search the models or type an id.
Without `/models` you can retry, enter an id manually, or go back:

```text
Base URL [http://localhost:20128/v1]:
API key (hidden): <paste the gateway key>
✓ Connected — discovered 47 model(s).
Select model: ag/gemini-3.7-flash-medium
```

- The endpoint goes in the gitignored `.repowise/.env`; the key only after
  confirmation, never with `--no-save-key`.
- `config.yaml` gets `provider: openai` and the exact model id.
- The separate **openai** row is the official endpoint.
- Scripted runs (`--yes`, CI, non-TTY) stay non-interactive: use environment
  variables and `--provider openai`.

</details>

### OpenRouter

```bash
export OPENROUTER_API_KEY="sk-or-..."
repowise init --provider openrouter --model openai/gpt-5 --reasoning minimal
repowise init --provider openrouter --model x-ai/grok-4 --reasoning off
```

### Gemini (Google)

```bash
export GEMINI_API_KEY="AI..."      # or GOOGLE_API_KEY
repowise init --provider gemini
```

With `GEMINI_API_KEY` set, Gemini is also the default embedder.

### DeepSeek

```bash
export DEEPSEEK_API_KEY="..."
repowise init --provider deepseek --model deepseek-flash
```

### Kimi

```bash
export KIMI_API_KEY="..."
repowise init --provider kimi --model kimi-for-coding
```

The default Kimi Code endpoint serves `kimi-for-coding` and
`kimi-for-coding-highspeed`. K2.x models need a Kimi Open Platform key and its
OpenAI-compatible endpoint. Repowise sets the required sampling parameters for
both, including K2.x thinking or instant mode.

```bash
export KIMI_BASE_URL="https://api.moonshot.ai/v1"
repowise init --provider kimi --model kimi-k2.6
```

### Ollama (local, no API key)

```bash
export OLLAMA_BASE_URL="http://localhost:11434"
repowise init --provider ollama --model llama3.2
```

- The window is the model's own `num_ctx` if its Modelfile sets one, else
  16384, never above its trained context length. Page material (code
  excerpts, source evidence) shrinks to fit it, least important first, so a
  4B model on a laptop writes a page in seconds instead of minutes.
- `REPOWISE_OLLAMA_NUM_CTX` pins `num_ctx` and sizes material to it, which
  gives a bigger model more to read; `context_window` in `config.yaml` sizes
  material only, and `num_ctx` grows to hold the prompt.
- `reasoning: auto` turns thinking off for page generation; `low`, `medium` or
  `high` turn it on, and the window grows to hold the longer answer.
- Requests go one at a time; with server `OLLAMA_NUM_PARALLEL` above 1, set the
  same value where Repowise runs.

### LiteLLM (100+ providers)

```bash
export LITELLM_API_KEY="..."
repowise init --provider litellm --model azure/gpt-4
```

### Eden AI (hosted, with an EU endpoint)

[Eden AI](https://www.edenai.co/) is a hosted gateway with an EU endpoint, so
inference stays in the EU without self-hosting. Every other hosted provider
here terminates in the US; under a DPA the alternatives are Ollama or LiteLLM
on your own infrastructure. `EDENAI_BASE_URL` selects the EU endpoint for the
LLM and the embedder; without it, requests use the global endpoint.

```bash
export EDENAI_API_KEY="..."
export EDENAI_BASE_URL="https://api.eu.edenai.run/v3"
repowise init --provider edenai --model mistral/mistral-small-latest
repowise init --provider edenai --model openai/gpt-5-mini --reasoning low
```

- One key reaches many vendors (Mistral, GPT, Claude, Gemini, Cohere,
  DeepSeek, Llama) through one OpenAI-compatible endpoint, as `vendor/model`.
- Public catalogue, no auth: <https://api.edenai.run/v3/models>; embedding
  models: <https://api.edenai.run/v3/embeddings/models>.
- `reasoning` maps to OpenAI `reasoning_effort` for OpenAI reasoning models
  (e.g. `openai/gpt-5*`); other models accept only `auto`.
- The key also selects the `edenai` embedder, default
  `amazon/amazon.titan-embed-text-v2:0` (EU-served, cheapest per token here).
  Change it with `REPOWISE_EMBEDDING_MODEL`, e.g.
  `google/gemini-embedding-001`.
- Only models in `EdenAIEmbedder._DIMS` are accepted, since a wrong width
  silently mis-sizes stored vectors; adding one means adding its measured width.

### Provider auto-detection

The CLI, `repowise serve` chat and jobs, and the MCP server resolve provider,
model and base URL the same way; first match wins:

1. `--provider` / `--model` flags, or a per-request choice in the web UI
2. `provider`, `model`, and `<provider>: {base_url: ...}` in `.repowise/config.yaml`
3. the global default saved from the web UI (`~/.repowise/provider_config.json`)
4. `REPOWISE_PROVIDER` / `REPOWISE_MODEL` and the provider's `*_BASE_URL` variable
5. API key env vars: `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `OPENROUTER_API_KEY` → `OLLAMA_BASE_URL` → `GEMINI_API_KEY` → `DEEPSEEK_API_KEY` → `KIMI_API_KEY` → `EDENAI_API_KEY`

- A model picked in the web UI is written to the repo's `config.yaml`, so the
  CLI uses it too.
- A local OpenAI-compatible server such as LM Studio: `provider: openai` with
  `openai: {base_url: http://localhost:1234/v1}`. Loopback URLs need no key.
  Page material is sized for a 16384-token window, since the server does not
  report its own; start the server with at least that context, or set
  `context_window` to the context length it runs with. A
  local backend behind LiteLLM is sized the same way.
- Since `config.yaml` may be committed, a non-local base URL there gets a key
  not in `.repowise/.env` only after you confirm in Settings or `repowise init`.
- The repo's Settings page sets all this: the model field lists live models
  and takes any id, and Test connection checks a base URL before saving.

### Provider endpoints in `config.yaml`

A provider's block in `.repowise/config.yaml` names its endpoint, ahead of
the provider's `*_BASE_URL` variable (order above).

```yaml
provider: openai
openai: {base_url: http://localhost:1234/v1}
```

<!-- BEGIN GENERATED settings:provider-endpoints (python scripts/gen_config_docs.py) -->
| Key | Default | Values | What it does |
|-----|---------|--------|--------------|
| `<provider>.base_url` | unset | provider: `gemini`, `anthropic`, `openai`, `deepseek`, `kimi`, `edenai`, `ollama`, `litellm` | Custom http(s) endpoint for a provider (openai.base_url and the like), such as a proxy or a local server. |
<!-- END GENERATED settings:provider-endpoints -->

## Embeddings (for semantic search)

The embedder is separate from the LLM provider.

| Embedder | Env var | Notes |
|----------|---------|-------|
| `gemini` | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | Default when the key is present |
| `openai` | `OPENAI_API_KEY` | `text-embedding-3-small` |
| `openrouter` | `OPENROUTER_API_KEY` | Routed through OpenRouter |
| `ollama` | `OLLAMA_EMBEDDING_MODEL` | Local, no API key |
| `edenai` | `EDENAI_API_KEY` | See [Eden AI](#eden-ai-hosted-with-an-eu-endpoint) |
| `mock` | n/a | No semantic search; default when no key is found |

```bash
repowise init --embedder openai
repowise reindex --embedder gemini   # switch embedder and rebuild index
```

Resolution, first match wins: `--embedder`, `REPOWISE_EMBEDDER`, the
`embedder` pin in `.repowise/config.yaml`, the first key present (gemini,
openai, openrouter, ollama, edenai), `embedder` in `~/.repowise/config.yaml`,
then `mock`. `reindex` skips a `mock` pin when a real embedder is found.

- The pin records what built the vector store. `search`, `serve` and MCP read
  it first and use the environment only when unpinned; otherwise a keyless
  index would be queried with whatever key is exported, and wider query vectors
  would match nothing in the narrower table.
- `reindex` writes its embedder back to the pin; otherwise the next `update`
  would write old-width vectors and rebuild the store, discarding the reindex.
- `REPOWISE_EMBEDDING_MODEL`, `REPOWISE_EMBEDDING_DIMS` and
  `REPOWISE_EMBEDDING_TIMEOUT` apply to the active embedder; [provider-prefixed
  variants](#embeddings) take precedence for theirs.

### Gemini Embedding 2

The `gemini` embedder supports `gemini-embedding-2`; its default remains
`gemini-embedding-001`. To switch an existing index:

```bash
export GEMINI_API_KEY="..."
export REPOWISE_EMBEDDER=gemini
export REPOWISE_EMBEDDING_MODEL=gemini-embedding-2
export REPOWISE_EMBEDDING_DIMS=768
repowise reindex --embedder gemini
```

Keep the same model and dimension settings in the environment of `update`,
`serve` and the MCP server. Embedding 2 and Embedding 001 use incompatible
vector spaces, even at the same dimension, so switching requires a full
reindex. Restart a running server after switching.

Repowise applies Google's code-retrieval instruction to queries and document
formatting to stored text, and requests a separate vector for each input.
Embedding 2 does not accept `task_type`. See Google's
[embedding instructions](https://ai.google.dev/gemini-api/docs/embeddings#task-types-with-embeddings-2).

## BYOK (Bring Your Own Key)

Keys resolve in order:

1. **Environment variable**
2. **`.repowise/.env`**: saved by interactive setup, loaded automatically, gitignored
3. **`~/.repowise/provider_config.json`**: keys saved from the web UI
4. **Interactive prompt** during `init`, saved to `.repowise/.env`

## Environment variables

### Provider API keys

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic |
| `OPENAI_API_KEY` | OpenAI or an OpenAI-compatible gateway |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Google Gemini |
| `OPENROUTER_API_KEY` | OpenRouter |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `KIMI_API_KEY` | Kimi |
| `LITELLM_API_KEY` | LiteLLM proxy key |
| `LITELLM_API_BASE` | LiteLLM proxy base URL |

### Provider base URLs

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_BASE_URL`, `GEMINI_BASE_URL`, `DEEPSEEK_BASE_URL`, `KIMI_BASE_URL`, `LITELLM_BASE_URL` | Override that provider's API base URL |
| `OPENAI_BASE_URL` | Override the OpenAI base URL (vLLM/SGLang, 9router, other compatible endpoints) |
| `OLLAMA_BASE_URL` | Ollama server (default `http://localhost:11434`) |
| `OLLAMA_NUM_PARALLEL` | Ollama requests sent at once (default 1) |

### Generation and embeddings

<!-- BEGIN GENERATED settings:env-generation (python scripts/gen_config_docs.py) -->
| Variable | Default | Values | What it does | Config key |
|----------|---------|--------|--------------|------------|
| `REPOWISE_PROVIDER` | unset |  | Which model service writes docs and answers (anthropic, openai, gemini, ollama, claude_cli and others). Unset: the saved global choice (provider_config.json), then REPOWISE_PROVIDER, then the first provider whose API key is present. | `provider` (the file wins) |
| `REPOWISE_MODEL` | unset |  | The model id to use with the provider. Unset: the saved global choice (provider_config.json), then REPOWISE_MODEL, then the provider's default model. | `model` (the file wins) |
| `REPOWISE_REASONING` | `auto` | `auto`, `off`, `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | How much the model thinks before writing. | `reasoning` (this wins) |
| `REPOWISE_EMBEDDER` | unset |  | Which embedding service powers semantic search: openai, gemini, ollama, openrouter, edenai, a plugin's, or mock for none. Unset: the first embedder whose API key is present, then the embedder in ~/.repowise/config.yaml, then mock. | `embedder` (this wins) |
| `REPOWISE_EMBEDDING_MODEL` | unset |  | The embedding model id. Unset uses the embedder's default. | `embedding_model` (this wins) |
| `REPOWISE_DOC_MODEL` | unset |  | A different model just for get_answer write-ups. |  |
| `REPOWISE_EMBEDDING_DIMS` | unset | > 0 | Force the embedding vector width. |  |
| `REPOWISE_EMBEDDING_DECLARED_DIMS` | unset | > 0 | Vector width an OpenAI-compatible server really returns, when it differs from the width requested. |  |
| `REPOWISE_EMBEDDING_TIMEOUT` | unset | > 0 | Seconds one embedding request may take. Unset uses the embedder's own. |  |
| `REPOWISE_EMBED_DOC_PREFIX` | unset |  | Text put before each document before embedding (e.g. "passage: "). |  |
| `REPOWISE_EMBED_QUERY_PREFIX` | unset |  | Text put before each search query before embedding (e.g. "query: "). |  |
| `REPOWISE_NO_COST_TRACKING` | off | `1`, `on`, `true`, `yes` | Do not save model cost records to the database. |  |
<!-- END GENERATED settings:env-generation -->

### Embeddings

| Variable | Description |
|----------|-------------|
| `OPENAI_`, `GEMINI_`, `OPENROUTER_`, `EDENAI_`, `OLLAMA_EMBEDDING_TIMEOUT` | That embedder only; wins over the shared variable |
| `OLLAMA_EMBEDDING_MODEL` | Ollama embedding model; also selects the `ollama` embedder |
| `OLLAMA_EMBEDDING_DIMS` | Ollama output dimensions (optional; inferred from the model) |

- `REPOWISE_EMBEDDING_TIMEOUT` unset: 30 seconds for `ollama`, 10 elsewhere.
  Raise it for a local endpoint: one request embeds a whole batch, and an
  expired batch shows only as `N/N items failed to embed`.
- With no Ollama or shared timeout set, an Ollama request gets 30s plus 2ms
  per input character, so long pages on a CPU-only model are not cut off.
- A cold first vector query (`REPOWISE_VECTOR_SEARCH_TIMEOUT_S`) pays for the
  store open, first embed and first ANN probe (past 13s where a warm query
  takes under a second). Raise the timeout on a slow disk or large wiki; a
  timeout drops the semantic leg with a warning, leaving full-text hits.
- An unparseable timeout warns and keeps the default.
- The `openai` embedder applies the embed prefixes. Set them only for a model
  trained with them (such as `query: ` / `passage: `), then run
  `repowise reindex`: vectors stored before a prefix was set or changed stop
  matching, with no error.

### Providers

<!-- BEGIN GENERATED settings:env-providers (python scripts/gen_config_docs.py) -->
| Variable | Default | Values | What it does |
|----------|---------|--------|--------------|
| `REPOWISE_CLAUDE_CLI_CONCURRENCY` | `4` | >= 1 (clamped) | How many Claude Code CLI calls run at once. |
| `REPOWISE_CODEX_CLI_CONCURRENCY` | `4` | >= 1 (clamped) | How many Codex CLI calls run at once. |
| `REPOWISE_COPILOT_CLI_CONCURRENCY` | `4` | >= 1 (clamped) | How many Copilot CLI calls run at once. |
| `REPOWISE_OPENCODE_CONCURRENCY` | `4` | >= 1 (clamped) | How many OpenCode CLI calls run at once. |
| `REPOWISE_OLLAMA_NUM_CTX` | unset | > 0 | Context window for Ollama. Unset uses the model's own. |
| `REPOWISE_CONFIG_DIR` | `~/.repowise` |  | Folder holding the saved global provider choice (provider_config.json). |
<!-- END GENERATED settings:env-providers -->

`REPOWISE_OLLAMA_NUM_CTX` sizes page material to it. Unset: the Modelfile's
`num_ctx`, else 16384.

### Indexing

<!-- BEGIN GENERATED settings:env-indexing (python scripts/gen_config_docs.py) -->
| Variable | Default | Values | What it does |
|----------|---------|--------|--------------|
| `REPOWISE_FORGE_HOSTS` | unset |  | Comma-separated host=forge pairs, added to forges.hosts. |
| `REPOWISE_PARSE_WORKERS` | unset | > 0 | Parallel parse workers for init and update. Unset sizes to the machine. |
| `REPOWISE_PARSE_TIMEOUT_S` | `60` | >= 0 | Seconds one file may take to parse; 0 turns the limit off. |
| `REPOWISE_GIT_WORKERS` | unset | > 0 | Parallel git processes while indexing history. Unset sizes to the machine. |
| `REPOWISE_GIT_INDEX_TIMEOUT_S` | `120` | > 0 | Seconds one file's git history lookup may take. |
| `REPOWISE_GIT_SCOPE` | `module` |  | Set to repo to index the whole repository's history when indexing one module. |
| `REPOWISE_GIT_WINDOW_ANCHOR` | unset |  | Set to now to measure git windows (recent churn, prior defects, blame age) from today instead of from the indexed commit's date. |
| `REPOWISE_GRAMMAR_DIR` | unset |  | Folder of pre-downloaded language grammars, for offline machines. |
| `REPOWISE_WORKTREE_FALLBACK` | on | on unless `0`, `false`, `no`, `off` | Let a git worktree read its main checkout's index; 0 turns it off. |
| `REPOWISE_INDEX_INFORMATION_FLOOR` | `0` | >= 0 | Least characters of real prose a wiki page needs to be found by semantic search. Shorter pages stay readable, just not in that search. 0 is off. |
<!-- END GENERATED settings:env-indexing -->

`REPOWISE_PARSE_WORKERS` defaults to the CPU count capped at 8, never above
the files to parse. Each worker holds about 50 MB; above 8 is not measurably
faster.

### MCP server and get_answer

<!-- BEGIN GENERATED settings:env-mcp (python scripts/gen_config_docs.py) -->
| Variable | Default | Values | What it does |
|----------|---------|--------|--------------|
| `REPOWISE_ANSWER_TIMEOUT_S` | unset | 0-600 (clamped) | Seconds get_answer may spend writing one answer. Unset uses the provider's own. |
| `REPOWISE_EMBED_TIMEOUT_S` | `8` | 0-120 (clamped) | Seconds a get_answer question may wait to be embedded. |
| `REPOWISE_VECTOR_SEARCH_TIMEOUT_S` | `30` | 0-120 (clamped) | Seconds one semantic search may take. |
| `REPOWISE_WORKSPACE_CONCURRENCY` | `4` | 1-64 (clamped) | How many repos one workspace query asks at once, never more than the server keeps open. |
| `REPOWISE_WORKSPACE_REPO_TIMEOUT_S` | `60` | 0-300 (clamped) | Seconds each repo gets in a workspace query. |
| `REPOWISE_MCP_NO_WATCHDOG` | off | `1`, `true`, `yes` | Keep the MCP server running when the app that started it exits. |
<!-- END GENERATED settings:env-mcp -->

`REPOWISE_ANSWER_TIMEOUT_S` defaults to 60s for remote API providers, 120s
for `ollama` and `litellm`, and 180s for agent CLI providers (`claude_cli`,
`codex_cli`, `copilot_cli`, `opencode`). Raise it for a slow model, lower it to
fail fast. Your MCP client's own tool timeout sits underneath: a higher value
yields a client error instead of Repowise's "synthesis exceeded its budget".

`REPOWISE_MCP_NO_WATCHDOG` turns off the parent-process watchdog in
`repowise mcp --transport stdio`, which exits the server when the MCP client
dies abnormally (crash, force-quit, killed terminal).

### Agent hooks

Each variable sets its [`hooks:`](#the-hooks-block) key for one session.

<!-- BEGIN GENERATED settings:env-hooks (python scripts/gen_config_docs.py) -->
| Variable | Default | Values | What it does | Config key |
|----------|---------|--------|--------------|------------|
| `REPOWISE_HOOK_READ_SKELETON` | off | `1`, `on`, `true`, `yes` | Serve a whole-file Read of a large indexed file as its outline. | `hooks.read_skeleton` (this wins) |
| `REPOWISE_HOOK_READ_REREAD` | off | `1`, `on`, `true`, `yes` | Serve an unchanged repeat Read as a short notice. | `hooks.read_reread` (this wins) |
| `REPOWISE_HOOK_SEARCH_DIGEST` | off | `1`, `on`, `true`, `yes` | Serve a search that floods many files as a digest. | `hooks.search_digest` (this wins) |
| `REPOWISE_HOOK_COVERAGE_REINGEST` | off | `1`, `on`, `true`, `yes` | Re-read coverage reports after an agent's full test run. | `hooks.coverage_reingest` (this wins) |
| `REPOWISE_HOOK_MEASURE` | off | `1`, `on`, `true`, `yes` | On update, check locally whether agents acted on each hook firing. | `hooks.measure` (this wins) |
| `REPOWISE_HOOK_HEALTH_GATE` | off | `1`, `on`, `true`, `yes` | Block an agent's commit or push that fails repowise health check. | `hooks.health_gate` (this wins) |
| `REPOWISE_HOOK_READ_SKELETON_MODE` | `smart` | `smart`, `plus` | What a served outline keeps: smart, or plus to keep all code outside function bodies. | `hooks.read_skeleton_mode` (this wins) |
<!-- END GENERATED settings:env-hooks -->

### Server and database

<!-- BEGIN GENERATED settings:env-server (python scripts/gen_config_docs.py) -->
| Variable | Default | Values | What it does |
|----------|---------|--------|--------------|
| `REPOWISE_API_KEY` | unset |  | Shared secret that protects the API and the MCP HTTP server; needed for requests from other machines. |
| `REPOWISE_HOST` | `127.0.0.1` |  | Address the servers listen on. 0.0.0.0 without an API key prints a warning. |
| `REPOWISE_PORT` | `7337` | 1-65535 | Port for the API server. |
| `REPOWISE_API_URL` | `http://localhost:7337` |  | Where the web UI finds the API. repowise serve sets it. |
| `REPOWISE_NO_OPEN` | off | `1`, `on`, `true`, `yes` | Do not open a browser when repowise serve starts. |
| `REPOWISE_DB_URL` | unset |  | Database to use instead of the repo's SQLite file, for example Postgres. |
| `REPOWISE_DATABASE_URL` | unset |  | Older name for REPOWISE_DB_URL, read when that is unset. |
| `REPOWISE_CORS_ORIGINS` | unset |  | Comma-separated web origins allowed to call the API from a browser. Unset allows any origin without credentials. |
| `REPOWISE_PUBLIC_URL` | unset |  | Public https address of the MCP server behind a proxy. |
| `REPOWISE_TOKENS_FILE` | `~/.repowise/tokens.yaml` |  | File of scoped access tokens for the server. |
| `REPOWISE_GITHUB_WEBHOOK_SECRET` | unset |  | Secret that verifies GitHub webhooks. |
| `REPOWISE_GITLAB_WEBHOOK_TOKEN` | unset |  | Token that verifies GitLab webhooks. |
| `REPOWISE_GITEA_WEBHOOK_SECRET` | unset |  | Secret that verifies Gitea webhooks. |
| `REPOWISE_PLUGINS` | unset |  | Comma-separated extra plugin modules to load (module or module:attr). |
<!-- END GENERATED settings:env-server -->

- Without `REPOWISE_API_KEY` the server answers local callers only and
  refuses other hosts with 403.
- `REPOWISE_HOST` is the default host for `repowise mcp` over HTTP/SSE;
  `repowise serve` sets it from `--host`.
- The MCP SSE port is the `repowise mcp --port` flag (default `7338`).

### Telemetry and account

Anonymous usage telemetry is **on by default** (opt-out). `DO_NOT_TRACK` with
any truthy value turns it off (cross-tool convention).

<!-- BEGIN GENERATED settings:env-account (python scripts/gen_config_docs.py) -->
| Variable | Default | Values | What it does |
|----------|---------|--------|--------------|
| `REPOWISE_OFFLINE` | off | `1`, `on`, `true`, `yes` | Block repowise's own network calls: telemetry, update checks, plan status, agent sign-in checks and decision sync. |
| `REPOWISE_TELEMETRY_DISABLED` | off | `1`, `on`, `true`, `yes` | Turn off anonymous usage telemetry. |
| `REPOWISE_TELEMETRY_DEBUG` | off | `1`, `on`, `true`, `yes` | Print the telemetry payload instead of sending it. |
| `REPOWISE_NO_HINTS` | off | `1`, `on`, `true`, `yes` | Hide repowise.dev tips in the CLI and web UI. |
| `REPOWISE_NO_SAVE_KEY` | off | on when set, unless `0`, `false`, `no` | Do not copy an API key into the repo's .repowise/.env. For CI and shared machines. |
| `REPOWISE_CHANGELOG` | unset |  | A changelog file for the what's-new check to read instead of the bundled one. |
<!-- END GENERATED settings:env-account -->

`REPOWISE_OFFLINE` also turns off the PyPI release check and the `init` probe
of signed-in agent CLIs (`DO_NOT_TRACK` and `REPOWISE_TELEMETRY_DISABLED` turn
those off too).

### Editor setup

<!-- BEGIN GENERATED settings:env-editor (python scripts/gen_config_docs.py) -->
| Variable | Default | Values | What it does |
|----------|---------|--------|--------------|
| `REPOWISE_SKIP_EDITOR_SETUP` | off | on when set, unless `0`, `false`, `no` | Do not register repowise in editors' and agents' global config. |
<!-- END GENERATED settings:env-editor -->

`REPOWISE_SKIP_EDITOR_SETUP` makes `init` skip machine-wide editor config
(Claude Code / Claude Desktop MCP entry, Claude Code hooks, distill
rewrite-hook offer), like `init --no-editor-setup`
([CLI_REFERENCE.md](CLI_REFERENCE.md#repowise-init-path)). Use it for CI,
sandboxes and benchmark runs. Project files (`.repowise/mcp.json`,
`CLAUDE.md`, Codex config) are still written.

### CI gates

<!-- BEGIN GENERATED settings:env-health (python scripts/gen_config_docs.py) -->
| Variable | Default | What it does |
|----------|---------|--------------|
| `REPOWISE_GITLAB_TOKEN` | unset | GitLab access token (api scope) that posts the CI summary as a merge-request note. |
<!-- END GENERATED settings:env-health -->

`REPOWISE_GITLAB_TOKEN` is read by `repowise ci-summary --gitlab-note`; set it
as a masked CI/CD variable ([CI.md](../start/CI.md#gitlab)).

### BLAS thread pool

repowise caps the BLAS thread pool to 1 before numpy loads. An uncapped pool costs about 32 MB of memory per thread (about 750 MB on a 32-core machine) and does not speed up the sparse graph work repowise runs. Spawned worker processes inherit the cap through the environment.

A value you set yourself always wins:

```bash
OPENBLAS_NUM_THREADS=4 repowise init   # 4 BLAS threads instead of 1
```

| Variable | Applies to | Default |
|----------|-----------|---------|
| `OPENBLAS_NUM_THREADS` | OpenBLAS | `1` unless set |
| `MKL_NUM_THREADS` | Intel MKL | `1` unless set |
| `NUMEXPR_NUM_THREADS` | NumExpr | `1` unless set |
| `VECLIB_MAXIMUM_THREADS` | Apple Accelerate | `1` unless set |

`OMP_NUM_THREADS` is not set: igraph's community detection uses OpenMP threads and is slower without them.

### Cache integrity key

repowise integrity-checks its on-disk caches with a machine-local key.

| Variable | Description |
|----------|-------------|
| `REPOWISE_CACHE_HMAC_KEY` | Hex-encoded key (at least 32 hex characters, 16 bytes) used instead of the key file. For CI and tests where the home directory is ephemeral or shared |
| `REPOWISE_CACHE_KEY_PATH` | Path to the key file. Default `$XDG_CONFIG_HOME/repowise/cache_hmac_key`, or `~/.config/repowise/cache_hmac_key` when `XDG_CONFIG_HOME` is unset |

## Exclude patterns

Repowise honours `.gitignore` (git's `gitwildmatch` format), including nested
`.gitignore` files, so a monorepo or workspace package's own build exclusions
(`dist/`, `coverage/`, generated bundles) apply without repeating them at the
root.

```bash
repowise init -x vendor/ -x "*.generated.ts" -x proto/ -x "**/*.pb.go"
```

- `--exclude` / `-x` patterns are saved to `exclude_patterns` and reused by
  `update`.
- A `.repowiseIgnore` file (gitignore syntax) at the root or in any
  subdirectory excludes without touching `.gitignore`.
- Always excluded: `.git/`, `.repowise/`, `node_modules/`, `__pycache__/`,
  `*.pyc`, `.venv/`, binary files, lockfiles, minified assets.
- `--skip-tests` (test files) and `--skip-infra` (Dockerfiles, Makefiles,
  shell scripts) apply only to their run; pass them again each time.

## Submodules

Submodule directories are excluded by default; Repowise finds them in
`.gitmodules`.

```bash
repowise init --include-submodules
```

The flag is not written to `config.yaml`; a workspace records it in
`state.json`.

## PostgreSQL

For team deployments or larger repos:

```bash
export REPOWISE_DB_URL="postgresql+asyncpg://user:pass@localhost:5432/repowise"
repowise init
```

`REPOWISE_DATABASE_URL` is a still-honored alias. Alembic manages the schema.

## Workspace config (`.repowise-workspace.yaml`)

A multi-repo [workspace](../scale/WORKSPACES.md) is configured at its root.
Beside the repo list it takes `repos[].tags`, `conformance:`, and a
`contracts:` block that tunes cross-repo contract detection.

### `repos[].tags`

Free-form tags that group services in conformance rules:

```yaml
repos:
  - path: web
    alias: frontend
    tags: [ui, edge]
  - path: services/db
    alias: db
    tags: [data]
```

### The `conformance:` block

Allow/deny dependency rules checked by `repowise workspace check` and the
Conformance view ([Architecture Conformance](../scale/WORKSPACES.md#architecture-conformance)).

```yaml
conformance:
  rules:
    - source: frontend
      target: db
      allow: false
      description: "..."
    - source: "tag:ui"
      target: "tag:data"
    - source: "*"
      target: legacy-payments
```

| Field | Default | Meaning |
|-------|---------|---------|
| `source` | required | Dependent side: `*` (any service), `tag:<name>` (repos with that tag), or a glob over node id / repo alias / display name |
| `target` | required | Depended-upon side, same forms |
| `allow` | `false` | `false` denies (a match is a violation); `true` whitelists an otherwise-denied edge |
| `description` | `""` | Rationale shown in reports |

Rules apply only to structural edges (HTTP, gRPC, socket, event, package, db),
never to behavioral co-change.

## Deprecated / legacy aliases

| Old name | Current name | Notes |
|----------|--------------|-------|
| `max_pages_pct` | `coverage_pct` | Internal alias, still read |
| `REPOWISE_DATABASE_URL` | `REPOWISE_DB_URL` | Still honored |
