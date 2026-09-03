---
layout: default
title: CLI Reference
nav_order: 4
---

# CLI Reference
{: .no_toc }

Every command, flag, and option.
{: .fs-6 .fw-300 }

---

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Global

```bash
repowise --version    # Print version
repowise --help       # Show help
repowise COMMAND --help   # Help for a specific command
```

Most commands accept an optional `PATH` argument — the root of the repository to operate on. If omitted, the current directory is used.

---

## `init`

Generate the full wiki for a codebase. Runs all four layers: ingestion, analysis, generation, persistence.

```bash
repowise init [PATH] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--provider` | string | auto | LLM provider: `anthropic`, `openai`, `openrouter`, `gemini`, `deepseek`, `kimi`, `ollama`, `litellm`, `codex_cli`, `opencode`, `edenai`, `mock` |
| `--model` | string | — | Model override (e.g., `claude-sonnet-4-6`, `gpt-4.1`) |
| `--embedder` | choice | auto | Embedding provider: `gemini`, `openai`, `mock` |
| `--prose` / `--no-prose` | flag | prose if a key | Write the subsystem (concept) pages as model prose (`--prose`, needs a key), or render the whole wiki from structure with no model and no spend (`--no-prose`). Every other page is structural either way. |
| `--index-only` / `--docs` | — | — | Deprecated hidden aliases. `--index-only` == `--no-prose`; `--docs llm` == `--prose`, `--docs deterministic` == `--no-prose`. |
| `--dry-run` | flag | false | Show generation plan and token estimate without running |
| `--test-run` | flag | false | Limit to top 10 files by PageRank (for validation) |
| `--skip-tests` | flag | false | Exclude test files |
| `--skip-infra` | flag | false | Exclude Dockerfiles, Makefiles, Terraform, shell scripts |
| `--exclude` / `-x` | string | — | Gitignore-style exclude pattern (repeatable: `-x vendor/ -x "*.gen.*"`) |
| `--include-submodules` | flag | false | Include git submodule directories (excluded by default) |
| `--concurrency` | int | 5 | Max concurrent LLM calls |
| `--reasoning` | choice | auto | Reasoning mode for supported providers: `auto`, `off`/`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max` |
| `--resume` | flag | false | Resume from last checkpoint after an interruption |
| `--force` | flag | false | Regenerate all pages even if up to date |
| `--commit-limit` | int | 500 | Max commits per file for git analysis (max 5000, saved to config) |
| `--follow-renames` | flag | false | Track file renames in git history (saved to config) |
| `--no-claude-md` | flag | false | Skip generating `.claude/CLAUDE.md` |
| `--agents` / `--no-agents` | flag | config | Generate or skip managed `AGENTS.md` for Codex |
| `--codex` / `--no-codex` | flag | prompt/skip | Generate or skip project-local Codex MCP config and hooks |
| `--distill-hook` / `--no-distill-hook` | flag | prompt/skip | Install or skip the Claude Code command-rewrite hook that routes noisy commands through `repowise distill` |
| `--editor-setup` / `--no-editor-setup` | flag | true | Register the MCP server and hooks in your global Claude Code / Claude Desktop config. `--no-editor-setup` indexes the repo and leaves everything outside it untouched |
| `--yes` / `-y` | flag | false | Skip the cost confirmation prompt |

### Examples

```bash
# Interactive setup (recommended for first run)
repowise init

# Non-interactive, specific provider
repowise init --provider anthropic --yes

# Use the authenticated local Codex CLI
repowise init --provider codex_cli --codex --yes
repowise init --provider opencode --yes

# OpenAI-compatible Qwen3 endpoint with thinking disabled
repowise init --provider openai --model qwen3 --reasoning off

# OpenRouter with minimal reasoning effort
repowise init --provider openrouter --model openai/gpt-5 --reasoning minimal

# Analysis plus a structural wiki, free, no API key needed
repowise init --no-prose

# Skip tests and infra, limit concurrency
repowise init --skip-tests --skip-infra --concurrency 3

# Exclude generated files and vendor directories
repowise init -x "*.generated.ts" -x vendor/ -x proto/

# Repo with git submodules — include them in indexing
repowise init --include-submodules

# Dry run to estimate cost before committing
repowise init --dry-run

# Resume an interrupted run
repowise init --resume
```

### What it does

1. **Ingestion** — parses all files with tree-sitter, builds dependency graph
2. **Analysis** — git churn/ownership, dead code detection, decision mining
3. **Generation** — every page is rendered from parsed structure; with a model (the default when a key is available) the subsystem (concept) pages are written as prose instead of stubs. `--no-prose` keeps them structural.
4. **Persistence** — writes to SQLite, builds vector index, generates managed editor instruction files

