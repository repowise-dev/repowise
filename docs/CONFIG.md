# Configuration

The `.repowise/` directory, provider setup, API keys, and what's customizable.

---

## The `.repowise/` directory

Everything repowise knows about your repository lives here. It's created at the
repo root on first `init`.

```
.repowise/
├── wiki.db          # SQLite database: all pages, symbols, graph, git metadata, decisions
├── lancedb/         # Vector search index (LanceDB)
├── omissions/       # Distill omission store + savings ledger (omissions.db)
├── config.yaml      # Provider, model, embedder, exclude patterns
├── state.json       # Last sync commit, page counts, token usage
├── mcp.json         # MCP server configuration
└── .env             # API keys (gitignored automatically)
```

repowise adds `.repowise/` to your `.gitignore` automatically. The directory
should not be committed; it's a local cache, not a source of truth.

---

## `config.yaml`

The main configuration file. Created after first `init`, updated when you pass
flags like `--commit-limit`, `--follow-renames`, or `--wiki-style`.

```yaml
provider: anthropic                  # LLM provider
model: claude-sonnet-4-6             # Model identifier
embedder: gemini                     # Embedding provider
reasoning: auto                      # auto | off | minimal
wiki_style: comprehensive            # comprehensive | caveman | reference | tutorial
exclude_patterns:                    # Gitignore-style patterns
  - vendor/
  - "*.generated.*"
  - proto/
commit_limit: 500                    # Max commits per file for git analysis
follow_renames: false                # Track file renames in git history
```

You can edit this file directly. Changes take effect on the next `init`,
`update`, or `serve` run.

`reasoning` controls documentation-generation calls for reasoning-capable chat
models. `auto` preserves provider defaults. `off` disables Qwen3-style thinking
for OpenAI-compatible vLLM/SGLang endpoints (sends
`extra_body.chat_template_kwargs.enable_thinking=false`) and maps to OpenRouter's
`reasoning.effort=none` for effort-capable OpenRouter model families. `minimal`
requests OpenAI's lowest supported reasoning effort for supported OpenAI
reasoning models and maps to OpenRouter's `reasoning.effort=minimal`. Providers
or models that cannot translate an explicit mode fail before making an API call.

`wiki_style` controls the voice and density of generated wiki pages. Set it with
`init --wiki-style` or switch later with `repowise restyle <style>` (which also
regenerates the wiki). Power users can define their own style under
`.repowise/styles/<name>/style.yaml`. Full guide: [WIKI_STYLES.md](WIKI_STYLES.md).
Note: hand-editing `wiki_style` here and running `update` does not regenerate
existing pages, use `restyle`.

