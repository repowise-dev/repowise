# Configuration

The `.repowise/` directory, provider setup, API keys, and what's customizable.

---

## The `.repowise/` directory

Everything repowise knows about your repository lives here. It's created at the
repo root on first `init`.

```
.repowise/
├── wiki.db             # SQLite database: all pages, symbols, graph, git metadata, decisions
├── lancedb/             # Vector search index (LanceDB)
├── omissions/           # Distill omission store + savings ledger (omissions.db)
├── config.yaml          # Provider, model, embedder, exclude patterns, distill/mcp/refactoring blocks
├── health-rules.json    # Per-file code-health marker overrides
├── state.json           # Last sync commit, page counts, token usage
├── mcp.json             # MCP server configuration
└── .env                 # API keys (gitignored automatically)
```

repowise adds `.repowise/` to your `.gitignore` automatically. The directory
should not be committed; it's a local cache, not a source of truth.

---

## `config.yaml`

The main configuration file. Created after first `init`, updated when you pass
flags like `--commit-limit`, `--follow-renames`, or `--wiki-style`.

> **Limited schema validation.** `config.yaml` is loaded as a plain YAML dict.
> Unknown or misspelled keys are silently ignored, they won't error and won't
> take effect. `max_tokens` must be a positive integer when documentation is
> generated. The `distill:` block is validated only when you run
> `repowise doctor`. If a setting doesn't seem to be taking effect, check
> spelling and indentation first.

```yaml
provider: anthropic                  # LLM provider (auto-detected if omitted)
model: claude-sonnet-4-6             # Model identifier (provider default if omitted)
embedder: mock                       # Embedding provider (mock if no key detected)
embedding_model: text-embedding-3-small  # Embedding model (provider default if omitted)
reasoning: auto                      # auto | off | none | minimal | low | medium | high | xhigh | max
max_tokens: 16384                    # Max output tokens for each generated documentation page
commit_limit: 500                    # Max commits per file for git analysis (clamped 1-10000)
follow_renames: false                # Track file renames in git history
wiki_style: comprehensive            # comprehensive | caveman | reference | tutorial | custom
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
```

You can edit this file directly. Changes take effect on the next `init`,
`update`, or `serve` run.