### Provider auto-detection

If `--provider` is not specified, repowise checks in order:
1. `REPOWISE_PROVIDER` environment variable
2. `.repowise/config.yaml` from a previous run
3. API key environment variables: `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `OPENROUTER_API_KEY` → `OLLAMA_BASE_URL` → `GEMINI_API_KEY` → `DEEPSEEK_API_KEY` → `KIMI_API_KEY` → `EDENAI_API_KEY`

---

## `update`

Incrementally sync the wiki after code changes. Diffs against the last indexed commit, regenerates only affected pages.

```bash
repowise update [PATH] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--provider` | string | — | Override LLM provider |
| `--model` | string | — | Override model |
| `--since` | string | — | Git ref to diff from (overrides saved `state.json`) |
| `--reasoning` | choice | auto | Reasoning mode for supported providers: `auto`, `off`/`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max` |
| `--cascade-budget` | int | auto | Max pages to regenerate from cascading changes |
| `--dry-run` | flag | false | Show affected pages without regenerating |
| `--agents` / `--no-agents` | flag | config | Generate or skip managed `AGENTS.md` after update |

### Examples

```bash
repowise update                      # Sync since last indexed commit
repowise update --since HEAD~10      # Re-sync last 10 commits
repowise update --reasoning off      # One-off supported-provider thinking-off run
repowise update --dry-run            # Preview what would change
repowise update --cascade-budget 20  # Limit cascade regeneration
```

---

## `watch`

Auto-update wiki on file saves. Watches for filesystem changes and debounces rapid saves.

```bash
repowise watch [PATH] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--provider` | string | — | Override LLM provider |
| `--model` | string | — | Override model |
| `--debounce` | int | 2000 | Debounce delay in milliseconds |

### Example

```bash
repowise watch --debounce 3000   # Wait 3 seconds after last save before updating
```

---

## `mcp`

Start the MCP server for AI editor integration.

```bash
repowise mcp [PATH] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--transport` | choice | stdio | `stdio` (for editors), `streamable-http` (for HTTP clients), or `sse` (legacy) |
| `--port` | int | 7338 | Port for HTTP/SSE transports |

### Examples

```bash
repowise mcp                              # stdio (Claude Code, Cursor, Cline)
repowise mcp --transport streamable-http  # HTTP on port 7338
repowise mcp --transport sse --port 7338  # legacy SSE
```

See [MCP Server →](mcp-server) for editor-specific configuration.

---

## `serve`

Start the API server and web dashboard.

```bash
repowise serve [PATH] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--port` | int | 7337 | API server port |
| `--host` | string | 127.0.0.1 | Host to bind |
| `--workers` | int | 1 | Number of uvicorn workers |
| `--ui-port` | int | 3000 | Web UI port |
| `--no-ui` | flag | false | Start API only, skip web UI |

### Examples

```bash
repowise serve                        # API on :7337, UI on :3000
repowise serve --no-ui                # API only
repowise serve --host 0.0.0.0         # Expose on network
repowise serve --port 8080 --ui-port 4000  # Custom ports
```

See [Web Dashboard →](web-dashboard) for what you can do from the UI.

---

## `search`

Full-text, semantic, or symbol search across the indexed wiki.

```bash
repowise search <QUERY> [PATH] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--mode` | choice | fulltext | `fulltext`, `semantic`, or `symbol` |
| `--limit` | int | 10 | Max results |

### Examples

```bash
repowise search "authentication flow"
repowise search "rate limiting" --mode semantic
repowise search "AuthService" --mode symbol
repowise search "database connection" --limit 20
```

For a synthesised answer rather than a keyword lookup, use [`ask`](#ask).

---

## `ask`

Answer a question about the codebase, with citations. The same synthesis the
`get_answer` MCP tool performs: hybrid retrieval followed by an LLM answer over
what it found, so this command costs an LLM call where the other query commands
do not.

```bash
repowise ask <QUESTION> [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--scope` | string | — | Restrict retrieval to a path prefix (e.g. `packages/cli/`) |
| `--path` | string | cwd | Repo (or workspace) root |
| `--repo` | string | — | Workspace repo alias to query |
| `--no-workspace` | flag | false | Force single-repo mode even inside a workspace |
| `--format` | choice | table | `table` or `json` |
| `--full` | flag | false | Emit the raw MCP tool payload |

### Examples

```bash
repowise ask "how does the retry backoff work?"
repowise ask "where is the session cookie set?" --format json
repowise ask "how is width resolved?" --scope packages/cli/
repowise ask "why is auth split across two modules?" --full
```

`confidence: high` is content-grounded, so it can be cited directly. A
low-confidence answer returns `best_guesses` instead of going silent.

Also available as the Claude Code slash command `/repowise:ask`.

---

## `context`

Triage card for files, modules or symbols: title, summary, architectural layer,
hotspot and bug-fix history, doc freshness, and the shape of the verified
skeleton. Relationships and risk signals, not source bytes. Batch targets in one
call.

```bash
repowise context <TARGETS...> [OPTIONS]
```

TARGETS are file paths, module paths, or `path/to/file.py::Symbol` ids.

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--include` | choice | — | Opt-in block, repeatable: `full_doc`, `ownership`, `last_change`, `callers`, `callees`, `metrics`, `community`, `decisions`, `health`, `skeleton` |
| `--no-compact` | flag | false | Add structure, imports and docstrings to each card |
| `--path` | string | cwd | Repo (or workspace) root |
| `--repo` | string | — | Workspace repo alias to query |
| `--no-workspace` | flag | false | Force single-repo mode even inside a workspace |
| `--format` | choice | table | `table` or `json` |
| `--full` | flag | false | Emit the raw MCP tool payload |

### Examples

```bash
repowise context src/api/routes.py src/api/auth.py
repowise context src/api/routes.py::login --include callers --include metrics
repowise context src/api/routes.py --include skeleton   # + the file's source shape
```

Pass `--include skeleton` for the whole file body-elided and line-verified, or
use [`symbol`](#symbol) for one function body. Also available as `/repowise:context`.

---

## `symbol`

Read one function, class or constant with live-verified line bounds. `source`
arrives in the same line-numbered format a file read produces; `verified: true`
means the bounds were checked against the live file.

```bash
repowise symbol <SYMBOL_ID> [OPTIONS]
```

`SYMBOL_ID` is `path/to/file.py::Name` (as `repowise context` reports it),
`path/to/file.py:140-180` for a live range read, or a `repowise#<hex>`
omission ref from a distilled command.

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--context-lines` | int | 0 | Extra lines before and after the body (0–50) |
| `--query` | string | — | Omission refs only: regex or substring filter on restored lines |
| `--path` | string | cwd | Repo (or workspace) root |
| `--repo` | string | — | Workspace repo alias to query |
| `--no-workspace` | flag | false | Force single-repo mode even inside a workspace |
| `--format` | choice | table | `table` or `json` |
| `--full` | flag | false | Emit the raw MCP tool payload |

### Examples

```bash
repowise symbol "src/api/routes.py::login"
repowise symbol "src/api/routes.py:140-180"     # live range read
repowise symbol "repowise#a1b2c3d4e5f6"          # a distill omission ref
```

An ambiguous id returns every matching body rather than silently picking one.
A truncated body carries a `continuation` you can pass straight back.
Also available as `/repowise:symbol`.

---

## `why`

Why the code is shaped this way: decision records, rationale and git
archaeology. Worth running before a refactor or a deliberate divergence from a
pattern.

```bash
repowise why [QUERY] [OPTIONS]
```

QUERY is a question (`why is auth using JWT?`), a file path (its governing
decisions, origin story and alignment score), or omitted for the
decision-health dashboard.

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--target` | string | — | File path to anchor the search to (repeatable) |
| `--path` | string | cwd | Repo (or workspace) root |
| `--repo` | string | — | Workspace repo alias to query |
| `--no-workspace` | flag | false | Force single-repo mode even inside a workspace |
| `--format` | choice | table | `table` or `json` |
| `--full` | flag | false | Emit the raw MCP tool payload |

### Examples

```bash
repowise why "why is auth using JWT?"           # question
repowise why src/api/auth.py                     # governing decisions + origin story
repowise why "why the retry cap?" --target src/api/client.py
repowise why                                     # decision health dashboard
```

Falls back to git archaeology when a path has no decisions, so it is never
empty. Also available as `/repowise:why`. Use `repowise decision` /
`/repowise:decision` to manage the records themselves.

---

## `reindex`

Rebuild the vector search index from existing wiki pages. Does not make LLM calls.

```bash
repowise reindex [PATH] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--embedder` | choice | auto | `gemini`, `openai`, or `auto` |
| `--batch-size` | int | 20 | Pages per embedding batch |

Use this after switching embedding providers, or if the LanceDB index is corrupted.

---

## `status`

Show the current sync state, page counts, and provider info.

```bash
repowise status [PATH]
```

Output includes:
- Last indexed commit and timestamp
- Total pages, symbols, and decisions
- Provider and model in use
- Total tokens consumed
- Index freshness (pages marked stale)

---

## `doctor`

Run health checks on the wiki setup.

```bash
repowise doctor [PATH]
```

Checks:
- Database connectivity and schema version
- Vector store consistency (pages without embeddings)
- Stale pages that need regeneration
- Missing or broken `.mcp.json` config
- API key availability for configured provider

---

## `dead-code`

Detect and report unused code in the indexed codebase.

```bash
repowise dead-code [PATH] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--min-confidence` | float | 0.5 | Minimum confidence threshold (0.0–1.0) |
| `--safe-only` | flag | false | Only show findings marked `safe_to_delete` |
| `--kind` | choice | — | Filter by type: `unreachable_file`, `unused_export`, `unused_internal`, `zombie_package` |
| `--format` | choice | table | Output format: `table`, `json`, or `md` |

### Examples

```bash
repowise dead-code                          # All findings, table format
repowise dead-code --safe-only              # Only confirmed safe to delete
repowise dead-code --kind unused_export     # Only unused exports
repowise dead-code --format json            # Machine-readable output
repowise dead-code --min-confidence 0.8     # High confidence only
```

---

## `health`

Compute per-file code-health scores from deterministic markers. No LLM by
default.

```bash
repowise health [PATH] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--file` | string | — | Deep-dive a single file (relative path) |
| `--module` | string | — | Restrict to files whose path starts with this prefix |
| `--refactoring-targets` | flag | false | Ranked refactoring candidates by impact/effort |
| `--trend` | flag | false | Last health snapshots + declining alerts |
| `--badge` | flag | false | Ready-to-paste health badge Markdown |
| `--format` | choice | table | `table`, `json`, or `md` |
| `--generate-code` | string | — | Opt-in LLM refactoring patch for one target (needs a provider) |

### Examples

```bash
repowise health
repowise health --refactoring-targets
repowise health --file packages/server/app.py
repowise health --trend
```

---

## `risk`

Rank a *change* (commit or `base..head` range) for review. The repo-relative
percentile/classification is authoritative; the supporting 0-10 score measures
diff size and spread and is not a probability. No LLM.

```bash
repowise risk [REVSPEC] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--path` | path | cwd | Git repository path |
| `--ext` | string | all | Comma-separated suffixes to count (e.g. `.py,.ts`) |
| `-x, --exclude` | pattern | — | Gitignore-style exclude (repeatable) |
| `--format` | choice | table | `table` or `json` |

### Examples

```bash
repowise risk                 # score uncommitted work, else HEAD
repowise risk HEAD            # score the last commit
repowise risk main..HEAD      # score a branch / PR range
repowise risk --ext .ts,.tsx
```

---

## `security`

Security signal scanning. Working-tree scanning already runs during `init` /
`update`. Use this group to walk **full git history** for leaked secrets.

```bash
repowise security scan --history [OPTIONS]
```

Without `--history`, the command prints a hint and exits (it does not re-run
the working-tree scan).

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--history` | flag | false | Required for a real scan: walk full git history |
| `--since` | rev | all | Lower bound (exclusive) |
| `--to` | rev | HEAD | Upper bound (inclusive) |
| `--path` | string | cwd | Repo path |
| `--all-patterns` | flag | false | Also report code-smell patterns (default: secrets only) |
| `--output` | choice | table | `table` or `json` |

### Examples

```bash
repowise security scan --history
repowise security scan --history --since v1.0.0 --output json
```

---

## `coverage`

Ingest and inspect test-coverage reports (LCOV, Cobertura/Clover, coverage.py).

```bash
repowise coverage SUBCOMMAND [OPTIONS]
```

### Subcommands

| Subcommand | Description |
|-----------|-------------|
| `add [PATHS...]` | Ingest reports (auto-discovers when none given); builds per-test map when contexts are present |
| `status` | Show ingested coverage + test-to-code map counts |

### Examples

```bash
repowise coverage add
repowise coverage add coverage.lcov
repowise coverage add .coverage
repowise coverage status
```

---

## `impacted-tests`

Print the tests whose coverage intersects a change's changed lines. Requires a
per-test map from `coverage add`.

```bash
repowise impacted-tests [REVSPEC] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--path` | string | cwd | Repo path |
| `--staged` | flag | when no revspec | Diff staged changes |
| `--format` | choice | table | `table`, `json`, or `list` (pipeable test ids) |

### Examples

```bash
repowise impacted-tests
repowise impacted-tests main..HEAD
repowise impacted-tests main..HEAD --format list | xargs pytest
```

---

## `workspace`

Manage multi-repo workspaces.

```bash
repowise workspace SUBCOMMAND [OPTIONS]
```

### Subcommands

| Subcommand | Description |
|-----------|-------------|
| `add` | Add a repo and (by default) index it |
| `list` | Show workspace repos and status |
| `remove` | Remove a repo from the workspace |
| `scan` | Find new repos under the workspace root |
| `set-default` | Change the primary repo |
| `check` | Architecture lint (dependency rules / cycles) |
| `metrics` | Architecture metrics |
| `diagnostics` | Explain cross-repo contract link counts |

---

## `distill`

Run a command and print a compact, reversible rendering of its output.

```bash
repowise distill <command>...
```

### Examples

```bash
repowise distill pytest -x
repowise distill git status
repowise distill npm run build
```

---

## `decision`

Manage architectural decision records (ADRs).

```bash
repowise decision SUBCOMMAND [OPTIONS]
```

### Subcommands

| Subcommand | Description |
|-----------|-------------|
| `add` | Interactively add a new decision |
| `list [PATH]` | List all decisions |
| `show <ID>` | Display full decision details |
| `confirm <ID>` | Mark a proposed decision as active |
| `dismiss <ID>` | Delete a proposed decision |
| `deprecate <ID>` | Mark a decision as deprecated |
| `health [PATH]` | Show decision health metrics |

### `list` options

| Flag | Type | Description |
|------|------|-------------|
| `--status` | choice | Filter by status: `proposed`, `active`, `deprecated`, `superseded`, `all` |
| `--source` | choice | Filter by origin: `adr`, `cli`, `comment`, `commit`, `git_archaeology`, `inline_marker`, `llm_inferred`, `pr`, `session`, `all` |
| `--proposed` | flag | Show only proposed decisions |
| `--stale-only` | flag | Show only decisions with staleness score ≥ 0.5 |

### Examples

```bash
repowise decision list
repowise decision list --status proposed
repowise decision show d-42
repowise decision confirm d-42
repowise decision deprecate d-17 --superseded-by d-42
```

---

## `generate-claude-md`

Generate or update the `.claude/CLAUDE.md` file for Claude Code context.

```bash
repowise generate-claude-md [PATH] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--output` | string | `.claude/CLAUDE.md` | Custom output path |
| `--stdout` | flag | false | Print to stdout instead of writing a file |

### Example

```bash
repowise generate-claude-md          # Update .claude/CLAUDE.md in place
repowise generate-claude-md --stdout # Preview without writing
```

See [CLAUDE.md Generator →](claude-md-generator) for how the file is structured.

---

## `export`

Export wiki pages to files, or emit a Structurizr DSL architecture model.

```bash
repowise export [PATH] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--format` | choice | markdown | `markdown`, `html`, `json`, or `structurizr` |
| `--output` / `-o` | string | `.repowise/export` | Output directory (for structurizr, a path ending in `.dsl` names the file) |
| `--full` | flag | false | JSON: include tombstones plus decisions, dead code, and hotspot metadata |
| `--standalone` | flag | false | Structurizr: emit a complete workspace with default views |
| `--components` | flag | false | Structurizr: include the component level (one box per directory) |
| `--force` | flag | false | Structurizr: overwrite the output file even if Repowise did not write it |
| `--no-externals` | flag | false | Structurizr: leave third-party dependencies out of the model |

### Examples

```bash
repowise export                          # Export all pages as markdown
repowise export --format html -o ./site  # HTML export to ./site
repowise export --format json            # Machine-readable JSON dump
repowise export --format json --full     # Archival JSON with decisions / dead code
repowise export --format structurizr --standalone --components
repowise export --format structurizr -o architecture.dsl
```

Also available as the Claude Code slash command `/repowise:export`.

---

## Configuration

Settings are saved to `.repowise/config.yaml` after the first `init`. You can edit this file directly or pass flags to override settings per run.

```yaml
provider: anthropic
model: claude-sonnet-4-6
embedder: gemini
max_tokens: 16384
exclude_patterns:
  - vendor/
  - "*.generated.*"
commit_limit: 500
follow_renames: false
```

`max_tokens` is the persistent per-page documentation output limit used by all
CLI and server generation paths.

See [Configuration →](configuration) for the full reference.
