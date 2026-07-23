---
layout: default
title: Configuration
nav_order: 9
---

# Configuration
{: .no_toc }

The `.repowise/` directory, provider setup, API keys, and what's customizable.
{: .fs-6 .fw-300 }

---

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## The `.repowise/` directory

Everything repowise knows about your repository lives here. It's created at the repo root on first `init`.

```
.repowise/
├── wiki.db          # SQLite database — all pages, symbols, graph, git metadata, decisions
├── lancedb/         # Vector search index (LanceDB)
├── config.yaml      # Provider, model, embedder, exclude patterns
├── state.json       # Last sync commit, page counts, token usage
├── mcp.json         # MCP server configuration
└── .env             # API keys (gitignored automatically)
```

repowise adds `.repowise/` to your `.gitignore` automatically. The directory should not be committed — it's a local cache, not source of truth.

---

## `config.yaml`

The main configuration file. Created after first `init`, updated when you pass `--commit-limit` or `--follow-renames` flags.

```yaml
provider: anthropic                  # LLM provider
model: claude-sonnet-4-6             # Model identifier
embedder: gemini                     # Embedding provider
reasoning: auto                      # auto | off/none | minimal | low | medium | high | xhigh | max
max_tokens: 16384                    # Max output tokens per generated documentation page
exclude_patterns:                    # Gitignore-style patterns
  - vendor/
  - "*.generated.*"
  - proto/
commit_limit: 500                    # Max commits per file for git analysis
follow_renames: false                # Track file renames in git history
editor_files:
  claude_md: true
  agents_md: true
```

You can edit this file directly. Changes take effect on the next `init`, `update`, or `serve` run.

`reasoning` controls documentation-generation calls for reasoning-capable chat
models. `auto` preserves provider defaults. `off`/`none` disables Qwen3-style thinking
for OpenAI-compatible vLLM/SGLang endpoints by sending
`extra_body.chat_template_kwargs.enable_thinking=false`, and maps to
OpenRouter's `reasoning.effort=none` for effort-capable OpenRouter model
families. `minimal` requests OpenAI's lowest supported reasoning effort for
supported OpenAI reasoning models and maps to OpenRouter's
`reasoning.effort=minimal` for effort-capable OpenRouter model families.
`low`, `medium`, `high`, `xhigh`, and `max` request native effort levels for
providers and model families that expose them. Providers or models that cannot
translate an explicit mode fail before making an API call. The interactive
`serve` chat streaming path is separate and does not currently consume this
setting.

`max_tokens` bounds each model-written documentation response. It is a
persistent repository setting used by `init`, `update`, `generate`, `restyle`,
workspace generation, and server-triggered generation. If a provider reports
that it reached this limit, repowise rejects the incomplete page instead of
saving it.

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

---

### OpenAI (GPT)

```bash
export OPENAI_API_KEY="sk-..."
```

| Model | Notes |
|-------|-------|
| `gpt-4.1` | Default |
| `gpt-4o` | Alternative |