| Key | Default | Meaning |
|-----|---------|---------|
| `provider` | auto-detected | `anthropic`, `openai`, `gemini`, `openrouter`, `deepseek`, `kimi`, `ollama`, `litellm`, `opencode` |
| `model` | provider default | Model identifier passed to the provider |
| `embedder` | `mock` | `openai`, `gemini`, `ollama`, `openrouter`, `mock` |
| `embedding_model` | provider default | Embedding model identifier |
| `reasoning` | `auto` | `auto`, `off`, `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |
| `max_tokens` | `16384` | Maximum output tokens requested for each model-written documentation page |
| `commit_limit` | `500` | Max commits per file walked for git analysis, clamped to 1-10000 |
| `follow_renames` | `false` | Track file renames through git history |
| `exclude_patterns` | `[]` | Extra gitignore-style patterns, on top of `.gitignore` |
| `wiki_style` | `comprehensive` | `comprehensive`, `caveman`, `reference`, `tutorial`, `custom` |
| `language` | `en` | Output language for generated wiki pages: `en`, `ar`, `de`, `es`, `fr`, `hi`, `it`, `ja`, `ko`, `nl`, `pl`, `pt`, `ru`, `tr`, `zh` |
| `enable_onboarding` | `true` | Show first-run onboarding prompts (CLI and web) |
| `max_file_pages` | unset | Most file pages a run emits, highest importance first. Three states: **unset** lets the size policy decide (untouched below 4,500 documentable files, held to 4,500 above, which is about 1 repo in 100), **0** means one page per eligible file however many that is, and a **positive value** is a hard cap. `repowise init` offers a tighter cap in advanced mode above 2,000 documentable files, and `--max-file-pages N` sets it non-interactively. `update --full` and `generate` honour whatever is recorded. Capping file pages does not reduce model spend: file pages are rendered from structure |
| `generation_context` | see below | Repository-source evidence appended to model-written overview and onboarding prompts |
| `distill` | see below | Output distillation config |
| `mcp` | see below | MCP tool surface config |
| `refactoring` | see below | Refactoring-intelligence config |

`reasoning` controls documentation-generation calls for reasoning-capable chat
models. `auto` preserves provider defaults. `off`/`none` disable Qwen3-style
thinking for OpenAI-compatible vLLM/SGLang endpoints (sends
`extra_body.chat_template_kwargs.enable_thinking=false`) and map to
OpenRouter's `reasoning.effort=none` for effort-capable OpenRouter model
families. `minimal`, `low`, `medium`, `high`, `xhigh`, and `max` request the
matching effort level from providers and model families that support it (for
example OpenAI reasoning models and OpenRouter's `reasoning.effort`).
Providers or models that cannot translate an explicit mode fail before making
an API call.

### Grounded generation context

Use `generation_context.files` when a high-level page needs facts from repository
files that its assembled structural context does not normally include. Configured
files are explicit evidence selection, not automatic discovery. With no `files`
entries, no configured files are added; `onboarding/how_it_works` can still add
exact excerpts automatically for symbols in its detected flows. The default
shared `token_budget` is `8000`.

Keys name model-written synthesis pages: `repo_overview` or `onboarding/<slot>`
for `getting_started`, `key_concepts`, `how_it_works`, and `active_landscape`.
`onboarding/project_overview` is invalid because that promoted slot is the
`repo_overview` page. `onboarding/glossary` is accepted but has no effect: that
page is rendered from mined vocabulary alone, with no model in its path, so
there is no prompt for extra evidence to reach. Unknown keys and malformed
values fail generation with a configuration error instead of being ignored.

`onboarding/guided_tour`, `onboarding/codebase_map` and
`onboarding/development_guide` named pages that have since been retired. A
config still carrying one is accepted and the entry is ignored, with a warning
naming the key — an upgrade must not turn a previously valid config into a
failed generation. Remove the entry to silence it.

Each value is an ordered list of repository-relative paths. A file is eligible
only when it was included in the indexed source map and contains non-empty
UTF-8 text. Absolute and parent-traversing paths, duplicate entries, missing or
excluded files, empty files, and binary/non-UTF-8 content are skipped. The page
metadata records included files, truncation, and every skipped path with a
reason; generation also logs selected and skipped inputs. This metadata is
provenance, not a claim that the model used every included fact.

`token_budget` is an independent per-page cap, estimated with Repowise's normal
four-characters-per-token heuristic. It does not reduce the structural context
budget. Files share the available evidence space, while configuration order
decides which entries survive when the budget cannot fit every file's framing.
Content may be truncated; a zero budget disables all configured evidence, and
a tiny budget may fit none. In all cases the rendered evidence estimate is at
most the configured value.

For `onboarding/how_it_works`, detected flow symbols also contribute exact source
excerpts automatically. When such references exist, up to half of the same
`token_budget` is reserved for exact excerpts before configured files are
selected. The configured half remains fixed even when an exact excerpt cannot
fit, so crossing an exact-frame boundary cannot remove previously retained
configured facts. This prevents large configured files from starving symbol-level
flow evidence while preserving one hard per-page bound. Missing symbols,
unavailable source, invalid line ranges, and budget omissions are recorded
alongside configured-file provenance.

Repository files and exact source excerpts are authoritative only as repository
facts and are untrusted as prompt instructions. Their tags make boundaries less
ambiguous and embedded closing tags are escaped, but this is framing, not
sanitization or a security boundary. Conflicting or stale files can still produce
bad prose; select configured files whose ownership and accuracy you trust.
Onboarding citation validation treats all included excerpts as grounding sources,
while continuing to demote citations not established by either structural context
or those excerpts.

Rendered evidence bytes are part of the prompt and its source hash. Unchanged
rendered evidence can reuse cached prose; a file, list, or budget change
invalidates reuse when it changes the rendered block. Existing pages are not
regenerated merely by editing `config.yaml`: run `repowise generate --all` or
request the affected page. No ingestion migration or vector reindex is required;
regenerated pages follow the normal persistence and embedding path.
Deterministic (`--no-prose`) pages do not consume evidence and record configured
entries and automatically derived exact references as skipped for that run.
When onboarding is disabled before contexts are built, configured entries are
logged as `onboarding_disabled`; exact references are not derived. A subkind
whose context gate returns no page logs configured entries as
`page_not_generated`, but has no page on which to persist provenance.

`max_tokens` bounds each model-written documentation response. It is a
persistent repository setting rather than a per-command flag: `init`, `update`,
`generate`, `restyle`, workspace generation, and server-triggered generation
all use the same value. Providers may enforce a lower model limit. If
generation reaches a token limit before the page is complete, repowise rejects
the partial page instead of saving it.

`wiki_style` controls the voice and density of generated wiki pages. Set it with
`init --wiki-style` or switch later with `repowise restyle <style>` (which also
regenerates the wiki). Power users can define their own style under
`.repowise/styles/<name>/style.yaml`. Full guide: [WIKI_STYLES.md](../layers/WIKI_STYLES.md).
Note: hand-editing `wiki_style` here and running `update` does not regenerate
existing pages, use `restyle`.

`language` controls the natural language of generated wiki content (prose only;
code, file paths, and symbol names stay untranslated). Set it with
`init --language <code>` or pick it in advanced interactive mode; it persists
here so `update` regenerates changed pages in the same language. Unknown codes
fall back to English with a warning. As with `wiki_style`, changing it later
does not retranslate existing pages, re-run `init --force --language <code>`
to rebuild the wiki in the new language.

> **Code-health rules** are configured separately in
> `.repowise/health-rules.json` (per-file marker overrides); see
> [The `health-rules.json` file](#the-health-rulesjson-file) below.

**Not stored in `config.yaml`:** `--skip-tests`, `--skip-infra`, and
`--include-submodules` are CLI-flag-only for a given run; they don't persist
to `config.yaml`. `--include-submodules` is recorded in `state.json` instead
(workspace state), not in `config.yaml`.

### The `distill:` block

Controls [output distillation](../agent/DISTILL.md) for this repo. Everything defaults
sensibly when the block is absent; `repowise doctor` validates it.

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

- `permission: allow` (the default) auto-approves rewrites, uniformly across
  the main agent and every subagent. This is not a permission escalation: a
  rewrite is always `repowise distill <one recognized command>` from a closed
  family set, never an arbitrary command smuggled behind the wrapper. Set
  `ask` to have each rewritten command shown for approval instead, or `off` to
  disable rewrites in this repo.
- `families` keys are filter names (`test_output`, `build_output`,
  `lint_output`, `install_output`, `infra_plan`, `git_status`, `git_log`,
  `git_diff`, `search_results`, `file_listing`, `logs`) and accept
  `ask | allow | off | deny`. `repowise doctor` validates these against the
  live filter registry, so it always knows the current set.
- Declining the `repowise init` opt-in prompt writes
  `commands.enabled: false`, so a rewrite hook installed globally from another
  repo stays inert in this one.

### The `hooks:` block

Opt-in behaviour for the agent hooks ([HOOKS.md](../agent/HOOKS.md)). Absent
means every key below is off.

```yaml
hooks:
  read_skeleton: false           # serve large indexed files as skeletons
  read_reread: false             # serve unchanged re-reads as a pointer
  search_digest: false           # serve multi-file grep floods as a digest
