# Configuration

The `.repowise/` directory, provider setup, API keys, and what's customizable.

---

## The `.repowise/` directory

Everything repowise knows about your repository lives here. It's created at the
repo root on first `init`.

```
.repowise/
├── wiki.db          # SQLite database — all pages, symbols, graph, git metadata, decisions
├── lancedb/         # Vector search index (LanceDB)
├── config.yaml      # Provider, model, embedder, exclude patterns
├── state.json       # Last sync commit, page counts, token usage
├── mcp.json         # MCP server configuration
└── .env             # API keys (gitignored automatically)
```

repowise adds `.repowise/` to your `.gitignore` automatically. The directory
should not be committed — it's a local cache, not a source of truth.

---

## `config.yaml`

The main configuration file. Created after first `init`, updated when you pass
`--commit-limit` or `--follow-renames` flags.

```yaml
provider: anthropic                  # LLM provider
model: claude-sonnet-4-6             # Model identifier
embedder: gemini                     # Embedding provider
reasoning: auto                      # auto | off | minimal
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

> **Code-health rules** are configured separately in
> `.repowise/health-rules.json` (per-file biomarker overrides) — see
> [CODE_HEALTH.md](CODE_HEALTH.md#configuration).

---

## LLM providers

### Anthropic (Claude)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

| Model | Notes |
|-------|-------|
| `claude-sonnet-4-6` | Default — best balance of quality and cost |
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

### Ollama (local — no API key)

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
| `mock` | — | Dummy embeddings, no semantic search |

```bash
repowise init --embedder openai
repowise reindex --embedder gemini   # switch embedder and rebuild index
```

---

## BYOK (Bring Your Own Key)

API keys are resolved in this order:

1. **Environment variable** — set before running repowise
2. **`.repowise/.env`** — persisted from interactive setup, loaded automatically
3. **Interactive prompt** — repowise asks during `init` if no key is found, then saves to `.repowise/.env`

The `.repowise/.env` file is gitignored automatically.

---

## Environment variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Google Gemini API key |
| `OLLAMA_BASE_URL` | Ollama server URL (default: `http://localhost:11434`) |
| `LITELLM_API_KEY` | LiteLLM proxy key |
| `REPOWISE_PROVIDER` | Override provider (skips auto-detection) |
| `REPOWISE_EMBEDDER` | Embedder: `gemini`, `openai`, or `mock` |
| `REPOWISE_DB_URL` | Use PostgreSQL instead of SQLite (e.g. `postgresql+asyncpg://...`) |
| `REPOWISE_HOST` | API server host (default: `127.0.0.1`) |
| `REPOWISE_PORT` | API server port (default: `7337`) |
| `REPOWISE_MCP_PORT` | MCP SSE server port (default: `7338`) |
| `REPOWISE_API_URL` | Frontend only — backend URL for the web UI (default: `http://localhost:7337`) |

---

## Exclude patterns

repowise respects your `.gitignore` automatically (same `gitwildmatch` format git
uses). On top of that, add extra patterns via `--exclude` / `-x`:

```bash
repowise init -x vendor/ -x "*.generated.ts" -x proto/ -x "**/*.pb.go"
```

Patterns are saved to `config.yaml` and applied on subsequent `update` runs. You
can also create a `.repowiseIgnore` file (same gitignore syntax) at the repo root
or in any subdirectory for more granular control.

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
