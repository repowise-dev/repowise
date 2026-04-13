# CLI Reference

Complete reference for all `repowise` commands. For a guided introduction, see the [Quickstart](QUICKSTART.md).

---

## Core Commands

### `repowise init [PATH]`

Index a codebase and generate wiki documentation. This is the starting point.

**Single repo:**

```bash
cd your-project
repowise init
```

**Multi-repo workspace:**

```bash
cd my-workspace/     # parent dir containing multiple git repos
repowise init .
```

**What it does (4 phases):**

1. **Ingestion** — walks every file, parses AST with tree-sitter, builds a two-tier dependency graph (file + symbol nodes), indexes git history (churn, hotspots, ownership, bus factor)
2. **Analysis** — detects dead code, extracts architectural decisions from inline markers, READMEs, and git history. Runs Leiden community detection and execution flow tracing.
3. **Generation** — sends structured prompts to the LLM, generates file-level, module-level, and repo-level wiki pages
4. **Persistence** — stores everything in `.repowise/wiki.db`, builds search indexes, generates `CLAUDE.md`, registers MCP server and Claude Code hooks

In workspace mode, adds: repo scanning, per-repo indexing, cross-repo analysis (co-changes, contracts, package deps), workspace CLAUDE.md generation.

**Options:**

| Flag | Description |
|------|-------------|
| `--provider` | LLM provider: `anthropic`, `openai`, `gemini`, `ollama`, `mock` |
| `--model` | Model name override (e.g., `claude-sonnet-4-6`) |
| `--embedder` | Embedder for semantic search: `gemini`, `openai`, `mock` |
| `--index-only` | Skip LLM generation. Only parse, build graph, and index git. Free. |
| `--dry-run` | Show generation plan and cost estimate without running. |
| `--test-run` | Generate docs for only the top 10 files (by PageRank). |
| `--skip-tests` | Exclude test files from doc generation. |
| `--skip-infra` | Exclude infrastructure files (Dockerfiles, Makefiles, Terraform). |
| `--exclude / -x` | Gitignore-style exclusion patterns. Repeatable. |
| `--include-submodules` | Include git submodule directories. |
| `--concurrency` | Max concurrent LLM calls (default: 5). |
| `--resume` | Resume from the last checkpoint if interrupted. |
| `--force` | Regenerate all pages even if they exist. |
| `--commit-limit` | Max commits to analyze per file (default: 500). |
| `--follow-renames` | Track file renames in git history. |
| `--no-claude-md` | Don't generate `CLAUDE.md`. |
| `--yes / -y` | Skip confirmation prompts. |

**Examples:**

```bash
repowise init                                         # interactive
repowise init --provider anthropic --yes              # automated
repowise init --index-only                            # free, no LLM
repowise init --dry-run                               # preview cost
repowise init --test-run                              # quick test (10 files)
repowise init -x vendor/ -x "*.gen.go"               # exclude patterns
repowise init --include-submodules                    # include submodules
repowise init .                                       # workspace mode
repowise init . --index-only -x "node_modules/"      # workspace, no LLM
```

---

### `repowise update [PATH]`

Incrementally update wiki pages for files changed since the last sync.

**Options:**

| Flag | Description |
|------|-------------|
| `--provider` | Override LLM provider for this run |
| `--model` | Override model |
| `--since` | Git ref to diff from (overrides `state.json`) |
| `--cascade-budget` | Max pages to regenerate (default: auto) |
| `--dry-run` | Show what would be updated without regenerating |
| `--workspace` | Update all stale repos in the workspace + cross-repo analysis |
| `--repo` | Update a specific workspace repo by alias |

**Examples:**

```bash
repowise update                        # diff since last sync
repowise update --dry-run              # preview
repowise update --since v1.0.0         # diff from a tag
repowise update --workspace            # all workspace repos
repowise update --repo backend         # specific workspace repo
```

---

### `repowise serve [PATH]`

Start the API server and web UI.

**Options:**

| Flag | Description |
|------|-------------|
| `--port` | API server port (default: 7337) |
| `--host` | Host to bind to (default: 127.0.0.1) |
| `--workers` | Uvicorn workers (default: 1) |
| `--ui-port` | Web UI port (default: 3000) |
| `--no-ui` | Start API server only |

```bash
repowise serve                           # API + Web UI
repowise serve --no-ui                   # API only
repowise serve --port 8080 --ui-port 8081
```

---

### `repowise watch [PATH]`

Watch for file changes and auto-update wiki pages. Press `Ctrl+C` to stop.

**Options:**

| Flag | Description |
|------|-------------|
| `--provider` | LLM provider |
| `--model` | Model override |
| `--debounce` | Delay in ms after last change (default: 2000) |
| `--workspace` | Watch all workspace repos |

```bash
repowise watch                           # single repo
repowise watch --debounce 5000           # 5s debounce
repowise watch --workspace               # all workspace repos
```

---

## Query Commands

### `repowise search QUERY [PATH]`

Search wiki pages by keyword, meaning, or symbol name.

**Options:**

| Flag | Description |
|------|-------------|
| `--mode` | `fulltext` (default), `semantic`, `symbol` |
| `--limit` | Max results (default: 10) |