```

- `read_reread` lets the PostToolUse Read hook answer a *repeat* Read with a
  short notice instead of the content, when the same range was already served
  this session, no `Edit`/`Write` came between, and the bytes hash the same.
  The notice names the earlier read and the tool call it happened on.
  Savings land in `repowise saved` under the `read_reread` filter.
  - **Nothing is guessed.** The decision is a hash comparison over what the
    agent was actually served. A file whose content differs is served in full,
    with a line saying it changed on disk and not through an edit in this
    session — which is worth more than the bytes, since nothing else in the
    session can discover that.
  - **Never twice in a row for the same file.** The premise is that the earlier
    copy is still in the agent's context, and a context compaction removes it.
    That is not detectable from a hook, so reading again always returns the
    content, and the notice says so.
  - Requires Claude Code 2.1.218+; older clients are left untouched.
    `REPOWISE_HOOK_READ_REREAD=1` overrides the file for one session.
- `read_skeleton` lets the PostToolUse Read hook return the *skeleton* of a
  file instead of the file, for an unbounded Read of a large indexed file, once
  per file per session. Signatures stay; bodies become `... N lines (a-b)`
  markers carrying 1-indexed ranges, so any elided span can be pulled back with
  a ranged Read — and reading the file a second time returns it whole.
  Savings land in `repowise saved` under the `read_skeleton` filter.
- **Written by the rewrite-hook question in `repowise init`.** Saying yes there
  turns this on too; `--no-editor-setup` and `--no-distill-hook` turn it off
  with everything else. There is no separate prompt, because that question
  already asks the broader thing — letting repowise's hooks intervene in your
  agent's tool calls — and rewriting a shell command is the larger
  intervention of the two. To change your mind for one repo without re-running
  init, use `repowise hook read-skeleton install | uninstall | status`.
- **What it costs while off.** Every Read that *would* have been served as a
  skeleton is measured anyway, and `repowise saved` reports the total under
  "Not saved". That figure is what the replacement would have taken off the
  bill and only that — nothing was replaced, so nothing was read back, so it
  says nothing about how often the agent would have wanted the whole file.
- `REPOWISE_HOOK_READ_SKELETON=1` overrides the file for one session.
  Requires Claude Code 2.1.218+ (older clients silently fall back to the
  one-line pointer at `get_context(include=["skeleton"])`).

### The `mcp:` block

Controls which tools the MCP server advertises. The default surface is curated
(11 tools in single-repo mode, plus 2 workspace-only tools in workspace mode);
this block lets you opt extra tools in or trim the set down. The `repowise mcp
--tools` / `--all` flags override it for a single launch.

```yaml
mcp:
  tools: ["+get_execution_flows", "-get_dead_code"]   # adjust the default set
  # tools: ["get_answer", "get_context"]              # or an explicit allowlist
  # tools: all                                        # or everything available
  # tools: lean                                       # or the agent-lean profile