```bash
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

---

### Gemini (Google)

```bash
export GEMINI_API_KEY="AI..."
# or:
export GOOGLE_API_KEY="AI..."
```

| Model | Notes |
|-------|-------|
| `gemini-3.1-flash-lite-preview` | Default — very fast and cheap |
| `gemini-2.5-pro` | Higher quality |

```bash
repowise init --provider gemini
```

Gemini is also the default embedding provider when `GEMINI_API_KEY` is set.

---

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

---

### Ollama (local)

Run models locally without any API key.

```bash
export OLLAMA_BASE_URL="http://localhost:11434"   # default if Ollama is running locally
```

```bash
repowise init --provider ollama --model llama3.2
```

Any model available in your local Ollama installation can be used.

---

### LiteLLM (100+ providers)

LiteLLM acts as a proxy supporting OpenAI, Azure, Cohere, Mistral, and dozens more.

```bash
export LITELLM_API_KEY="..."
repowise init --provider litellm --model azure/gpt-4
```

---

### Codex CLI

Use your authenticated local Codex CLI subscription instead of an API key:

```bash
npm install -g @openai/codex
codex login
codex login status
repowise init --provider codex_cli --codex --yes
```

`codex_cli/default` uses the Codex CLI's configured model. `codex_cli/gpt-5.5` passes `--model gpt-5.5` to `codex exec`. Repowise records token usage from Codex JSONL output and treats `codex_cli/*` cost as `$0.00` because billing is handled by Codex CLI auth/subscription.

Project-local Codex setup is controlled by:

```yaml
editor_files:
  agents_md: true
```

Use `repowise init --codex` to write `.codex/config.toml` and `.codex/hooks.json`.

---

### OpenCode CLI

Use your local OpenCode CLI instead of an API key:

```bash
curl -fsSL https://opencode.ai/install | bash
opencode       # first-run setup
repowise init --provider opencode --yes
```

`opencode/default` uses the OpenCode CLI's configured default model. `opencode/deepseek-v4-pro` passes `--model deepseek-v4-pro` to `opencode run`. Repowise records token usage from OpenCode JSONL output and treats `opencode/*` cost as `$0.00` because billing is handled by OpenCode's own auth/subscription.

OpenCode is detected automatically when installed — no auth setup needed from repowise. Use `opencode providers` to manage authentication and `opencode models` to list available models.

---

### Provider auto-detection

If you don't pass `--provider`, repowise detects your provider by checking:

1. `REPOWISE_PROVIDER` environment variable
2. `provider` in `.repowise/config.yaml`
3. API key environment variables, in order: `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `OPENROUTER_API_KEY` → `OLLAMA_BASE_URL` → `GEMINI_API_KEY` → `DEEPSEEK_API_KEY` → `KIMI_API_KEY`

Codex CLI is intentionally explicit: pass `--provider codex_cli` or set `REPOWISE_PROVIDER=codex_cli`. Similarly, OpenCode is detected automatically when on `PATH` but can be explicitly selected with `--provider opencode` or `REPOWISE_PROVIDER=opencode`.

---

## Embeddings (for semantic search)

Embeddings power semantic search via the vector index. The embedder is separate from the LLM provider.

| Embedder | Env var | Notes |
|----------|---------|-------|
| `gemini` | `GEMINI_API_KEY` | Default when key is present |
| `openai` | `OPENAI_API_KEY` | OpenAI text-embedding-3-small |
| `mock` | — | Dummy embeddings, no semantic search |

```bash
repowise init --embedder openai
repowise reindex --embedder gemini   # Switch embedder and rebuild index
```

---

## BYOK (Bring Your Own Key)

API keys can be provided in three ways, checked in this order:

1. **Environment variable** — set before running repowise
2. **`.repowise/.env`** — persisted from interactive setup, loaded automatically
3. **Interactive prompt** — repowise asks during `init` if no key is found, then saves to `.repowise/.env`

The `.repowise/.env` file is gitignored automatically. Example:

```env
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AI...
```

---

## `state.json`

Tracks the current indexing state. Do not edit manually.

```json
{
  "last_sync_commit": "59ca5c8a1b2c3d4e5f6a7b8c",
  "total_pages": 247,
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "total_tokens_used": 180432
}
```

`last_sync_commit` is used by `repowise update` to determine which files have changed since the last run.

---

## `mcp.json`

MCP server configuration for this repository.

```json
{
  "mcpServers": {
    "repowise": {
      "command": "repowise",
      "args": ["mcp", "/path/to/repo", "--transport", "stdio"],
      "description": "repowise: codebase intelligence for my-repo"
    }
  },
  "settings": {
    "port": 7338
  }
}
```

repowise also writes a `.mcp.json` at the repository root for Claude Code auto-discovery. This file can be committed.

---

## Environment variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Google Gemini API key |
| `KIMI_API_KEY` | Kimi API key |
| `KIMI_BASE_URL` | Override the Kimi API base URL |
| `OLLAMA_BASE_URL` | Ollama server URL (default: `http://localhost:11434`) |
| `LITELLM_API_KEY` | LiteLLM proxy key |
| `REPOWISE_PROVIDER` | Override provider (skips auto-detection) |
| `REPOWISE_DB_URL` | Use PostgreSQL instead of SQLite (e.g., `postgresql+asyncpg://...`) |
| `REPOWISE_HOST` | API server host (default: `127.0.0.1`) |
| `REPOWISE_PORT` | API server port (default: `7337`) |
| `REPOWISE_MCP_PORT` | MCP SSE server port (default: `7338`) |

---

## Exclude patterns

repowise respects your `.gitignore` automatically. All patterns in your root `.gitignore` are honored during file traversal using the same `gitwildmatch` format that git uses.

On top of `.gitignore`, you can add extra patterns via `--exclude` / `-x`:

```bash
repowise init -x vendor/ -x "*.generated.ts" -x proto/ -x "**/*.pb.go"
```

Patterns are saved to `config.yaml` and applied on subsequent `update` runs. In interactive advanced mode, you can enter exclude patterns one per line instead of comma-separated.

You can also create a `.repowiseIgnore` file (same gitignore syntax) at the repo root or in any subdirectory for more granular control.

Built-in exclusions (always applied):
- `.git/`, `.repowise/`, `node_modules/`
- `__pycache__/`, `*.pyc`, `.venv/`
- Binary files, lockfiles, and minified assets

Use `--skip-tests` to exclude test files and `--skip-infra` to exclude Dockerfiles, Makefiles, and shell scripts.

After traversal, repowise prints a filtering summary showing how many files were included and excluded by each rule (`.gitignore`, extensions, binary, oversized, etc.), along with a language breakdown.

---

## Submodules

Git submodule directories are excluded by default. If your repo uses submodules and you want to include them in the index, pass `--include-submodules`:

```bash
repowise init --include-submodules
```

repowise reads `.gitmodules` to detect submodule paths. When excluded, submodule directories are skipped entirely during traversal.

---

## PostgreSQL

For team deployments or larger repos, use PostgreSQL instead of SQLite:

```bash
export REPOWISE_DB_URL="postgresql+asyncpg://user:pass@localhost:5432/repowise"
repowise init
```

The schema is managed with Alembic migrations. See [Self-hosting →](self-hosting) for Docker setup with PostgreSQL.