```bash
repowise search "rate limiting"
repowise search "how are errors handled" --mode semantic
repowise search "AuthService" --mode symbol
```

---

### `repowise query QUESTION [PATH]`

Ask a question about your codebase from the terminal.

```bash
repowise query "how does authentication work?"
repowise query "what files handle payment processing?"
```

---

### `repowise status [PATH]`

Show wiki sync state, page statistics, and coverage.

```bash
repowise status                          # single repo
repowise status --workspace              # all workspace repos
```

---

## Analysis Commands

### `repowise dead-code [PATH]`

Detect dead and unused code.

**Options:**

| Flag | Description |
|------|-------------|
| `--min-confidence` | Minimum confidence threshold (default: 0.4) |
| `--safe-only` | Only show findings marked safe to delete |
| `--kind` | Filter: `unreachable_file`, `unused_export`, `unused_internal`, `zombie_package` |
| `--format` | Output: `table` (default), `json`, `md` |
| `--include-internals` | Include private/underscore symbols |
| `--include-zombie-packages` | Include unused declared packages |

```bash
repowise dead-code
repowise dead-code --safe-only --min-confidence 0.8
repowise dead-code --format json
repowise dead-code resolve <id>          # mark resolved / false positive
```

---

### `repowise decision`

Manage architectural decision records.

**Subcommands:**

```bash
repowise decision list [PATH]           # list decisions
repowise decision show ID [PATH]        # full details
repowise decision add [PATH]            # interactive add
repowise decision confirm ID [PATH]     # confirm a proposal
repowise decision dismiss ID [PATH]     # delete a proposal
repowise decision deprecate ID [PATH]   # mark deprecated
repowise decision health [PATH]         # health dashboard
```

**List options:**

| Flag | Description |
|------|-------------|
| `--status` | `active`, `proposed`, `deprecated`, `superseded`, `all` |
| `--source` | `git_archaeology`, `inline_marker`, `readme_mining`, `cli`, `all` |
| `--proposed` | Shortcut for `--status proposed` |
| `--stale-only` | Only stale decisions |

---

### `repowise costs`

Show LLM spend tracking.

```bash
repowise costs                           # total spend
repowise costs --by operation            # grouped by operation
repowise costs --by model                # grouped by model
repowise costs --by day                  # grouped by day
```

---

## Workspace Commands

### `repowise workspace list`

Show all repos in the workspace with their index status.

### `repowise workspace add <path>`

Add a new repo to an existing workspace and index it.

```bash
repowise workspace add ../new-service --alias api-gateway
```

### `repowise workspace remove <alias>`

Remove a repo from the workspace (does not delete files).

### `repowise workspace scan`

Re-scan the workspace directory for new repos not yet added.

### `repowise workspace set-default <alias>`

Change which repo is the default for MCP queries.

See [Workspaces](WORKSPACES.md) for the full multi-repo guide.

---

## Auto-Sync Commands

### `repowise hook install`

Install a post-commit git hook that runs `repowise update` in the background after every commit.

```bash
repowise hook install                    # current repo
repowise hook install --workspace        # all workspace repos
```

### `repowise hook status`

Check if hooks are installed.

```bash
repowise hook status
repowise hook status --workspace
```

### `repowise hook uninstall`

Remove the post-commit hook.

```bash
repowise hook uninstall
repowise hook uninstall --workspace
```

See [Auto-Sync](AUTO_SYNC.md) for all sync methods (hooks, file watcher, webhooks, polling).

---

## Utility Commands

### `repowise mcp [PATH]`

Start the MCP server for AI editor integration.

**Options:**

| Flag | Description |
|------|-------------|
| `--transport` | `stdio` (default, for editors) or `sse` (for web clients) |
| `--port` | Port for SSE transport (default: 7338) |

```bash
repowise mcp --transport stdio           # for Claude Code, Cursor, etc.
repowise mcp --transport sse --port 7338 # for web clients
```

See [MCP Tools](MCP_TOOLS.md) for all 7 exposed tools.

---

### `repowise generate-claude-md [PATH]`

Generate or update `CLAUDE.md` with codebase intelligence. Custom instructions at the top are preserved.

```bash
repowise generate-claude-md
repowise generate-claude-md -o custom-path.md
repowise generate-claude-md --stdout
```

---

### `repowise export [PATH]`

Export wiki pages to files.

**Options:**

| Flag | Description |
|------|-------------|
| `--format` | `markdown` (default), `html`, `json` |
| `--output / -o` | Output directory (default: `.repowise/export`) |
| `--full` | Include decisions, dead code, hotspots, provenance metadata (JSON only) |

```bash
repowise export
repowise export --format json --full
repowise export --format html -o ./wiki/
```

---

### `repowise reindex [PATH]`

Rebuild vector search index from existing wiki pages.

```bash
repowise reindex
repowise reindex --embedder gemini --batch-size 50
```

---

### `repowise doctor [PATH]`

Run health checks on the wiki setup.

```bash
repowise doctor
repowise doctor --repair    # fix detected store mismatches
```

---

### `repowise augment`

Hook-driven context enrichment engine. Not meant to be called manually — invoked by Claude Code hooks installed during `repowise init`.