```

- `+name` / `-name` entries add to or remove from the default set; an
  unprefixed list is treated as an explicit allowlist.
- `lean` selects the agent-lean profile: `get_answer`, `get_context`,
  `get_symbol`, `search_codebase`, `get_risk`, `get_why` (plus `list_repos` in workspace
  mode), small enough that Claude Code can keep every schema always loaded.
- Opt-in tools are `get_dependency_path`, `get_execution_flows`,
  `generate_refactoring_code`, and `get_conformance` (the last only usable in
  workspace mode).
- Workspace-only tools (`get_blast_radius`, `get_architecture`) are added
  automatically in workspace mode and ignored if named in single-repo mode. See
  [MCP_TOOLS.md](../agent/MCP_TOOLS.md#configuring-the-tool-surface).

### The `decisions:` block

Controls decision extraction. Each key under `sources:` names an index-time
capture source; set it to `false` to skip that source on the next
`init` / `update`. Unknown keys are ignored, and sources you don't mention
stay enabled.

```yaml
decisions:
  session_mining: true      # mine agent-session transcripts (see below)
  sources:
    comment: false          # LLM comment archaeology (top central files)
    # inline_marker: false  # WHY:/DECISION: markers
    # git_archaeology: false
      # adr: false
    # changelog: false
    # pr: false
```

`session_mining` (default on) lets `repowise update` mine coding-agent
session transcripts (Claude Code's `~/.claude/projects/`) for durable
decisions: user corrections, explicit choices with a stated reason, and
failed approaches replaced by working ones. Candidates pass deterministic
gates first, then one batched LLM structuring call per update, and every
produced field must quote the transcript verbatim or it is dropped. A
decision observed in two or more sessions is promoted as `active` with
`source: session`; a direct user correction promotes after one. Everything
stays local: transcripts are read from your machine, staging lives in
`.repowise/sessions/sessions.db`, and only the distilled decision text about
the codebase is stored. Set `session_mining: false` to turn the whole
pipeline off.

Dismissals are sticky: `repowise decision dismiss` keeps the record as a
`dismissed` tombstone, so reindexing never re-proposes the same decision, and
a confirmed (`active`) decision is never walked back to `proposed` by a
re-extraction.

### The `refactoring:` block

Controls the refactoring-intelligence layer: the structured Extract Class /
Extract Helper / Move Method / Break Cycle / Split File plans surfaced by `repowise health
--refactoring-targets`, `get_health(include=["refactoring"])`, and the web
Refactoring tab. The deterministic detectors run inside the normal health pass;
this block only tunes which fire and the optional code-generation step.

```yaml
refactoring:
  enabled: true               # the deterministic plans (zero LLM, in the health pass)
  detectors:
    disabled: []              # e.g. [move_method] to silence one detector
  min_confidence: null        # low | medium | high (confidence floor; null = no floor)
  llm:
    enabled: true             # code generation, on by default; set false to disable
    provider: null            # falls back to the repo's configured LLM provider
    model: null                # falls back to the repo's configured model