> **Code-health rules** are configured separately in
> `.repowise/health-rules.json` (per-file marker overrides); see
> [CODE_HEALTH.md](CODE_HEALTH.md#configuration).

### The `distill:` block

Controls [output distillation](DISTILL.md) for this repo. Everything defaults
sensibly when the block is absent; `repowise doctor` validates it.

```yaml
distill:
  enabled: true                  # master switch for this repo
  commands:
    enabled: true                # the command path (CLI + hook rewrites)
    permission: ask              # ask | allow | off (rewrite-hook posture)
    families:                    # per-filter overrides
      test_output: allow         #   auto-allow rewrites for test runs
      git_diff: deny             #   never rewrite git diff here
    disabled_filters: []         # filters to skip entirely, e.g. [logs]
  omission_store:
    ttl_days: 7                  # prune stored omissions after this many days
    max_mb: 50                   # size cap; oldest entries pruned first
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
(10 tools in single-repo mode, plus 3 workspace-only tools in workspace mode);
this block lets you opt extra tools in or trim the set down. The `repowise mcp
--tools` / `--all` flags override it for a single launch.

```yaml
mcp:
  tools: ["+get_execution_flows", "-get_dead_code"]   # adjust the default set
  # tools: ["get_answer", "get_context"]              # or an explicit allowlist
  # tools: all                                        # or everything available
```

- `+name` / `-name` entries add to or remove from the default set; an
  unprefixed list is treated as an explicit allowlist.
- Opt-in tools are `get_dependency_path` and `get_execution_flows`.
- Workspace-only tools (`get_blast_radius`, `get_conformance`,
  `get_architecture`) are added automatically in workspace mode and ignored if
  named in single-repo mode. See [MCP_TOOLS.md](MCP_TOOLS.md#configuring-the-tool-surface).

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
  min_confidence: medium      # low | medium | high (the surface gate)
  llm:
    enabled: true             # code generation, on by default; set false to disable
    provider: null            # falls back to the repo's configured LLM provider
    model: null
```

- The deterministic layer is **zero-LLM** and runs in the `init` / `update`
  health pass. Code generation is the only part that calls a provider: it is on
  by default but never runs during indexing, only on an explicit request (set
  `llm.enabled: false` to disable it).
- Per-path disables reuse the `.repowise/health-rules.json` glob mechanism (the
  same one markers use).
- Full reference: [REFACTORING.md](REFACTORING.md).

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

For OpenRouter:

```bash
repowise init --provider openrouter --model openai/gpt-5 --reasoning minimal
repowise init --provider openrouter --model x-ai/grok-4 --reasoning off
```

### Gemini (Google)

```bash
export GEMINI_API_KEY="AI..."      # or GOOGLE_API_KEY
repowise init --provider gemini
```

Gemini is also the default embedding provider when `GEMINI_API_KEY` is set.

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
| `ollama` | `OLLAMA_EMBEDDING_MODEL` | Local Ollama embeddings, no API key |
| `mock` | n/a | Dummy embeddings, no semantic search |

```bash
repowise init --embedder openai
repowise reindex --embedder gemini   # switch embedder and rebuild index
```

---

## BYOK (Bring Your Own Key)

API keys are resolved in this order:

1. **Environment variable**: set before running repowise
2. **`.repowise/.env`**: persisted from interactive setup, loaded automatically
3. **Interactive prompt**: repowise asks during `init` if no key is found, then saves to `.repowise/.env`

The `.repowise/.env` file is gitignored automatically.

---

## Environment variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Google Gemini API key |
| `OLLAMA_BASE_URL` | Ollama server URL (default: `http://localhost:11434`) |
| `OLLAMA_EMBEDDING_MODEL` | Ollama embedding model (also selects the `ollama` embedder) |
| `OLLAMA_EMBEDDING_DIMS` | Ollama embedding output dimensions (optional; inferred from the model otherwise) |
| `OLLAMA_EMBEDDING_TIMEOUT` | Ollama embed request timeout in seconds (default: `30`); raise it for long pages on slow local models |
| `LITELLM_API_KEY` | LiteLLM proxy key |
| `REPOWISE_PROVIDER` | Override provider (skips auto-detection) |
| `REPOWISE_EMBEDDER` | Embedder: `gemini`, `openai`, or `mock` |
| `REPOWISE_DB_URL` | Use PostgreSQL instead of SQLite (e.g. `postgresql+asyncpg://...`) |
| `REPOWISE_HOST` | API server host (default: `127.0.0.1`) |
| `REPOWISE_PORT` | API server port (default: `7337`) |
| `REPOWISE_MCP_PORT` | MCP SSE server port (default: `7338`) |
| `REPOWISE_API_URL` | Frontend only; backend URL for the web UI (default: `http://localhost:7337`) |

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

Patterns are saved to `config.yaml` and applied on subsequent `update` runs. You
can also create a `.repowiseIgnore` file (same gitignore syntax) at the repo root
or in any subdirectory for more granular control without touching `.gitignore`.

Built-in exclusions (always applied): `.git/`, `.repowise/`, `node_modules/`,
`__pycache__/`, `*.pyc`, `.venv/`, binary files, lockfiles, and minified assets.

Use `--skip-tests` to exclude test files and `--skip-infra` to exclude
Dockerfiles, Makefiles, and shell scripts.

---

## Submodules

Git submodule directories are excluded by default. To include them:

```bash
repowise init --include-submodules
```

repowise reads `.gitmodules` to detect submodule paths.

---

## PostgreSQL

For team deployments or larger repos, use PostgreSQL instead of SQLite:

```bash
export REPOWISE_DB_URL="postgresql+asyncpg://user:pass@localhost:5432/repowise"
repowise init
```

The schema is managed with Alembic migrations.

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
      allow: false              # optional, default false (deny). true = exception
      description: "..."        # optional, shown in reports
    - source: "tag:ui"          # matcher: tag:<name> (repos carrying that tag)
      target: "tag:data"
    - source: "*"               # matcher: * (any service)
      target: legacy-payments
```

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `source` | string (matcher) | required | The dependent side. `*`, `tag:<name>`, or a glob over node id / repo alias / display name |
| `target` | string (matcher) | required | The depended-upon side (same matcher forms) |
| `allow` | bool | `false` | `false` = deny (a matching dependency is a violation); `true` = whitelist an otherwise-denied edge |
| `description` | string | `""` | Human-readable rationale, surfaced in reports |

Rules are evaluated only against structural edges (HTTP, gRPC, event, package, db); behavioral co-change is never treated as a dependency.
