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
| `--provider` | string | auto | LLM provider: `anthropic`, `openai`, `gemini`, `ollama`, `litellm`, `mock` |
| `--model` | string | — | Model override (e.g., `claude-sonnet-4-6`, `gpt-4.1`) |
| `--embedder` | choice | auto | Embedding provider: `gemini`, `openai`, `mock` |
| `--index-only` | flag | false | Skip LLM generation — parse, graph, git, dead code only |
| `--dry-run` | flag | false | Show generation plan and token estimate without running |
| `--test-run` | flag | false | Limit to top 10 files by PageRank (for validation) |
| `--skip-tests` | flag | false | Exclude test files |
| `--skip-infra` | flag | false | Exclude Dockerfiles, Makefiles, Terraform, shell scripts |
| `--exclude` / `-x` | string | — | Gitignore-style exclude pattern (repeatable: `-x vendor/ -x "*.gen.*"`) |
| `--concurrency` | int | 5 | Max concurrent LLM calls |
| `--resume` | flag | false | Resume from last checkpoint after an interruption |
| `--force` | flag | false | Regenerate all pages even if up to date |
| `--commit-limit` | int | 500 | Max commits per file for git analysis (max 5000, saved to config) |
| `--follow-renames` | flag | false | Track file renames in git history (saved to config) |
| `--no-claude-md` | flag | false | Skip generating `.claude/CLAUDE.md` |
| `--yes` / `-y` | flag | false | Skip the cost confirmation prompt |

### Examples

```bash
# Interactive setup (recommended for first run)
repowise init

# Non-interactive, specific provider
repowise init --provider anthropic --yes

# Analysis only — free, no API key needed
repowise init --index-only

# Skip tests and infra, limit concurrency
repowise init --skip-tests --skip-infra --concurrency 3

# Exclude generated files and vendor directories
repowise init -x "*.generated.ts" -x vendor/ -x proto/

# Dry run to estimate cost before committing
repowise init --dry-run

# Resume an interrupted run
repowise init --resume
```

### What it does

1. **Ingestion** — parses all files with tree-sitter, builds dependency graph
2. **Analysis** — git churn/ownership, dead code detection, decision mining
3. **Generation** — LLM writes wiki pages at repo/module/file level (skipped with `--index-only`)
4. **Persistence** — writes to SQLite, builds vector index, generates `.claude/CLAUDE.md`

### Provider auto-detection

If `--provider` is not specified, repowise checks in order:
1. `REPOWISE_PROVIDER` environment variable
2. `.repowise/config.yaml` from a previous run
3. API key environment variables: `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `OLLAMA_BASE_URL` → `GEMINI_API_KEY`

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
| `--cascade-budget` | int | auto | Max pages to regenerate from cascading changes |
| `--dry-run` | flag | false | Show affected pages without regenerating |

### Examples

```bash
repowise update                      # Sync since last indexed commit
repowise update --since HEAD~10      # Re-sync last 10 commits
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
| `--transport` | choice | stdio | `stdio` (for editors) or `sse` (for web clients) |
| `--port` | int | 7338 | Port for SSE transport |

### Examples

```bash
repowise mcp                              # stdio (Claude Code, Cursor, Cline)
repowise mcp --transport sse --port 7338  # SSE for web clients
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
| `--min-confidence` | float | 0.4 | Minimum confidence threshold (0.0–1.0) |
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
| `--source` | choice | Filter by origin: `git_archaeology`, `inline_marker`, `readme_mining`, `cli`, `all` |
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

Export wiki pages to files.

```bash
repowise export [PATH] [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--format` / `-f` | choice | markdown | `markdown`, `html`, or `json` |
| `--output` / `-o` | string | `.repowise/export` | Output directory |

### Examples

```bash
repowise export                          # Export all pages as markdown
repowise export --format html -o ./site  # HTML export to ./site
repowise export --format json            # Machine-readable JSON dump
```

---

## Configuration

Settings are saved to `.repowise/config.yaml` after the first `init`. You can edit this file directly or pass flags to override settings per run.

```yaml
provider: anthropic
model: claude-sonnet-4-6
embedder: gemini
exclude_patterns:
  - vendor/
  - "*.generated.*"
commit_limit: 500
follow_renames: false
```

See [Configuration →](configuration) for the full reference.