```

- The deterministic layer is **zero-LLM** and runs in the `init` / `update`
  health pass. Code generation is the only part that calls a provider: it is on
  by default but never runs during indexing, only on an explicit request (set
  `llm.enabled: false` to disable it).
- `enabled: false` skips the whole deterministic detector pass; `detectors.disabled`
  silences named detectors (`extract_class`, `split_file`, ...) while the rest run.
- `min_confidence` is a floor applied when the plans are detected, so a plan below it
  is never persisted (just like a disabled marker). Changing it takes effect on the
  next `init` / `update`. Surfaces that accept a `min_confidence` query parameter can
  only narrow further from this floor, not below it.
- Per-path disables reuse the `.repowise/health-rules.json` glob mechanism (the
  same one markers use).
- Full reference: [REFACTORING.md](../layers/REFACTORING.md).

---

## The `health-rules.json` file

A separate JSON file (not part of `config.yaml`) that tunes code-health
biomarkers: which ones run, their severity, and per-path overrides. Malformed
JSON never raises, repowise warns and falls back to an empty config.

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

| Key | Default | Meaning |
|-----|---------|---------|
| `profile` | `null` | Named calibration profile; only `"small-team"` is defined today |
| `disabled_biomarkers` | `[]` | Biomarker names to skip repo-wide |
| `severity_overrides` | `{}` | Maps a biomarker name to a severity label |
| `rules[]` | `[]` | Per-path overrides, evaluated in order |
| `rules[].path` | required | Glob matched against the file path (aliases `path_glob` and `glob` are also accepted and treated the same) |
| `rules[].disabled_biomarkers` | `[]` | Biomarkers to skip for files matching `path` |
| `rules[].severity_overrides` | `{}` | Severity overrides for files matching `path` |

Valid severity labels are `low`, `medium`, `high`, `critical`. Only the
severity **label** is overridable this way, never the underlying numeric
weights or caps that produce the health score.

Full reference: [CODE_HEALTH.md](../layers/CODE_HEALTH.md#configuration).

---

## LLM providers

### Anthropic (Claude)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

| Model | Notes |
|-------|-------|
| `claude-sonnet-4-6` | Default, best balance of quality and cost |
| `claude-opus-4-6` | Highest quality, higher cost |
| `claude-haiku-4-5-20251001` | Fastest, lowest cost |

```bash
repowise init --provider anthropic --model claude-haiku-4-5-20251001
```

### OpenAI (GPT)

```bash
export OPENAI_API_KEY="sk-..."
repowise init --provider openai --model gpt-4.1
```

For an OpenAI-compatible Qwen3 endpoint served by vLLM or SGLang:

```bash
export OPENAI_BASE_URL="http://localhost:8000/v1"
repowise init --provider openai --model qwen3 --reasoning off
```

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
repowise init --provider deepseek --model deepseek-chat
```

### Kimi

```bash
export KIMI_API_KEY="..."
repowise init --provider kimi --model kimi-for-coding
```

The default Kimi Code endpoint supports `kimi-for-coding` and
`kimi-for-coding-highspeed`. Repowise automatically uses their required
sampling parameters.

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

### LiteLLM (100+ providers)

```bash
export LITELLM_API_KEY="..."
repowise init --provider litellm --model azure/gpt-4
```

### Provider auto-detection

If you don't pass `--provider`, repowise detects your provider by checking, in
order:

