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

> **No schema validation.** `config.yaml` is loaded as a plain YAML dict.
> Unknown or misspelled keys are silently ignored, they won't error and won't
> take effect. The only part that's validated is the `distill:` block, and
> only when you run `repowise doctor`. If a setting doesn't seem to be taking
> effect, check spelling and indentation first.

```yaml
provider: anthropic                  # LLM provider (auto-detected if omitted)
model: claude-sonnet-4-6             # Model identifier (provider default if omitted)
embedder: mock                       # Embedding provider (mock if no key detected)
embedding_model: text-embedding-3-small  # Embedding model (provider default if omitted)
reasoning: auto                      # auto | off | none | minimal | low | medium | high | xhigh | max
commit_limit: 500                    # Max commits per file for git analysis (clamped 1-10000)
follow_renames: false                # Track file renames in git history
wiki_style: comprehensive            # comprehensive | caveman | reference | tutorial | custom
language: en                         # Output language for generated pages (en, zh, ru, hi, ...)
enable_onboarding: true               # Show first-run onboarding prompts
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
| `provider` | auto-detected | `anthropic`, `openai`, `gemini`, `openrouter`, `deepseek`, `ollama`, `litellm`, `opencode` |
| `model` | provider default | Model identifier passed to the provider |
| `embedder` | `mock` | `openai`, `gemini`, `ollama`, `openrouter`, `mock` |
| `embedding_model` | provider default | Embedding model identifier |
| `reasoning` | `auto` | `auto`, `off`, `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |
| `commit_limit` | `500` | Max commits per file walked for git analysis, clamped to 1-10000 |
| `follow_renames` | `false` | Track file renames through git history |
| `exclude_patterns` | `[]` | Extra gitignore-style patterns, on top of `.gitignore` |
| `wiki_style` | `comprehensive` | `comprehensive`, `caveman`, `reference`, `tutorial`, `custom` |
| `language` | `en` | Output language for generated wiki pages: `en`, `ar`, `de`, `es`, `fr`, `hi`, `it`, `ja`, `ko`, `nl`, `pl`, `pt`, `ru`, `tr`, `zh` |
| `enable_onboarding` | `true` | Show first-run onboarding prompts (CLI and web) |
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

`wiki_style` controls the voice and density of generated wiki pages. Set it with
`init --wiki-style` or switch later with `repowise restyle <style>` (which also
regenerates the wiki). Power users can define their own style under
`.repowise/styles/<name>/style.yaml`. Full guide: [WIKI_STYLES.md](WIKI_STYLES.md).
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

Controls [output distillation](DISTILL.md) for this repo. Everything defaults
sensibly when the block is absent; `repowise doctor` validates it.

```yaml
distill:
  enabled: true                  # master switch for this repo
  commands:
    enabled: true                # the command path (CLI + hook rewrites)
    permission: ask               # ask | allow | off (rewrite-hook posture)
    families:                     # per-filter overrides
      test_output: allow          #   auto-allow rewrites for test runs
      git_diff: deny              #   never rewrite git diff here
    disabled_filters: []          # filters to skip entirely, e.g. [logs]
  omission_store:
    ttl_days: 7                   # prune stored omissions after this many days
    max_mb: 50                    # size cap; oldest entries pruned first
```

- `permission: ask` (the default) means the agent's rewritten command is shown
  for approval; `allow` auto-approves rewrites; `off` disables rewrites here.
- `families` keys are filter names (`test_output`, `build_output`,
  `lint_output`, `git_status`, `git_log`, `git_diff`, `search_results`,
  `file_listing`, `logs`) and accept `ask | allow | off | deny`.
- Declining the `repowise init` opt-in prompt writes
  `commands.enabled: false`, so a rewrite hook installed globally from another
  repo stays inert in this one.

### The `mcp:` block

Controls which tools the MCP server advertises. The default surface is curated
(11 tools in single-repo mode, plus 3 workspace-only tools in workspace mode);
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
  `get_symbol`, `search_codebase`, `get_risk` (plus `list_repos` in workspace
  mode), small enough that Claude Code can keep every schema always loaded.
- Opt-in tools are `get_dependency_path` and `get_execution_flows`.
- Workspace-only tools (`get_blast_radius`, `get_conformance`,
  `get_architecture`) are added automatically in workspace mode and ignored if
  named in single-repo mode. See [MCP_TOOLS.md](MCP_TOOLS.md#configuring-the-tool-surface).

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
    # readme_mining: false
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
- Full reference: [REFACTORING.md](REFACTORING.md).

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

Full reference: [CODE_HEALTH.md](CODE_HEALTH.md#configuration).

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

### Ollama (local, no API key)

```bash
export OLLAMA_BASE_URL="http://localhost:11434"
repowise init --provider ollama --model llama3.2
```

### LiteLLM (100+ providers)

```bash
pip install "repowise[litellm]"
export LITELLM_API_KEY="..."
repowise init --provider litellm --model azure/gpt-4
```

### Provider auto-detection

If you don't pass `--provider`, repowise detects your provider by checking, in
order:

1. `REPOWISE_PROVIDER` environment variable
2. `provider` in `.repowise/config.yaml`
3. API key env vars: `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `OLLAMA_BASE_URL` → `GEMINI_API_KEY`

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
| `LITELLM_BASE_URL` | Override the LiteLLM proxy base URL |

### Provider and model overrides

| Variable | Description |
|----------|-------------|
| `REPOWISE_PROVIDER` | Override provider (skips auto-detection) |
| `REPOWISE_MODEL` | Override model |
| `REPOWISE_DOC_MODEL` | Override the model used for `get_answer` synthesis specifically |
| `REPOWISE_REASONING` | Override `reasoning` (see valid values above) |

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
| `REPOWISE_API_KEY` | Bearer token required by clients calling the server API |
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
| `REPOWISE_SKIP_EDITOR_SETUP` | Skip the interactive editor/MCP setup step |
| `REPOWISE_CHANGELOG` | Override the changelog source used by the "what's new" check |

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

A multi-repo [workspace](WORKSPACES.md) is configured by a `.repowise-workspace.yaml` at the workspace root. Alongside the repo list it carries two optional blocks.

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

Declares architecture conformance rules (allow/deny dependency rules) checked by `repowise workspace check` and the workspace Conformance view. See [Architecture Conformance](WORKSPACES.md#architecture-conformance).

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
