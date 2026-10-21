# Configuration

The `.repowise/` directory, `config.yaml` and its blocks, provider setup, API
keys, and environment variables.

---

## Contents

**Files and `config.yaml`**
[The `.repowise/` directory](#the-repowise-directory) ·
[`config.yaml`](#configyaml) ·
[Grounded generation context](#grounded-generation-context) ·
[The `distill:` block](#the-distill-block) ·
[The `hooks:` block](#the-hooks-block) ·
[The `mcp:` block](#the-mcp-block) ·
[The `editor_files:` block](#the-editor_files-block) ·
[The `decisions:` block](#the-decisions-block) ·
[`decisions.yaml`](#repowisedecisionsyaml) ·
[`components.yaml`](#repowisecomponentsyaml) ·
[`policies.yaml`](#repowisepoliciesyaml) ·
[`teams.yaml`](#repowiseteamsyaml) ·
[The `coverage:` block](#the-coverage-block) ·
[The `security:` block](#the-security-block) ·
[The `attribution:` block](#the-attribution-block) ·
[The `events:` block](#the-events-block) ·
[The `refactoring:` block](#the-refactoring-block) ·
[The `assertions:` block](#the-assertions-block)

**Code health rules**
[The `health-rules.json` file](#the-health-rulesjson-file)

**LLM providers**
[Overview](#llm-providers) ·
[Anthropic](#anthropic-claude) ·
[OpenAI](#openai-gpt) ·
[OpenRouter](#openrouter) ·
[Gemini](#gemini-google) ·
[DeepSeek](#deepseek) ·
[Kimi](#kimi) ·
[Ollama](#ollama-local-no-api-key) ·
[LiteLLM](#litellm-100-providers) ·
[Eden AI](#eden-ai-hosted-with-an-eu-endpoint) ·
[Provider auto-detection](#provider-auto-detection)

**Embeddings and keys**
[Embeddings (for semantic search)](#embeddings-for-semantic-search) ·
[BYOK (Bring Your Own Key)](#byok-bring-your-own-key)

**Environment variables**
[Overview](#environment-variables) ·
[Provider API keys](#provider-api-keys) ·
[Provider base URLs](#provider-base-urls) ·
[Provider and model overrides](#provider-and-model-overrides) ·
[Embeddings](#embeddings) ·
[Server and database](#server-and-database) ·
[Telemetry](#telemetry) ·
[Misc](#misc)

**Repo scope**
[Exclude patterns](#exclude-patterns) ·
[Submodules](#submodules) ·
[PostgreSQL](#postgresql)

**Workspace**
[Workspace config (`.repowise-workspace.yaml`)](#workspace-config-repowise-workspaceyaml) ·
[The `conformance:` block](#the-conformance-block)

**Reference**
[Deprecated / legacy aliases](#deprecated--legacy-aliases)

---

## The `.repowise/` directory

Everything Repowise knows about your repository lives here. `init` creates it
at the repo root.

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

- Repowise adds `.repowise/` to your `.gitignore` automatically.
- Do not commit the directory: it is a local cache, not a source of truth.
- The exception is [`.repowise/decisions.yaml`](#repowisedecisionsyaml), which
  is meant to be committed.

---

## `config.yaml`

The main configuration file. `init` creates it, and flags such as
`--commit-limit`, `--follow-renames` and `--wiki-style` update it. You can edit
it directly; changes take effect on the next `init`, `update` or `serve` run.

> **Limited schema validation.** `config.yaml` is loaded as a plain YAML dict.
> Unknown or misspelled keys are silently ignored: they do not error and do not
> take effect. Exceptions: in the `security:` block they stop
> `repowise security check`; `max_tokens` must be a positive integer when
> documentation is generated; the `distill:` block is validated only when you
> run `repowise doctor`. If a setting does not seem to take effect, check
> spelling and indentation first.

```yaml
provider: anthropic                  # LLM provider (auto-detected if omitted)
model: claude-sonnet-4-6             # Model identifier (provider default if omitted)
embedder: mock                       # Embedding provider (mock if no key detected)
embedding_model: text-embedding-3-small  # Embedding model (provider default if omitted)
reasoning: auto                      # auto | off | none | minimal | low | medium | high | xhigh | max
max_tokens: 16384                    # Max output tokens for each generated documentation page
commit_limit: 5000                   # Max commits per file for git analysis (clamped 1-10000)
follow_renames: false                # Track file renames in git history
wiki_style: comprehensive            # comprehensive | caveman | reference | tutorial | <custom style name>
language: en                         # Output language for generated pages (en, zh, ru, hi, ...)
enable_onboarding: true               # Show first-run onboarding prompts
max_file_pages: 2000                  # Cap file pages (omit = size policy, 0 = one page per file)
generation_context:                   # Optional source evidence for synthesis pages
  token_budget: 8000
  files:
    repo_overview:
      - docs/ARCHITECTURE.md
    onboarding/how_it_works:
      - docs/runtime-flow.md
exclude_patterns:                    # Gitignore-style patterns
  - vendor/
  - "*.generated.*"
  - proto/

distill:                             # see "The distill: block" below
  enabled: true

mcp:                                 # see "The mcp: block" below
  tools: ["+get_execution_flows", "-get_dead_code"]

refactoring:                         # see "The refactoring: block" below
  enabled: true

assertions:                          # see "The assertions: block" below
  extra_names: []
```

### Top-level keys

| Key | Type | Default | What it does |
|-----|------|---------|--------------|
| `provider` | string | auto-detected | `anthropic`, `openai`, `gemini`, `openrouter`, `deepseek`, `kimi`, `ollama`, `litellm`, `edenai`, `claude_cli`, `codex_cli`, `copilot_cli`, `opencode`. |
| `model` | string | provider default | Model identifier passed to the provider. |
| `embedder` | string | `mock` | `openai`, `gemini`, `ollama`, `openrouter`, `edenai`, `mock`. See [Embeddings](#embeddings-for-semantic-search). |
| `embedding_model` | string | provider default | Embedding model identifier. |
| `reasoning` | string | `auto` | `auto`, `off`, `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. See [notes](#reasoning). |
| `max_tokens` | int | `16384` | Most output tokens requested for each model-written documentation page. |
| `commit_limit` | int | `5000` | Newest commits walked for git analysis and stored as commit rows, clamped to 1-10000. |
| `follow_renames` | bool | `false` | Track file renames through git history. |
| `exclude_patterns` | list | `[]` | Extra gitignore-style patterns, on top of `.gitignore`. See [Exclude patterns](#exclude-patterns). |
| `wiki_style` | string | `comprehensive` | `comprehensive`, `caveman`, `reference`, `tutorial`, or the name of a custom style under `.repowise/styles/<name>/`. |
| `language` | string | `en` | Output language for generated wiki pages: `en`, `ar`, `de`, `es`, `fr`, `hi`, `it`, `ja`, `ko`, `nl`, `pl`, `pt`, `ru`, `tr`, `zh`. |
| `enable_onboarding` | bool | `true` | Show first-run onboarding prompts (CLI and web). |
| `max_file_pages` | int | unset | Most file pages a run emits, highest importance first. See [notes](#max_file_pages). |
| `generation_context` | mapping | see [below](#grounded-generation-context) | Repository-source evidence appended to model-written overview and onboarding prompts. |
| `distill` | mapping | see [below](#the-distill-block) | Output distillation config. |
| `hooks` | mapping | see [below](#the-hooks-block) | Opt-in agent hook behaviour. |
| `mcp` | mapping | see [below](#the-mcp-block) | MCP tool surface config. |
| `editor_files` | mapping | see [below](#the-editor_files-block) | The generated block in `CLAUDE.md` and `AGENTS.md`. |
| `decisions` | mapping | see [below](#the-decisions-block) | Decision capture. |
| `coverage` | mapping | see [below](#the-coverage-block) | Coverage reports and gates. |
| `security` | mapping | see [below](#the-security-block) | Custom secret shapes. |
| `attribution` | mapping | see [below](#the-attribution-block) | AI attribution. |
| `events` | mapping | see [below](#the-events-block) | Component snapshots and typed events. |
| `refactoring` | mapping | see [below](#the-refactoring-block) | Refactoring-intelligence config. |
| `assertions` | mapping | see [below](#the-assertions-block) | Your own assertion helpers. |
| `forges.hosts` | mapping | `{}` | Maps a self-managed git host to its forge (`github`, `gitlab`, `azure`, `bitbucket`, `generic`). See [GIT_HOSTS.md](../start/GIT_HOSTS.md#self-managed-hosts). |

Code-health rules are not in `config.yaml`; they live in
[`.repowise/health-rules.json`](#the-health-rulesjson-file).

**Not stored in `config.yaml`:** `--skip-tests`, `--skip-infra` and
`--include-submodules` apply only to the run they are passed on.
`--include-submodules` is recorded in `state.json` (workspace state) instead.

### Notes on top-level keys

#### `commit_limit`

- Sets how far back history-based signals see: churn, co-change, the commit
  list and change records.
- The first `repowise update` after upgrading re-reads the history once, with
  no model calls.
- A `commit_limit` written in `config.yaml` is kept as set. When it is below
  the default, `repowise update` says so once.

#### `reasoning`

Controls documentation-generation calls for reasoning-capable chat models.

| Value | Effect |
|-------|--------|
| `auto` | Keeps provider defaults. |
| `off`, `none` | Disable Qwen3-style thinking on OpenAI-compatible vLLM/SGLang endpoints (sends `extra_body.chat_template_kwargs.enable_thinking=false`). Map to OpenRouter's `reasoning.effort=none` for effort-capable OpenRouter model families. |
| `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | Request the matching effort level from providers and model families that support it (for example OpenAI reasoning models and OpenRouter's `reasoning.effort`). |

Providers or models that cannot translate an explicit mode fail before making
an API call.

#### `max_tokens`

- Bounds each model-written documentation response.
- A persistent repository setting, not a per-command flag: `init`, `update`,
  `generate`, `restyle`, workspace generation and server-triggered generation
  all use the same value.
- Providers may enforce a lower model limit.
- If generation reaches a token limit before the page is complete, Repowise
  rejects the partial page instead of saving it.

#### `max_file_pages`

| Value | Effect |
|-------|--------|
| unset | The size policy decides: untouched below 4,500 documentable files, held to 4,500 above (about 1 repo in 100). |
| `0` | One page per eligible file, however many that is. |
| positive | A hard cap. |

- `repowise init` offers a tighter cap in advanced mode above 2,000
  documentable files; `--max-file-pages N` sets it non-interactively.
- `update --full` and `generate` honour whatever is recorded.
- Capping file pages does not reduce model spend: file pages are rendered from
  structure.

#### `wiki_style`

- Controls the voice and density of generated wiki pages.
- Set it with `init --wiki-style`, or switch later with
  `repowise restyle <style>`, which also regenerates the wiki.
- Define your own style under `.repowise/styles/<name>/style.yaml` and set
  `wiki_style: <name>`. Full guide: [WIKI.md](../layers/WIKI.md#styles).
- An unknown name falls back to `comprehensive` during generation; the CLI and
  API validate it up front.
- Hand-editing `wiki_style` and running `update` does not regenerate existing
  pages; use `restyle`.

#### `language`

- Controls the natural language of generated wiki content. Code, file paths
  and symbol names stay untranslated.
- It reaches the model-written pages, and the headings and fixed sentences of
  the template-rendered ones (file, symbol, infrastructure, cycle and
  API-contract pages) for the languages that have a label catalog.
- A language without a catalog keeps English headings on those pages while the
  model-written prose is still translated.
- Set it with `init --language <code>` or pick it in advanced interactive mode.
  It persists here, so `update` regenerates changed pages in the same language.
- Unknown codes fall back to English with a warning.
- Changing it later does not retranslate existing pages: re-run
  `init --force --language <code>` to rebuild the wiki in the new language.

## Grounded generation context

`generation_context` adds repository files as evidence to model-written
synthesis pages, when a page needs facts its structural context does not
include. Files are explicit evidence selection, not automatic discovery.

| Key | Type | Default | What it does |
|-----|------|---------|--------------|
| `token_budget` | int | `8000` | Shared per-page cap on rendered evidence. |
| `files` | mapping | none | Page key to an ordered list of repository-relative paths. |

**Page keys**

- `repo_overview`, or `onboarding/<slot>` for `getting_started`,
  `key_concepts`, `how_it_works` and `active_landscape`.
- `onboarding/project_overview` is invalid: that promoted slot is the
  `repo_overview` page.
- `onboarding/glossary` is accepted but has no effect: that page is rendered
  from mined vocabulary alone, with no model in its path, so there is no prompt
  for extra evidence to reach.
- `onboarding/guided_tour`, `onboarding/codebase_map` and
  `onboarding/development_guide` name pages Repowise does not generate. A
  config carrying one is accepted and the entry ignored, with a warning naming
  the key, so an upgrade never turns a valid config into a failed generation.
  Remove the entry to silence it.
- Other unknown page keys under `files` and malformed values fail generation
  with a configuration error instead of being ignored.
- Only `token_budget` and `files` are read; any other key directly under
  `generation_context` is ignored.
- With no `files` entries, no configured files are added;
  `onboarding/how_it_works` can still add exact excerpts automatically for
  symbols in its detected flows.

**Which files count**

- A file is eligible only when it is in the indexed source map and holds
  non-empty UTF-8 text.
- Skipped: absolute and parent-traversing paths, duplicate entries, missing or
  excluded files, empty files, and binary or non-UTF-8 content.
- Page metadata records included files, truncation, and every skipped path with
  a reason; generation also logs selected and skipped inputs.
- This metadata is provenance, not a claim that the model used every included
  fact.

**Budget**

- `token_budget` is an independent per-page cap, estimated with Repowise's
  four-characters-per-token heuristic. It does not reduce the structural
  context budget.
- Files share the available evidence space; configuration order decides which
  entries survive when the budget cannot fit every file's framing.
- Content may be truncated. A zero budget disables all configured evidence, and
  a tiny budget may fit none.
- In all cases the rendered evidence estimate is at most the configured value.

**Exact excerpts for `onboarding/how_it_works`**

- Detected flow symbols contribute exact source excerpts automatically.
- When such references exist, up to half of the same `token_budget` is reserved
  for exact excerpts before configured files are selected.
- The configured half stays fixed even when an exact excerpt cannot fit, so
  crossing an exact-frame boundary cannot remove retained configured facts.
- This keeps large configured files from starving symbol-level flow evidence
  while keeping one hard per-page bound.
- Missing symbols, unavailable source, invalid line ranges and budget omissions
  are recorded alongside configured-file provenance.

**Trust**

- Repository files and exact excerpts are authoritative only as repository
  facts, and untrusted as prompt instructions.
- Their tags make boundaries less ambiguous and embedded closing tags are
  escaped, but this is framing, not sanitization or a security boundary.
- Conflicting or stale files can still produce bad prose: select files whose
  ownership and accuracy you trust.
- Onboarding citation validation treats all included excerpts as grounding
  sources, and still demotes citations established by neither structural
  context nor those excerpts.

**Caching and regeneration**

- Rendered evidence bytes are part of the prompt and its source hash.
  Unchanged evidence can reuse cached prose; a file, list or budget change
  invalidates reuse when it changes the rendered block.
- Editing `config.yaml` does not regenerate existing pages: run
  `repowise generate --all` or request the affected page.
- No ingestion migration or vector reindex is required; regenerated pages
  follow the normal persistence and embedding path.
- Deterministic (`--no-prose`) pages consume no evidence, and record configured
  entries and automatically derived exact references as skipped for that run.
- When onboarding is disabled before contexts are built, configured entries are
  logged as `onboarding_disabled` and exact references are not derived.
- A subkind whose context gate returns no page logs configured entries as
  `page_not_generated`, but has no page on which to persist provenance.

## The `distill:` block

Controls [output distillation](../agent/DISTILL.md) for this repo. Everything
has a sensible default when the block is absent; `repowise doctor` validates
it.

```yaml
distill:
  enabled: true                  # master switch for this repo
  commands:
    enabled: true                # the command path (CLI + hook rewrites)
    permission: allow             # ask | allow | off (rewrite-hook posture)
    families:                     # per-filter overrides
      test_output: allow          #   auto-allow rewrites for test runs
      git_diff: deny              #   never rewrite git diff here
    disabled_filters: []          # filters to skip entirely, e.g. [logs]
  omission_store:
    ttl_days: 7                   # prune stored omissions after this many days
    max_mb: 50                    # size cap; oldest entries pruned first
```

| Key | Type | Default | What it does |
|-----|------|---------|--------------|
| `enabled` | bool | `true` | Master switch for this repo. |
| `commands.enabled` | bool | `true` | The command path: CLI and hook rewrites. |
| `commands.permission` | string | `allow` | Rewrite-hook posture: `ask`, `allow` or `off`. |
| `commands.families` | mapping | `{}` | Per-filter overrides: filter name to `ask`, `allow`, `off` or `deny`. |
| `commands.disabled_filters` | list | `[]` | Filters to skip entirely, e.g. `[logs]`. |
| `omission_store.ttl_days` | number | `7` | Prune stored omissions after this many days. |
| `omission_store.max_mb` | number | `50` | Size cap; oldest entries are pruned first. |

- `permission: allow` auto-approves rewrites, uniformly across the main agent
  and every subagent. This is not a permission escalation: a rewrite is always
  `repowise distill <one recognized command>` from a closed family set, never
  an arbitrary command smuggled behind the wrapper.
- `ask` shows each rewritten command for approval instead; `off` disables
  rewrites in this repo.
- Filter names for `families`: `test_output`, `build_output`, `lint_output`,
  `install_output`, `infra_plan`, `git_status`, `git_log`, `git_diff`,
  `git_diff_stat`, `search_results`, `file_listing`, `logs`. `repowise doctor` validates them
  against the live filter registry, so it always knows the current set.
- Declining the `repowise init` opt-in prompt writes `commands.enabled: false`,
  so a rewrite hook installed globally from another repo stays inert in this
  one.

## The `hooks:` block

Opt-in behaviour for the agent hooks ([HOOKS.md](../agent/HOOKS.md)). An absent
block means every key is off.

```yaml
hooks:
  read_skeleton: false           # serve large indexed files as skeletons
  read_skeleton_mode: smart      # smart, or plus to keep all non-function code
  read_reread: false             # serve unchanged re-reads as a pointer
  search_digest: false           # serve multi-file grep floods as a digest
  coverage_reingest: false       # re-ingest coverage after an agent's full test run
```

| Key | Type | Default | Session override | What it does |
|-----|------|---------|------------------|--------------|
| `read_skeleton` | bool | `false` | `REPOWISE_HOOK_READ_SKELETON=1` | Serve an unbounded Read of a large indexed file as its skeleton. |
| `read_skeleton_mode` | string | `smart` | `REPOWISE_HOOK_READ_SKELETON_MODE=plus` | `smart`, or `plus` to keep all non-function code. |
| `read_reread` | bool | `false` | `REPOWISE_HOOK_READ_REREAD=1` | Serve an unchanged repeat Read as a short notice. |
| `search_digest` | bool | `false` | `REPOWISE_HOOK_SEARCH_DIGEST=1` | Serve multi-file grep floods as a digest. |
| `coverage_reingest` | bool | `false` | `REPOWISE_HOOK_COVERAGE_REINGEST=1` | Re-ingest coverage after an agent's full test run. |

Each session override applies to one session.

### `read_skeleton`

- The PostToolUse Read hook returns the *skeleton* of a file instead of the
  file, for an unbounded Read of a large indexed file, once per file per
  session.
- Signatures stay; bodies become `... N lines (a-b)` markers carrying 1-indexed
  ranges, so a ranged Read pulls any elided span back, and reading the file a
  second time returns it whole.
- `read_skeleton_mode: plus` keeps every line outside a function or method body
  (imports, constants, class fields, decorators, comments) and elides only the
  bodies.
- Savings land in `repowise saved` under the `read_skeleton` filter.
- Requires Claude Code 2.1.218+. Older clients silently fall back to the
  one-line pointer at `get_context(include=["skeleton"])`.
- **Written by the rewrite-hook question in `repowise init`.** Saying yes there
  turns this on too; `--no-editor-setup` and `--no-distill-hook` turn it off
  with everything else. There is no separate prompt: that question already asks
  the broader thing (letting Repowise's hooks intervene in your agent's tool
  calls), and rewriting a shell command is the larger intervention of the two.
- To change your mind for one repo without re-running init, use
  `repowise hook read-skeleton install | uninstall | status`.
- **What it costs while off.** Every Read that *would* have been served as a
  skeleton is measured anyway, and `repowise saved` reports the total under
  "Not saved". That figure is what the replacement would have taken off the
  bill and only that: nothing was replaced, so nothing was read back, so it
  says nothing about how often the agent would have wanted the whole file.

### `read_reread`

The PostToolUse Read hook answers a *repeat* Read with a short notice instead
of the content when all of these hold: the same range was already served this
session, no `Edit` or `Write` came between, and the bytes hash the same.

- The notice names the earlier read and the tool call it happened on.
- **Nothing is guessed.** The decision is a hash comparison over what the agent
  was actually served. A file whose content differs is served in full, with a
  line saying it changed on disk and not through an edit in this session. That
  line is worth more than the bytes: nothing else in the session can discover
  it.
- **Never twice in a row for the same file.** The premise is that the earlier
  copy is still in the agent's context, and a context compaction removes it.
  A hook cannot detect that, so reading again always returns the content, and
  the notice says so.
- Savings land in `repowise saved` under the `read_reread` filter.
- Requires Claude Code 2.1.218+; older clients are left untouched.

### `coverage_reingest`

Re-ingests coverage after an agent's full test run, at a process start per
shell command. Details: [test intelligence](../layers/TEST_INTELLIGENCE.md).

## The `mcp:` block

Controls which tools the MCP server advertises. The default surface is curated:
10 tools in single-repo mode, plus `list_repos` in workspace mode; seven more
are opt-in. The `repowise mcp --tools` / `--all` flags override the block for
a single launch.

```yaml
mcp:
  tools: ["+get_execution_flows", "-get_dead_code"]   # adjust the default set
  # tools: ["get_answer", "get_context"]              # or an explicit allowlist
  # tools: all                                        # or everything available
  # tools: lean                                       # or the agent-lean profile
```

| `tools` value | Effect |
|---------------|--------|
| `+name` / `-name` entries | Add to or remove from the default set. |
| unprefixed list | An explicit allowlist. |
| `all` | Everything available. |
| `lean` | The agent-lean profile: `get_answer`, `get_context`, `get_symbol`, `search_codebase`, `get_risk`, `get_why` (plus `list_repos` in workspace mode), small enough that Claude Code can keep every schema always loaded. |

- Opt-in tools: `get_dependency_path`, `get_execution_flows`,
  `generate_refactoring_code` and `set_finding_status`, plus the
  workspace-only `get_blast_radius`, `get_conformance` and `get_architecture`.
- None is on by default. The workspace-only three are ignored if named in
  single-repo mode.
- See [MCP_TOOLS.md](../agent/MCP_TOOLS.md#configuring-the-tool-surface).

## The `editor_files:` block

Controls the generated block in `.claude/CLAUDE.md` and `AGENTS.md`. The block
is read into the agent's context on every turn, so the default is lean.

```yaml
editor_files:
  claude_md: true
  sections: ["+commands", "+fix_first"]
```

| Key | Type | Default | What it does |
|-----|------|---------|--------------|
| `claude_md` | bool | `true` | Write the block into `.claude/CLAUDE.md`. |
| `agents_md`, `cursor_rules`, `kiro_steering`, `vscode_mcp` | bool | `true` | `false` stops Repowise writing that agent target's project file. |
| `sections` | list | default set | Adjusts the sections with the `mcp.tools` grammar: `+name` / `-name` change the default set, unprefixed names replace it. |

| Section | In the default set | Holds |
|---------|--------------------|-------|
| `architecture` | yes | |
| `key_modules` | yes | |
| `entry_points` | yes | |
| `hotspots` | yes | |
| `health` | yes | Score and worst file. |
| `decisions` | yes | Top three titles. |
| `how_to_work` | yes | Three bullets. |
| `tools` | yes | |
| `code_health` | opt-in | All health signals; replaces `health`. |
| `fix_first` | opt-in | |
| `commands` | opt-in | Build, test and lint commands read from manifests. |
| `how_to_work_full` | opt-in | |

## The `decisions:` block

Controls decision capture: whether it runs at all, which sources it uses, and
whether any of them may call a model. One resolved policy backs the CLI, the
API and the index pipeline, so all three agree about what will run.

```yaml
decisions:
  enabled: true             # master switch for automatic capture
  llm: true                 # master switch for decision-extraction model calls
  preset: balanced          # default | off | local_only | balanced | full
  sources:
    inline_marker: true     # WHY:/DECISION: markers
    git_archaeology: true   # commit messages
    adr: true               # ADR files
    pr: true                # PR / squash-merge bodies
    comment: false          # comment archaeology on top central files
    session:                # off by default; long form runs the deterministic
      enabled: true         #   parse and skips the model stage
      llm: false
    session_discovery: true # one broad model pass over new transcript prose
    conventions: false      # import patterns the graph proves, no model
  discovery:                # budget for that one pass, per update
    max_sessions: 12        # 1-24
    max_input_tokens: 30000 # 2000-60000
  harnesses:                # whose transcripts the session lane reads
    - claude_code           # the default; add codex to read that store too
  agent_acceptance: false   # may an agent grant a decision authority?
  capture_prompt: false     # ask the agent to record what it just committed
```

Every key is optional.

| Key | Type | Default | What it does |
|-----|------|---------|--------------|
| `enabled` | bool | `true` | Master switch for automatic capture. |
| `llm` | bool | `true` | Master switch for decision-extraction model calls. |
| `preset` | string | none (`default`) | `default`, `off`, `local_only`, `balanced`, `full`. See [presets](#presets). |
| `sources.inline_marker` | source | on | `WHY:` / `DECISION:` markers. |
| `sources.git_archaeology` | source | on | Commit messages. Model-only. |
| `sources.adr` | source | on | ADR files. |
| `sources.pr` | source | on | PR and squash-merge bodies. Model-only. |
| `sources.comment` | source | on | Comment archaeology on top central files. Model-only. |
| `sources.session` | source | off | Coding-agent session transcripts. See [session mining](#session-mining). |
| `sources.session_discovery` | source | off | One broad model pass over new transcript prose. Model-only. See [session discovery](#session-discovery). |
| `sources.conventions` | source | off | Import patterns the graph proves, no model. |
| `discovery.max_sessions` | int | `12` | Sessions per discovery pass, 1-24. |
| `discovery.max_input_tokens` | int | `30000` | Input tokens per discovery pass, 2000-60000. |
| `harnesses` | list | `[claude_code]` | Whose transcripts the session lane reads; add `codex` to read that store too. |
| `agent_acceptance` | bool | `false` | Whether an agent may grant a decision authority. |
| `capture_prompt` | bool | `false` | Ask the agent to record what it just committed. |

### Sources

- A source is a bare boolean or an `{enabled, llm}` mapping.
- The long form matters only for a source with both a deterministic and a model
  stage (`inline_marker`, `adr`, `session`): `llm: false` keeps the
  deterministic parse and skips the model call.
- A model-only source (`git_archaeology`, `pr`, `comment`, `session_discovery`)
  is skipped entirely when its model stage is off: running it would produce a
  zero indistinguishable from an empty repository.
- Unknown keys are reported as warnings rather than discarded, so a typo'd
  source name shows up instead of silently reading as a working switch.
- The names `code_comment`, `changelog` and `readme_mining` are unknown keys:
  an old config still loads and says which key it ignored.

### Defaults and upgrades

- **A config with no `decisions:` block** has every long-standing source on and
  model stages on, except `session`. That resolved policy is named `default`.
- `session_discovery` and `conventions` stay off until you ask for them, so
  upgrading never starts a model call nobody enabled.
- `session` ships **off**: what it captured read as working agreements from a
  transcript rather than decisions the codebase had taken.
  `repowise decision source set session --on` turns it on, and `local_only`
  carries it.
- A config that names a preset *and* lists its sources keeps that list: it is
  what the preset covered when written, so a source added to that preset later
  does not join it retroactively. Re-apply the preset to pick it up.
- The legacy `decisions.session_mining: true|false` key is honoured and
  resolves to the `session` source in both directions, so `true` switches it
  on. An explicit `sources.session` may narrow that, never widen it. The first
  write through the CLI or the API replaces the legacy key with
  `sources.session`.

### Presets

Presets are conveniences that write the same keys:

| Preset | Effect |
|--------|--------|
| `default` | What a config with no `decisions:` block resolves to. Every long-standing source on; session mining and broad discovery off. |
| `off` | No automatic capture. Stored decisions and manual entry keep working. |
| `local_only` | Deterministic capture only, session mining included: it is the only lane that produces without a key. Zero decision-extraction model calls. |
| `balanced` | The high-signal sources plus session mining and broad session discovery, which is fed by it; comment archaeology off. |
| `full` | Every source, every model stage. |

Editing any individual key after applying a preset drops the `preset` line,
and the resolved policy reads as `custom`.

### `llm: false`

A complete mode, not a degraded one:

- Transcripts are still read, markers and ADRs still parsed, and episodes still
  recorded.
- Manual decisions still work, and accepted decisions keep governing.
- It is the one switch that proves no decision extraction reaches a model.

### `agent_acceptance` and `capture_prompt`

These two keys are not about capture. Both ship off, and no preset turns
either on.

- **`agent_acceptance`.** Off, an agent can propose candidates and withdraw
  authority but never grant it. On, it may accept, and the acceptance is
  recorded as an agent's, with the session that signed it, on every surface,
  never as yours.
- **`capture_prompt`.** On, a successful `git commit` whose message carries two
  or more decision signals prompts the agent, once per session, to run
  `repowise decision add`. Never for a commit a record already cites, and never
  as anything but a proposal. An agent cannot decline a hook, so it ships off.
  Turn it on with `repowise decision config capture-prompt --on`.
- That command also installs what the prompt needs. The shared PostToolUse
  matcher excludes the shell tools on purpose (measured at 51% of hook
  invocations for 0.7% of emissions), so switching the prompt on adds a
  separate `Bash|PowerShell` PostToolUse entry, and switching it off removes it.
- The entry is per install, not per repository: other repositories on the
  machine pay a process start on shell calls and emit nothing unless they
  switch it on too, and switching it off here turns it off for all of them.

### Setting it from the CLI

```bash
repowise decision config show          # the resolved policy, per source
repowise decision config preset local_only
repowise decision source set comment --off
repowise decision source set adr --no-llm     # keep the parse, skip the model
repowise decision source set session_discovery --on
repowise decision config discovery --max-sessions 6 --max-input-tokens 12000
repowise decision llm --off
```

Every mutating command takes `--dry-run` to print the change without writing,
and `--format json` for scripts. Writes are atomic and preserve every unrelated
key in `config.yaml`.

### Session mining

`session` mining is **off by default**. On, `repowise update` reads
coding-agent session transcripts (Claude Code's `~/.claude/projects/`) for
durable decisions: user corrections, explicit choices with a stated reason, and
failed approaches replaced by working ones.

- Candidates pass deterministic gates first, then one batched LLM structuring
  call per update.
- Every produced field must quote the transcript verbatim, or it is dropped.
- **Mined decisions are stored as candidates**, however many sessions they
  recur across. Accepting one with `repowise decision confirm` makes it govern.
- Everything stays local: transcripts are read from your machine, staging lives
  in `.repowise/sessions/sessions.db`, and only the distilled decision text
  about the codebase is stored.

### Session discovery

`session_discovery` is the broad lane beside those gates. Once per update it
sends the user and assistant prose that update newly read, bounded by
`discovery.max_sessions` and `discovery.max_input_tokens`, to the model in a
single call, and asks for durable decisions the gates never surfaced.

- It reuses the transcript read the `session` source performs, so enabling it
  does not read your transcripts twice. It needs that source on.
- Every candidate must cite the span ids it rests on, and each cited quote is
  verified against that span's exact text before anything is stored.
- A candidate that cites a span that was not sent, quotes something no span
  says, or names a file the cited turns never touched is rejected and counted,
  never stored.
- Scope comes from the files those turns' tools touched; the model may select
  among them and may not add one.
- Everything it produces is a `proposed` candidate, like the deterministic
  lane; only `repowise decision confirm` makes a decision govern.

**Queue**

- Prose that does not fit one update's budget is not dropped. It stays queued
  in `.repowise/sessions/sessions.db`, the next update sends it oldest-first,
  and a queued span is never aged out before it is read.
- A provider failure leaves the same prose queued and retries it on the next
  two updates before dropping it, so an outage costs a round rather than the
  input.
- Switching the source off leaves what is already queued in place. New prose is
  captured only while the source is on, so there is no backfill of what was
  read while it was off.

### Dismissals

Dismissals are sticky: `repowise decision dismiss` keeps the record as a
`dismissed` tombstone, so reindexing never re-proposes the same decision. A
confirmed (`active`) decision is never walked back to `proposed` by a
re-extraction.

## `.repowise/decisions.yaml`

The one file under `.repowise/` you commit: it holds the decisions you
accepted. Everything else there is a local index you can delete and rebuild.

- `repowise decision export` writes it, and un-ignores it in your `.gitignore`
  if a `.repowise/` rule was hiding it.
- `repowise decision import` reconciles the store to it, with the file as the
  authority.
- Its format carries its own `version`; a file written by a newer Repowise is
  refused rather than downgraded.
- See [DECISIONS.md](../layers/DECISIONS.md) for the round trip.

## `.repowise/components.yaml`

Components name the parts of a repository that health, hotspots, bus factor,
test reach and dead code roll up to. See
[examples/components](../../examples/components/components.yaml) and
`repowise components check`.

- The file is multi-document Backstage YAML: `Component`, `System` and `Group`
  on `backstage.io/v1alpha1`.
- A repo-root `catalog-info.yaml` is read the same way, so a developer portal
  and Repowise share one file.

| Annotation | What it holds |
|------------|---------------|
| `repowise.dev/paths` | Gitignore-style globs, written with `/`, matched case-sensitively. |
| `repowise.dev/criticality` | `low`, `medium`, `high` or `critical`. |
| `repowise.dev/tags` | Tags. |
| `repowise.dev/capability` | Capability. |

**Declared and derived components**

- Without a file, components are derived from workspace services, then module
  pages, then top-level directories.
- A declared component claims the files its globs match. Where two match a
  file, the first declared wins.
- A declared component named like a derived one replaces it and, with no paths
  of its own, keeps its directory.
- A derived component is named after its directory, skipping route parameters
  and groups such as `[id]` or `(group)`.
- Where two derived components would share a name, the shallowest keeps it and
  each other adds its nearest parent directory other than `src`, `lib` or
  `app` (`workspace-web`), so a name a catalog already uses stays the same.

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
| `name` | Unique rule name: lowercase letters, digits, `_` and `-`, starting with a letter or digit, at most 63 characters. |
| `description`, `message` | Optional text for the rule. |
| `match`, `exclude` | Select components by `tags`, `names` or `criticality`; each list is any-of. |
| `when` | Conditions over the change's evidence; all must hold. |
| `action` | `warn`, `require_owner_review`, `require_tests` or `fail`. `fail` is the default for a gate. |
| `gate` | Wrap an existing gate instead: `security`, `coverage`, `doc_drift` or `health`. |
| `with` | That command's options for a `gate` rule. |

| Condition | Values |
|-----------|--------|
| `attribution` | `agent`, `human_attested` or `unknown`. |
| `agent_tier` | 1, 2 or 3. |
| `reaching_tests` | `missing` (a changed file in scope has no reaching test) or `present`. |
| `contract_break` | |
| `decision_crossed` | |
| `bus_factor_below` | |

- There is no risk-score condition.
- A wrapped gate runs exactly as the command does on its own. `doc_drift` is
  scoped to the change (`since`) unless the rule sets it.
- `require_owner_review` and `require_tests` are advisory: they are reported in
  every format and `repowise check` still exits 0. Only `fail` exits 1.
- A condition whose evidence is not available (no index, attribution off)
  leaves the rule not evaluated, and it never fires.
- Exit 2 (`policy_invalid`, every problem listed) on an unknown key, condition
  or gate option, a bad action, a duplicate rule name, or a file over 256,000
  bytes.
- Exit 2 also when a `fail` gate cannot be evaluated.

## `.repowise/teams.yaml`

Declared teams with the members who count as the team's activity, for
`repowise components ownership` and `get_context(include=["ownership"])` on a
component.

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

- Each team is a Backstage `Group`. `spec.members` lists forge logins, and the
  `repowise.dev/member-emails` annotation lists git author emails.
- Members are used only for matching commits and never appear in output.
- A CODEOWNERS `@org/team` (GitHub or GitLab, sections included) and a catalog
  `spec.owner` of `group:default/team` both resolve to the Group named `team`.
- Group entities in the catalog files add team names; their `spec.members` is
  not read.

## The `coverage:` block

Where coverage reports live and how their paths map. The full block is in
[test intelligence](../layers/TEST_INTELLIGENCE.md#configuration); the gates
are in [CI.md](../start/CI.md#gate-on-coverage).

| Key | Type | Default | What it does |
|-----|------|---------|--------------|
| `auto_discover` | bool | `true` | Look for reports at the discovery locations. |
| `artifacts` | list | discovery globs | Replace the discovery globs entirely. |
| `paths` | list | `[]` | Report paths or globs, relative to the repo root; bypass discovery. An entry may be `{path, path_prefix}`. |
| `ignore` | list | `[]` | Gitignore-style globs for files coverage leaves out. |
| `format` | string | sniffed | Force a report parser. |
| `strip_prefix` | string | none | Remove this leading prefix from report paths. |
| `path_prefix` | string | none | Prepend this prefix to report paths. |
| `reingest_on_update` | bool | unset | `true` re-reads the reports on every update; `false` keeps the stored coverage until `repowise coverage add`; unset re-reads a report written after the last ingest. |
| `fail_under` | number | none | Patch-coverage gate, percent 0-100. |
| `min_coverable_lines` | int | none | Small-change tolerance. |
| `fail_under_risky` | number | none | Gate over risky files only, percent 0-100. |
| `fail_under_branches` | number | none | Least share of branches taken on changed lines, percent 0-100. |
| `max_drop` | number | none | Most project coverage may fall from the base, in points. |
| `gates` | list | `[]` | [Path-scoped gates](../start/CI.md#path-scoped-gates): `{name, paths, fail_under, informational}`. |

- `repowise update` re-reads the reports when one in `coverage.paths`, or at a
  fixed discovery location such as `coverage/lcov.info`, was written after the
  last ingest, replacing coverage added by hand from another report.
- A report only a `**` glob finds is not watched; name it in `coverage.paths`.

## The `security:` block

Secret shapes of your own for `repowise security check`.

```yaml
security:
  patterns:
    - name: internal_token        # 1 to 40 of a-z, 0-9, _ and -, unique
      regex: 'itk_[A-Za-z0-9]{32}'
      severity: high              # low, med or high; high when omitted
```

| Key | Type | Default | What it does |
|-----|------|---------|--------------|
| `patterns[].name` | string | required | 1 to 40 of `a-z`, `0-9`, `_` and `-`; unique. |
| `patterns[].regex` | string | required | The secret shape. |
| `patterns[].severity` | string | `high` | `low`, `med` or `high`. |

- Each pattern is the secret kind `custom:<name>` (SARIF rule
  `custom/<name>`).
- It scans changed lines only, in every commit of the change, and masks a match
  to its first four characters and `****` in every format.
- It keeps its severity under test, fixture and example paths.
- A finding is keyed on file, kind and masked line, so two matches with the
  same visible prefix on one line share a baseline entry.
- At most 50 patterns of 500 characters. Lines over 4096 characters are not
  matched and are counted (`long_lines_skipped` in JSON); a slow regex that
  passes the checks stays slow.
- Exit 2 (`config_invalid`, every problem listed) on an unknown key, a missing
  name or regex, a duplicate or badly shaped name, a bad severity, or a regex
  that does not compile, can match zero characters, or nests repetition like
  `(a+)+` or `(a|aa)+` (best effort).

## The `attribution:` block

```yaml
attribution:
  enabled: true      # read public AI markers: bot authors, trailers, footers, git-ai notes, Agent Trace files
  capture: off       # off, local or notes
  agents: []         # agent target ids, e.g. claude-code, codex
```

| Key | Type | Default | What it does |
|-----|------|---------|--------------|
| `enabled` | bool | `true` | Read public AI markers: bot authors, trailers, footers, git-ai notes, Agent Trace files. |
| `capture` | string | `off` | `off`; `local` records the listed agents' edits as line hashes in `.repowise/attribution/`; `notes` also writes git-ai notes on `refs/notes/ai`. |
| `agents` | list | `[]` | Agent target ids, e.g. `claude-code`, `codex`. |

- The same block in `~/.repowise/config.yaml` applies to every repository, and
  the stricter value wins per key: `enabled: false` in either file, the lower
  capture level, and the agents both files list.
- So capture needs both files: a key unset in one file takes its default
  before the comparison, and a repo-only `capture: local` resolves to `off`.
- With `enabled: false` nothing about AI is stored or shown in the API, CLI,
  MCP, web or wiki.
- `DO_NOT_TRACK=1` keeps capture off.
- `repowise attribution status` shows the effective, repo and user values with
  the coverage of the indexed history.
- What each capture level stores, and what leaves the machine, is in
  [ATTRIBUTION.md](../layers/ATTRIBUTION.md).
- The same consent lets `repowise attribution backfill --agent
  claude-code,codex` bind past commits from the transcripts these agents keep
  on this machine.

## The `events:` block

```yaml
events:
  enabled: true            # snapshot components at each index run and diff them
  retention_days: 365      # older runs and events are dropped
  compact_after_days: 30   # older runs keep one per ISO week
  kinds: {new_hotspot: true}
  sinks: [jsonl]           # jsonl (.repowise/events.jsonl), stdout, or a plugin's sink
```

| Key | Type | Default | What it does |
|-----|------|---------|--------------|
| `enabled` | bool | `true` | Snapshot components at each index run and diff them. |
| `retention_days` | int | `365` | Older runs and events are dropped. |
| `compact_after_days` | int | `30` | Older runs keep one per ISO week. |
| `kinds` | mapping | every kind on except `new_hotspot` | Turn event kinds on or off. |
| `sinks` | list | `[jsonl]` | `jsonl` (`.repowise/events.jsonl`), `stdout`, or a plugin's sink. |

- An index run diffs into typed events against the run right before it when
  both ran under the same analyzer and parser stamp. A stamp change, or a run
  in between that took no snapshot, starts a new baseline.
- Owners are compared as keyed digests made with a random per-repository key
  in `.repowise/events.key`; a declared owner is named only when it is a group.
- Severity follows the component's `repowise.dev/criticality`.
- `repowise doctor` names any value in this block that fell back to its
  default.
- Read events with `repowise events` or `GET /api/repos/{id}/events`.

## The `refactoring:` block

Tunes the refactoring-intelligence layer: the structured Extract Class /
Extract Method / Extract Helper / Move Method / Break Cycle / Split File plans surfaced by
`repowise health --refactoring-targets`, `get_health(include=["refactoring"])`
and the web Refactoring tab. The deterministic detectors run inside the normal
health pass; this block tunes which fire and the optional code-generation step.

```yaml
refactoring:
  enabled: true               # the deterministic plans (zero LLM, in the health pass)
  detectors:
    disabled: []              # e.g. [move_method] to silence one detector
  min_confidence: null        # low | medium | high (confidence floor; null = no floor)
  llm:
    enabled: false            # code generation, off by default; uses chat's provider and model
```

| Key | Type | Default | What it does |
|-----|------|---------|--------------|
| `enabled` | bool | `true` | The deterministic plans, zero LLM, in the `init` / `update` health pass. `false` skips the whole detector pass. |
| `detectors.disabled` | list | `[]` | Silences named detectors (`extract_class`, `split_file`, `move_method`, ...) while the rest run. |
| `min_confidence` | string | `null` (no floor) | `low`, `medium` or `high`. |
| `llm.enabled` | bool | `false` | Code generation, using chat's provider and model. |

- Code generation is the only part that calls a provider. It never runs during
  indexing, only on an explicit request.
- `min_confidence` is applied when the plans are detected, so a plan below it
  is never persisted (like a disabled marker). A change takes effect on the
  next `init` / `update`.
- Surfaces that accept a `min_confidence` query parameter can only narrow
  further from this floor, not below it.
- Per-path disables reuse the `.repowise/health-rules.json` glob mechanism
  that markers use.
- Full reference: [REFACTORING.md](../layers/REFACTORING.md).

## The `assertions:` block

Names your own assertion helpers, for the test-quality markers that divide by a
test's assertion count.

```yaml
assertions:
  extra_names: [ensureInvariant, mustMatch]   # exact callee names, not prefixes
```

| Key | Type | Default | What it does |
|-----|------|---------|--------------|
| `extra_names` | list | `[]` | Exact callee names that count as assertions. |

Repowise recognises the xUnit and BDD families out of the box (`assert*`,
`expect*`) plus a per-language vocabulary for the idioms those miss, such as
Go's `t.Fatalf` and testify's `require`. A house helper that follows neither
convention is invisible to it, and a test built entirely of those helpers reads
as having no assertions at all. This block is the escape hatch, the same one
other test linters provide.

- **Names are exact and case-insensitive, never prefixes.** `ensureInvariant`
  matches `ensureInvariant(...)` and not `ensureConnectionPool(...)`. Prefix
  matching was measured against three real test corpora and matched production
  functions under test far more often than assertions.
- A name matches whether it is the call itself or the receiver of one, so both
  `ensureInvariant(x)` and `ensureInvariant(x).isTrue()` count.
- **Nothing here can change a health score.** These names reach only the
  advisory assertion total; the calibrated `large_assertion_block` and
  `duplicated_assertion_block` markers read a separate count that takes no
  configuration. A wrong name costs signal, never a wrong score.
- Changing the list re-walks the affected files on the next `init` / `update`.

---

## The `health-rules.json` file

A separate JSON file, not part of `config.yaml`, that tunes code-health
biomarkers: which ones run, their severity, and per-path overrides. Malformed
JSON never raises: Repowise warns and falls back to an empty config.

```json
{
  "profile": null,
  "disabled_biomarkers": [],
  "severity_overrides": {
    "high_churn": "low"
  },
  "rules": [
    {
      "path": "legacy/**",
      "disabled_biomarkers": ["long_function"],
      "severity_overrides": {
        "duplication": "critical"
      }
    }
  ]
}
```

| Key | Default | What it does |
|-----|---------|--------------|
| `profile` | `null` | Named calibration profile; only `"small-team"` is defined. |
| `disabled_biomarkers` | `[]` | Biomarker names to skip repo-wide. |
| `severity_overrides` | `{}` | Maps a biomarker name to a severity label. |
| `rules[]` | `[]` | Per-path overrides, evaluated in order. |
| `rules[].path` | required | Gitignore-style glob matched against the file path; `*` stops at a `/`, so use `**` for nested files. The aliases `path_glob` and `glob` are accepted and treated the same. |
| `rules[].disabled_biomarkers` | `[]` | Biomarkers to skip for files matching `path`. |
| `rules[].severity_overrides` | `{}` | Severity overrides for files matching `path`. |

- Valid severity labels: `low`, `medium`, `high`, `critical`.
- Only the severity **label** is overridable, never the underlying numeric
  weights or caps that produce the health score.
- Full reference: [CODE_HEALTH.md](../layers/CODE_HEALTH.md#configuration).

---

## LLM providers

### Anthropic (Claude)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

| Model | Notes |
|-------|-------|
| `claude-sonnet-5-5` | Best balance of quality and cost |
| `claude-opus-5-5` | Highest quality, higher cost |
| `claude-haiku-5-5` | Default, fastest, lowest cost |

```bash
repowise init --provider anthropic --model claude-haiku-5-5
```

### OpenAI (GPT)

```bash
export OPENAI_API_KEY="sk-..."
repowise init --provider openai --model gpt-5.6-luna
```

For an OpenAI-compatible Qwen3 endpoint served by vLLM or SGLang:

```bash
export OPENAI_BASE_URL="http://localhost:8000/v1"
repowise init --provider openai --model qwen3 --reasoning off
```

**Local gateways and custom providers.** The same adapter works with local
gateways such as 9router and with any OpenAI-compatible custom provider:

- Set the gateway's API key under `OPENAI_API_KEY`.
- Point `OPENAI_BASE_URL` at its `/v1` endpoint.
- Use the model id returned by that endpoint's `/models` response.

```bash
export OPENAI_API_KEY="your-9router-dashboard-key"
export OPENAI_BASE_URL="http://localhost:20128/v1"
repowise init --provider openai --model ag/gemini-3.7-flash-medium
```

- Repowise takes namespaced model ids from compatible gateways as-is, so the
  model does not need to start with `gpt-`.
- For another local provider, replace the key, URL and model with that
  provider's values.

**Interactive setup.** In an interactive `repowise init`, choose
**OpenAI-compatible (Custom / local gateway)**. Repowise validates the
endpoint, collects the key without echoing it, verifies the gateway through
`/models`, and lets you search the discovered models or enter an exact model
id. If `/models` is unavailable, you can retry the endpoint and key, continue
with a manual model id, or return to the provider menu:

```text
Base URL [http://localhost:20128/v1]:
API key (hidden): <paste the gateway key>
✓ Connected — discovered 47 model(s).
Select model: ag/gemini-3.7-flash-medium
```

- The endpoint goes in the repo's gitignored `.repowise/.env`. The key is saved
  there only after confirmation, and `--no-save-key` always keeps it
  process-local.
- The runtime provider (`openai`) and exact model id are written to
  `config.yaml`.
- Choose the separate **openai** row for the official OpenAI endpoint.
- Scripted runs (`--yes`, CI, or non-TTY) stay non-interactive: use
  environment variables and `--provider openai`.

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

Gemini is also the default embedding provider when `GEMINI_API_KEY` is set.

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

The default Kimi Code endpoint supports `kimi-for-coding` and
`kimi-for-coding-highspeed`. Repowise uses their required sampling parameters
automatically.

K2.x models use the separate Kimi Open Platform. Supply a key from that
platform together with its OpenAI-compatible endpoint:

```bash
export KIMI_API_KEY="..."
export KIMI_BASE_URL="https://api.moonshot.ai/v1"
repowise init --provider kimi --model kimi-k2.6
```

For K2.x models, Repowise selects the required sampling parameters for
thinking or instant mode.

### Ollama (local, no API key)

```bash
export OLLAMA_BASE_URL="http://localhost:11434"
repowise init --provider ollama --model llama3.2
```

- Repowise sizes the model's context window (`num_ctx`) to each prompt, so
  pages are not cut to Ollama's small default window.
- Set `REPOWISE_OLLAMA_NUM_CTX` to pin it instead, for example to stay within a
  machine's memory.
- Requests are sent one at a time. If the server runs with
  `OLLAMA_NUM_PARALLEL` above 1, set the same value where you run Repowise to
  send that many at once.

### LiteLLM (100+ providers)

```bash
export LITELLM_API_KEY="..."
repowise init --provider litellm --model azure/gpt-4
```

### Eden AI (hosted, with an EU endpoint)

[Eden AI](https://www.edenai.co/) is a hosted gateway with an EU endpoint, so
inference can stay in the EU without self-hosting anything. Every other hosted
provider in this list terminates in the US; for a team working under a DPA,
the alternatives are Ollama or LiteLLM on your own infrastructure.

Point at the EU endpoint with `EDENAI_BASE_URL`. It covers the embedder as
well, since both read the same variable:

```bash
export EDENAI_API_KEY="..."
export EDENAI_BASE_URL="https://api.eu.edenai.run/v3"
repowise init --provider edenai --model mistral/mistral-small-latest
```

Without that variable, requests go to the global endpoint:

```bash
export EDENAI_API_KEY="..."
repowise init --provider edenai --model openai/gpt-5-mini --reasoning low
```

- **Aggregator.** One key reaches many vendors (Mistral, GPT, Claude, Gemini,
  Cohere, DeepSeek, Llama) through a single OpenAI-compatible endpoint, with
  models addressed as `vendor/model`.
- The live catalogue is public and needs no authentication at
  <https://api.edenai.run/v3/models>, with the embedding models at
  <https://api.edenai.run/v3/embeddings/models>.
- `reasoning` is forwarded as OpenAI `reasoning_effort` for OpenAI reasoning
  models routed through Eden (e.g. `openai/gpt-5*`); other models expose only
  `auto`.

**Embeddings.** The same key also selects `edenai` as the embedder for
semantic search. Its default is `amazon/amazon.titan-embed-text-v2:0`, served
from an EU region and the cheapest per token of the models this adapter
declares. Pick another with `REPOWISE_EMBEDDING_MODEL`:

```bash
export REPOWISE_EMBEDDING_MODEL="google/gemini-embedding-001"
```

Only the models in `EdenAIEmbedder._DIMS` are accepted, because a wrong
dimension count would silently mis-size stored vectors. Adding one means
adding its measured width to that table.

### Provider auto-detection

The CLI, `repowise serve` chat and jobs, and the MCP server resolve provider,
model and base URL the same way; first match wins:

1. `--provider` / `--model` flags, or a per-request choice in the web UI
2. `provider`, `model`, and `<provider>: {base_url: ...}` in `.repowise/config.yaml`
3. the global default saved from the web UI (`~/.repowise/provider_config.json`)
4. `REPOWISE_PROVIDER` / `REPOWISE_MODEL` and the provider's `*_BASE_URL` variable
5. API key env vars: `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `OPENROUTER_API_KEY` → `OLLAMA_BASE_URL` → `GEMINI_API_KEY` → `DEEPSEEK_API_KEY` → `KIMI_API_KEY` → `EDENAI_API_KEY`

- Picking a model in the web UI for a repo writes it to that repo's
  `config.yaml`, so the CLI uses it too.
- A local OpenAI-compatible server such as LM Studio works as
  `provider: openai` with `openai: {base_url: http://localhost:1234/v1}`.
- Because `config.yaml` may be committed, a non-local base URL there only
  receives a key that is not in the repo's `.repowise/.env` after you confirm
  it in Settings or with `repowise init`.
- A loopback base URL needs no API key.
- The repo's Settings page sets all of this without editing the file: the
  model field lists the provider's live models and takes any id, and Test
  connection checks a base URL before you save it.

---

## Embeddings (for semantic search)

The embedder is separate from the LLM provider.

| Embedder | Env var | Notes |
|----------|---------|-------|
| `gemini` | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | Default when key is present |
| `openai` | `OPENAI_API_KEY` | OpenAI `text-embedding-3-small` |
| `openrouter` | `OPENROUTER_API_KEY` | Routed through OpenRouter |
| `ollama` | `OLLAMA_EMBEDDING_MODEL` | Local Ollama embeddings, no API key |
| `edenai` | `EDENAI_API_KEY` | Eden AI gateway, see [Eden AI](#eden-ai-hosted-with-an-eu-endpoint) |
| `mock` | n/a | Dummy embeddings, no semantic search (default when no key is detected) |

```bash
repowise init --embedder openai
repowise reindex --embedder gemini   # switch embedder and rebuild index
```

**Which embedder a run uses**, first match wins:

1. `--embedder`
2. `REPOWISE_EMBEDDER`
3. the `embedder` pin in `.repowise/config.yaml`
4. the first key present, in the order gemini, openai, openrouter, ollama,
   edenai
5. `embedder` in `~/.repowise/config.yaml`
6. `mock`

`reindex` skips a `mock` pin when a real embedder can be found.

**The pin is the record.** The `embedder` key in `config.yaml` records what
built the vector store, so writers and readers both use it.

- `search`, `serve` and the MCP server resolve it from there first, and fall
  back to the environment only when nothing is pinned. Otherwise a repo indexed
  without a key would be queried with whatever API key happened to be
  exported, and the wider query vectors would match nothing in the narrower
  stored table.
- `reindex` writes the embedder it used back to `config.yaml`. Without that,
  the next `update` would read the old pin, write vectors at the old width, and
  rebuild the store from scratch, discarding what the reindex built.

**Model, dimensions and timeout.** `REPOWISE_EMBEDDING_MODEL` overrides the
model for whichever embedder is active. `REPOWISE_EMBEDDING_DIMS` and
`REPOWISE_EMBEDDING_TIMEOUT` apply the same way. The provider-prefixed
variants [below](#embeddings) (`OPENAI_*`, `GEMINI_*`, `OLLAMA_*`,
`OPENROUTER_*`, `EDENAI_*`) narrow a setting to one embedder and take
precedence over the shared name.

---

## BYOK (Bring Your Own Key)

API keys are resolved in this order:

1. **Environment variable**: set before running Repowise
2. **`.repowise/.env`**: persisted from interactive setup, loaded automatically
3. **`~/.repowise/provider_config.json`**: keys saved from the web UI
4. **Interactive prompt**: Repowise asks during `init` if no key is found, then saves to `.repowise/.env`

The `.repowise/.env` file is gitignored automatically.

---

## Environment variables

### Provider API keys

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI or OpenAI-compatible gateway API key |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Google Gemini API key |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `DEEPSEEK_API_KEY` | DeepSeek API key |
| `KIMI_API_KEY` | Kimi API key |
| `LITELLM_API_KEY` | LiteLLM proxy key |
| `LITELLM_API_BASE` | LiteLLM proxy base URL |

### Provider base URLs

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_BASE_URL` | Override the Anthropic API base URL |
| `OPENAI_BASE_URL` | Override the OpenAI API base URL (used for vLLM/SGLang, 9router, and other compatible endpoints) |
| `GEMINI_BASE_URL` | Override the Gemini API base URL |
| `OLLAMA_BASE_URL` | Ollama server URL (default: `http://localhost:11434`) |
| `REPOWISE_OLLAMA_NUM_CTX` | Fixed Ollama context window; unset sizes it to each prompt |
| `OLLAMA_NUM_PARALLEL` | Ollama requests Repowise sends at once (default: 1) |
| `DEEPSEEK_BASE_URL` | Override the DeepSeek API base URL |
| `KIMI_BASE_URL` | Override the Kimi API base URL |
| `LITELLM_BASE_URL` | Override the LiteLLM proxy base URL |

### Provider and model overrides

| Variable | Description |
|----------|-------------|
| `REPOWISE_PROVIDER` | Provider when neither a flag nor `config.yaml` names one (skips auto-detection) |
| `REPOWISE_MODEL` | Model when neither a flag nor `config.yaml` names one |
| `REPOWISE_DOC_MODEL` | Override the model used for `get_answer` synthesis specifically |
| `REPOWISE_REASONING` | Override `reasoning` (see [valid values](#reasoning)) |
| `REPOWISE_ANSWER_TIMEOUT_S` | Seconds `get_answer` waits for synthesis before giving up, capped at 600s. Default: a [per-provider budget](#repowise_answer_timeout_s). |

#### `REPOWISE_ANSWER_TIMEOUT_S`

- Default per provider: 60s for the remote API providers, 120s for `ollama`
  and `litellm`, 180s for the agent CLI providers (`claude_cli`, `codex_cli`,
  `copilot_cli`, `opencode`).
- Raise it if your model is slower than its class suggests; lower it if you
  would rather an agent fail fast than block.
- Your MCP client enforces its own tool timeout underneath this one. A value
  above it produces a client-side error instead of Repowise's diagnosable
  "synthesis exceeded its budget" response.

### Embeddings

| Variable | Description |
|----------|-------------|
| `REPOWISE_EMBEDDER` | Embedder: `gemini`, `openai`, `ollama`, `openrouter`, `edenai`, or `mock` |
| `REPOWISE_EMBEDDING_MODEL` | Embedding model, applies to any embedder |
| `REPOWISE_EMBEDDING_DIMS` | Embedding output dimensions (optional; inferred from the model otherwise) |
| `REPOWISE_EMBEDDING_TIMEOUT` | Embed request timeout in seconds (default: `30` for `ollama`, `10` elsewhere). See notes. |
| `REPOWISE_VECTOR_SEARCH_TIMEOUT_S` | Seconds one vector-store query may take (default: `30`, capped at `120`). See notes. |
| `OPENAI_EMBEDDING_TIMEOUT` | As `REPOWISE_EMBEDDING_TIMEOUT`, `openai` only; takes precedence over the shared variable |
| `GEMINI_EMBEDDING_TIMEOUT` | As above, `gemini` only |
| `OPENROUTER_EMBEDDING_TIMEOUT` | As above, `openrouter` only |
| `EDENAI_EMBEDDING_TIMEOUT` | As above, `edenai` only |
| `OLLAMA_EMBEDDING_MODEL` | Ollama embedding model (also selects the `ollama` embedder) |
| `OLLAMA_EMBEDDING_DIMS` | Ollama embedding output dimensions (optional; inferred from the model otherwise) |
| `OLLAMA_EMBEDDING_TIMEOUT` | As above, `ollama` only. See notes. |

Notes:

- `REPOWISE_EMBEDDING_TIMEOUT`: raise it for a local endpoint. One request
  embeds a whole batch, and an expired batch is reported only as
  `N/N items failed to embed`.
- With neither `OLLAMA_EMBEDDING_TIMEOUT` nor `REPOWISE_EMBEDDING_TIMEOUT` set,
  an Ollama request gets 30s plus 2ms per input character, so a full chunk of
  long pages on a CPU-only model is not cut off. Setting either pins the
  timeout to that value.
- `REPOWISE_VECTOR_SEARCH_TIMEOUT_S`: the first query in a process pays for the
  store open, the first embed and the first ANN probe, which can run past 13s
  on a cold index where a warm query takes under a second. Raise it on a slow
  disk or a very large wiki. A timeout drops the semantic leg and logs a
  warning, leaving full-text hits only.
- An unparseable value in either timeout variable warns and keeps the default.

### Server and database

| Variable | Description |
|----------|-------------|
| `REPOWISE_DB_URL` | Use PostgreSQL instead of SQLite (e.g. `postgresql+asyncpg://...`) |
| `REPOWISE_DATABASE_URL` | Legacy alias for `REPOWISE_DB_URL`, still honored |
| `REPOWISE_HOST` | Default host for `repowise mcp` over HTTP/SSE (default: `127.0.0.1`). `repowise serve` takes `--host` and sets this variable to it |
| `REPOWISE_PORT` | API server port (default: `7337`) |
| `REPOWISE_API_URL` | Frontend only; backend URL for the web UI (default: `http://localhost:7337`) |
| `REPOWISE_API_KEY` | Bearer token required by clients calling the server API. Without it the server answers local callers only, and refuses any request from another host with 403 |
| `REPOWISE_CONFIG_DIR` | Override where Repowise looks for its config directory |
| `REPOWISE_GITHUB_WEBHOOK_SECRET` | Secret for verifying GitHub webhook signatures |
| `REPOWISE_GITLAB_WEBHOOK_TOKEN` | Token for verifying GitLab webhook requests |

The MCP SSE server port is the `repowise mcp --port` flag (default: `7338`).

### Telemetry

Anonymous usage telemetry is **enabled by default** (opt-out).

| Variable | Description |
|----------|-------------|
| `DO_NOT_TRACK` | Any truthy value disables telemetry (respects the cross-tool convention) |
| `REPOWISE_TELEMETRY_DISABLED` | Disables telemetry, Repowise-specific |
| `REPOWISE_TELEMETRY_DEBUG` | Prints the telemetry payload to stderr instead of sending it |

### Misc

| Variable | Description |
|----------|-------------|
| `REPOWISE_GIT_WINDOW_ANCHOR` | Set to `now` to measure git history windows (90-day churn, prior defects, decay, blame age) from wall-clock time. By default they are measured from the indexed commit's committer date. |
| `REPOWISE_SKIP_EDITOR_SETUP` | Truthy value stops `init` writing machine-wide editor config. See notes. |
| `REPOWISE_FORGE_HOSTS` | Self-managed git hosts as `host=forge` pairs separated by commas (`git.corp.com=gitlab,tfs.corp.com=azure`). Wins over `forges.hosts` in `config.yaml` |
| `REPOWISE_CHANGELOG` | Override the changelog source used by the "what's new" check |
| `REPOWISE_PARSE_WORKERS` | How many processes parse files during indexing. Default: your CPU count capped at 8, never more than the number of files to parse. See notes. |

Notes:

- `REPOWISE_SKIP_EDITOR_SETUP` skips the Claude Code / Claude Desktop MCP
  entry, the Claude Code hooks and the distill rewrite-hook offer. It is the
  same switch as `init --no-editor-setup`
  ([CLI_REFERENCE.md](CLI_REFERENCE.md#repowise-init-path)); the env var is the
  one to use for CI, sandboxes and benchmark runs that index many repos.
  Project-local files (`.repowise/mcp.json`, `CLAUDE.md`, Codex config) are
  written either way.
- `REPOWISE_PARSE_WORKERS`: each worker is a separate interpreter holding
  roughly 50 MB, so lower it on a memory-constrained machine. Raising it above
  8 is not measurably faster.

---

## Exclude patterns

Repowise respects your `.gitignore` automatically, in the same `gitwildmatch`
format git uses.

- Like git, it reads **nested `.gitignore` files**: a `.gitignore` in any
  subdirectory applies to that directory's contents.
- This matters for monorepos and yarn/npm workspaces, where a package keeps its
  own `.gitignore` excluding its build output (e.g. `dist/`, `coverage/`,
  generated bundles), so those exclusions apply without duplicating them at
  the repo root.

Add extra patterns with `--exclude` / `-x`:

```bash
repowise init -x vendor/ -x "*.generated.ts" -x proto/ -x "**/*.pb.go"
```

- Patterns are saved to `config.yaml` (`exclude_patterns`) and applied on
  later `update` runs.
- A `.repowiseIgnore` file (same gitignore syntax) at the repo root or in any
  subdirectory gives more granular control without touching `.gitignore`.
- Built-in exclusions, always applied: `.git/`, `.repowise/`, `node_modules/`,
  `__pycache__/`, `*.pyc`, `.venv/`, binary files, lockfiles, and minified
  assets.
- `--skip-tests` excludes test files, and `--skip-infra` excludes Dockerfiles,
  Makefiles and shell scripts. Both apply only to the run they are passed on
  and are not written to `config.yaml`, so pass them again on later
  `init`/`update` calls for the same exclusions.

---

## Submodules

Git submodule directories are excluded by default. To include them:

```bash
repowise init --include-submodules
```

Repowise reads `.gitmodules` to detect submodule paths. The flag is not written
to `config.yaml`; for a workspace, the choice is recorded in `state.json`
instead.

---

## PostgreSQL

For team deployments or larger repos, use PostgreSQL instead of SQLite:

```bash
export REPOWISE_DB_URL="postgresql+asyncpg://user:pass@localhost:5432/repowise"
repowise init
```

`REPOWISE_DATABASE_URL` is a legacy alias for `REPOWISE_DB_URL` and is still
honored. The schema is managed with Alembic migrations.

---

## Workspace config (`.repowise-workspace.yaml`)

A multi-repo [workspace](../scale/WORKSPACES.md) is configured by a
`.repowise-workspace.yaml` at the workspace root. Alongside the repo list it
carries optional `repos[].tags` and `conformance:` blocks, and a `contracts:`
block that tunes cross-repo contract detection.

### `repos[].tags`

Each repo entry may declare free-form `tags` used to group services in
conformance rules:

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

Declares architecture conformance rules (allow/deny dependency rules) checked
by `repowise workspace check` and the workspace Conformance view. See
[Architecture Conformance](../scale/WORKSPACES.md#architecture-conformance).

```yaml
conformance:
  rules:
    - source: frontend          # matcher: a glob over node id / repo / name
      target: db                # matcher
      allow: false               # optional, default false (deny). true = exception
      description: "..."         # optional, shown in reports
    - source: "tag:ui"          # matcher: tag:<name> (repos carrying that tag)
      target: "tag:data"
    - source: "*"                # matcher: * (any service)
      target: legacy-payments
```

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `source` | string (matcher) | required | The dependent side. `*`, `tag:<name>`, or a glob over node id / repo alias / display name |
| `target` | string (matcher) | required | The depended-upon side (same matcher forms) |
| `allow` | bool | `false` | `false` = deny (a matching dependency is a violation); `true` = whitelist an otherwise-denied edge |
| `description` | string | `""` | Human-readable rationale, surfaced in reports |

Rules are evaluated only against structural edges (HTTP, gRPC, socket, event,
package, db); behavioral co-change is never treated as a dependency.

---

## Deprecated / legacy aliases

| Old name | Current name | Notes |
|----------|--------------|-------|
| `max_pages_pct` | `coverage_pct` | Internal alias, still read for backward compatibility |
| `REPOWISE_DATABASE_URL` | `REPOWISE_DB_URL` | Still honored |