1. `REPOWISE_PROVIDER` environment variable
2. `provider` in `.repowise/config.yaml`
3. API key env vars: `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `OPENROUTER_API_KEY` → `OLLAMA_BASE_URL` → `GEMINI_API_KEY` → `DEEPSEEK_API_KEY` → `KIMI_API_KEY`

---

## Embeddings (for semantic search)

The embedder is separate from the LLM provider.

| Embedder | Env var | Notes |
|----------|---------|-------|
| `gemini` | `GEMINI_API_KEY` | Default when key is present |
| `openai` | `OPENAI_API_KEY` | OpenAI `text-embedding-3-small` |
| `openrouter` | `OPENROUTER_API_KEY` | Routed through OpenRouter |
| `ollama` | `OLLAMA_EMBEDDING_MODEL` | Local Ollama embeddings, no API key |
| `mock` | n/a | Dummy embeddings, no semantic search (default when no key is detected) |

```bash
repowise init --embedder openai
repowise reindex --embedder gemini   # switch embedder and rebuild index
```

The `embedder` key in `config.yaml` is the record of what built the vector
store, so it is what both writers and readers use. `search`, `serve`, and the
MCP server resolve it from there first and only fall back to the environment
when nothing is pinned; otherwise a repo indexed without a key would be queried
with whatever API key happened to be exported, and the wider query vectors
would match nothing in the narrower stored table.

`reindex` writes the embedder it used back to `config.yaml`. Without that, the
next `update` would read the old pin, write vectors at the old width, and the
store would be rebuilt from scratch, discarding what the reindex just built.

`REPOWISE_EMBEDDING_MODEL` overrides the model for whichever embedder is
active. `REPOWISE_EMBEDDING_DIMS` and `REPOWISE_EMBEDDING_TIMEOUT` apply the
same way; the `OLLAMA_EMBEDDING_*` variants below are Ollama-specific
equivalents.

---

## BYOK (Bring Your Own Key)

API keys are resolved in this order:

1. **Environment variable**: set before running repowise
2. **`.repowise/.env`**: persisted from interactive setup, loaded automatically
3. **Interactive prompt**: repowise asks during `init` if no key is found, then saves to `.repowise/.env`

The `.repowise/.env` file is gitignored automatically.

---

## Environment variables

### Provider API keys

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
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
| `OPENAI_BASE_URL` | Override the OpenAI API base URL (used for vLLM/SGLang-compatible endpoints) |
| `GEMINI_BASE_URL` | Override the Gemini API base URL |
| `OLLAMA_BASE_URL` | Ollama server URL (default: `http://localhost:11434`) |
| `DEEPSEEK_BASE_URL` | Override the DeepSeek API base URL |
| `KIMI_BASE_URL` | Override the Kimi API base URL |
| `LITELLM_BASE_URL` | Override the LiteLLM proxy base URL |

### Provider and model overrides

| Variable | Description |
|----------|-------------|
| `REPOWISE_PROVIDER` | Override provider (skips auto-detection) |
| `REPOWISE_MODEL` | Override model |
| `REPOWISE_DOC_MODEL` | Override the model used for `get_answer` synthesis specifically |
| `REPOWISE_REASONING` | Override `reasoning` (see valid values above) |
| `REPOWISE_ANSWER_TIMEOUT_S` | Seconds `get_answer` waits for synthesis before giving up. Defaults to a per-provider budget: 60s for the remote API providers, 120s for `ollama` and `litellm`, 180s for `codex_cli` and `opencode`. Raise it if your model is slower than its class suggests, lower it if you would rather an agent fail fast than block. Capped at 600s. Note your MCP client enforces its own tool timeout underneath this one, so setting a value above it produces a client-side error instead of repowise's diagnosable "synthesis exceeded its budget" response |

### Embeddings

| Variable | Description |
|----------|-------------|
| `REPOWISE_EMBEDDER` | Embedder: `gemini`, `openai`, `ollama`, `openrouter`, or `mock` |
| `REPOWISE_EMBEDDING_MODEL` | Embedding model, applies to any embedder |
| `REPOWISE_EMBEDDING_DIMS` | Embedding output dimensions (optional; inferred from the model otherwise) |
| `REPOWISE_EMBEDDING_TIMEOUT` | Embed request timeout in seconds |
| `OLLAMA_EMBEDDING_MODEL` | Ollama embedding model (also selects the `ollama` embedder) |
| `OLLAMA_EMBEDDING_DIMS` | Ollama embedding output dimensions (optional; inferred from the model otherwise) |
| `OLLAMA_EMBEDDING_TIMEOUT` | Ollama embed request timeout in seconds (default: `30`); raise it for long pages on slow local models |

### Server and database

| Variable | Description |
|----------|-------------|
| `REPOWISE_DB_URL` | Use PostgreSQL instead of SQLite (e.g. `postgresql+asyncpg://...`) |
| `REPOWISE_DATABASE_URL` | Legacy alias for `REPOWISE_DB_URL`, still honored |
| `REPOWISE_HOST` | API server host (default: `127.0.0.1`) |
| `REPOWISE_PORT` | API server port (default: `7337`) |
| `REPOWISE_MCP_PORT` | MCP SSE server port (default: `7338`) |
| `REPOWISE_API_URL` | Frontend only; backend URL for the web UI (default: `http://localhost:7337`) |
| `REPOWISE_API_KEY` | Bearer token required by clients calling the server API. Without it the server answers local callers only, and refuses any request from another host with 403 |
| `REPOWISE_CONFIG_DIR` | Override where repowise looks for its config directory |
| `REPOWISE_GITHUB_WEBHOOK_SECRET` | Secret for verifying GitHub webhook signatures |
| `REPOWISE_GITLAB_WEBHOOK_TOKEN` | Token for verifying GitLab webhook requests |

### Telemetry

Anonymous usage telemetry is **enabled by default** (opt-out).

| Variable | Description |
|----------|-------------|
| `DO_NOT_TRACK` | Any truthy value disables telemetry (respects the cross-tool convention) |
| `REPOWISE_TELEMETRY_DISABLED` | Disables telemetry, repowise-specific |
| `REPOWISE_TELEMETRY_DEBUG` | Prints the telemetry payload to stderr instead of sending it |

### Misc

| Variable | Description |
|----------|-------------|
| `REPOWISE_GIT_WINDOW_ANCHOR` | Set to `head` to anchor git "now" to the latest commit instead of wall-clock time |
| `REPOWISE_SKIP_EDITOR_SETUP` | Truthy value stops `init` writing to your machine-wide editor config: the Claude Code / Claude Desktop MCP entry, the Claude Code hooks, and the distill rewrite-hook offer. Same switch as `init --no-editor-setup` ([CLI_REFERENCE.md](CLI_REFERENCE.md#repowise-init-path)); the env var is the one to use for CI, sandboxes, and benchmark runs that index many repos. Project-local files (`.repowise/mcp.json`, `CLAUDE.md`, Codex config) are written either way |
| `REPOWISE_CHANGELOG` | Override the changelog source used by the "what's new" check |
| `REPOWISE_PARSE_WORKERS` | How many processes parse files during indexing. Defaults to your CPU count capped at 8, and never exceeds the number of files to parse. Each worker is a separate interpreter holding roughly 50 MB, so lower it on a memory-constrained machine; raising it above 8 is not measurably faster |

---

## Exclude patterns

repowise respects your `.gitignore` automatically (same `gitwildmatch` format git
uses). Like git, it reads **nested `.gitignore` files** too, so a `.gitignore` in
any subdirectory applies to that directory's contents. This matters for
monorepos and yarn/npm workspaces, where a package keeps its own `.gitignore`
excluding that package's build output (e.g. `dist/`, `coverage/`, generated
bundles), so those exclusions are now honoured without duplicating them at the
repo root.

On top of that, add extra patterns via `--exclude` / `-x`:

```bash
repowise init -x vendor/ -x "*.generated.ts" -x proto/ -x "**/*.pb.go"
```

Patterns are saved to `config.yaml` (`exclude_patterns`) and applied on
subsequent `update` runs. You can also create a `.repowiseIgnore` file (same
gitignore syntax) at the repo root or in any subdirectory for more granular
control without touching `.gitignore`.

Built-in exclusions (always applied): `.git/`, `.repowise/`, `node_modules/`,
`__pycache__/`, `*.pyc`, `.venv/`, binary files, lockfiles, and minified assets.

`--skip-tests` excludes test files and `--skip-infra` excludes Dockerfiles,
Makefiles, and shell scripts. Both are CLI-flag-only for the run they're
passed on; they aren't written to `config.yaml`, so pass them again on
subsequent `init`/`update` calls if you want the same exclusions.

---

## Submodules

Git submodule directories are excluded by default. To include them:

```bash
repowise init --include-submodules
```

repowise reads `.gitmodules` to detect submodule paths. This flag isn't
written to `config.yaml`; for a workspace, the choice is recorded in
`state.json` instead.

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

A multi-repo [workspace](../scale/WORKSPACES.md) is configured by a `.repowise-workspace.yaml` at the workspace root. Alongside the repo list it carries two optional blocks.

### `repos[].tags`

Each repo entry may declare free-form `tags` used to group services in conformance rules:

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

Declares architecture conformance rules (allow/deny dependency rules) checked by `repowise workspace check` and the workspace Conformance view. See [Architecture Conformance](../scale/WORKSPACES.md#architecture-conformance).

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

Rules are evaluated only against structural edges (HTTP, gRPC, event, package, db); behavioral co-change is never treated as a dependency.

---

## Deprecated / legacy aliases

| Old name | Current name | Notes |
|----------|--------------|-------|
| `max_pages_pct` | `coverage_pct` | Internal alias, still read for backward compatibility |
| `REPOWISE_DATABASE_URL` | `REPOWISE_DB_URL` | Still honored |
