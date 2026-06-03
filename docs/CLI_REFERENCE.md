# CLI Reference

Complete reference for all `repowise` commands. For a guided introduction, see the [Quickstart](QUICKSTART.md).

## Workspace auto-detect (cross-cutting)

Most commands auto-detect whether you're in a workspace root and route accordingly. When auto-detection fires, the command prints a one-line `[workspace] …` notice. You can always override:

| Flag | Effect |
|------|--------|
| `--workspace` / `-w` | Force workspace mode. Errors if no `.repowise-workspace.yaml` is found. |
| `--no-workspace` | Force single-repo mode even when invoked from a workspace root. |
| `--repo <alias>` | Scope a workspace command to one repo. Available on commands where it makes sense. |
| `--all` | Fan out across every workspace repo (on `costs`, `search`). |

The commands that grew these flags in v0.8.x: `update`, `status`, `watch`, `doctor`, `costs`, `search`, `dead-code`, `decision`, `generate-claude-md`, `hook install/status/uninstall`.

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
4. **Persistence** — stores everything in `.repowise/wiki.db`, builds search indexes, generates editor instruction files, registers MCP server and hooks

In workspace mode, adds: repo scanning, per-repo indexing, cross-repo analysis (co-changes, contracts, package deps), workspace CLAUDE.md generation.

**Options:**

| Flag | Description |
|------|-------------|
| `--provider` | LLM provider: `anthropic`, `openai`, `openrouter`, `gemini`, `deepseek`, `ollama`, `litellm`, `codex_cli`, `mock` |
| `--model` | Model name override (e.g., `claude-sonnet-4-6`) |
| `--embedder` | Embedder for semantic search: `gemini`, `openai`, `mock` |
| `--index-only` | Skip LLM generation. Only parse, build graph, and index git. Free. |
| `--mode` | Pipeline depth: `standard` (default) or `fast` (graph + essential-git only — no per-file blame/co-change, no LLM — for very large repos; upgrade later with `update --full`). |
| `--dry-run` | Show generation plan and cost estimate without running. |
| `--test-run` | Generate docs for only the top 10 files (by PageRank). |
| `--skip-tests` | Exclude test files from doc generation. |
| `--skip-infra` | Exclude infrastructure files (Dockerfiles, Makefiles, Terraform). |
| `--exclude / -x` | Gitignore-style exclusion patterns. Repeatable. |
| `--include-submodules` | Include git submodule directories. |
| `--concurrency` | Max concurrent LLM calls (default: 5). |
| `--reasoning` | Reasoning mode for supported providers: `auto`, `off`/`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max` (default: `auto`). |
| `--resume` | Resume from the last checkpoint if interrupted. |
| `--force` | Regenerate all pages even if they exist. |
| `--commit-limit` | Max commits to analyze per file (default: 500). |
| `--follow-renames` | Track file renames in git history. |
| `--no-claude-md` | Don't generate `CLAUDE.md`. |
| `--agents / --no-agents` | Generate or skip managed `AGENTS.md` for Codex. Persists the preference. |
| `--codex / --no-codex` | Generate or skip project-local Codex MCP/hooks setup. Interactive runs prompt when Codex CLI is installed and logged in; non-interactive runs require `--codex`. |
| `--yes / -y` | Skip confirmation prompts. |

**Examples:**

```bash
repowise init                                         # interactive
repowise init --provider anthropic --yes              # automated
repowise init --provider codex_cli --codex --yes       # use authenticated Codex CLI
repowise init --index-only                            # free, no LLM
repowise init --dry-run                               # preview cost
repowise init --test-run                              # quick test (10 files)
repowise init --provider openai --model qwen3 --reasoning off
repowise init --provider openrouter --model openai/gpt-5 --reasoning minimal
repowise init -x vendor/ -x "*.gen.go"               # exclude patterns
repowise init --include-submodules                    # include submodules
repowise init --no-codex --no-agents                  # skip Codex project files
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
| `--reasoning` | Reasoning mode for supported providers: `auto`, `off`/`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max` |
| `--cascade-budget` | Max pages to regenerate (default: auto) |
| `--dry-run` | Show what would be updated without regenerating |
| `--workspace` | Update all stale repos in the workspace + cross-repo analysis |
| `--no-workspace` | Force single-repo mode (handy when running from a workspace root) |
| `--repo` | Update a specific workspace repo by alias |
| `--full` | Upgrade a fast (`--mode fast`) index to a full one — see below. Single-repo only. |
| `--agents / --no-agents` | Generate or skip managed `AGENTS.md` after update. Persists the preference. |

**First-time indexing:** as of v0.8, `update --workspace` now runs full first-time indexing for workspace entries that have no `.repowise/` dir yet (previously skipped with `"not_indexed"`). The pipeline runs index-only — no LLM cost — and writes a state.json marker so `repowise update --repo <alias> --docs` later picks up doc generation cleanly.

**Upgrading a fast index to full (`--full`):** a repo first indexed with `repowise init --mode fast` has the full dependency graph + metrics persisted, but only the *essential* git tier (last commits, no per-file blame or co-change) and no LLM docs. `repowise update --full` upgrades it **incrementally**: it backfills the git tier to FULL (per-file blame + repo-wide co-change) using a resumable, checkpointed worker, then generates the docs that fast mode skipped. Crucially, it **reuses the persisted graph** — the dependency graph is rehydrated from SQL rather than re-parsed and re-resolved, so the expensive import/call/heritage resolution and centrality computation the fast index already did are not repeated. This is measurably cheaper than re-running a full `init`. The backfill is resumable: if it is interrupted, re-running `repowise update --full` picks it up. A provider is required (the fast index made no LLM calls), so pass `--provider`/`--model` or have one configured.

**Examples:**

```bash
repowise update                        # diff since last sync
repowise update --dry-run              # preview
repowise update --since v1.0.0         # diff from a tag
repowise update --reasoning off        # one-off supported-provider thinking-off run
repowise update --workspace            # all workspace repos (incl. first-time indexing)
repowise update --repo backend         # specific workspace repo
repowise update --no-workspace         # force single-repo mode in a workspace root
repowise update --full --provider anthropic   # upgrade a fast index to full
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
| `--refresh-ui` | Force re-download of the web UI tarball, ignoring any cache |

```bash
repowise serve                           # API + Web UI
repowise serve --no-ui                   # API only
repowise serve --port 8080 --ui-port 8081
repowise serve --refresh-ui              # bypass cache, pull latest UI tarball
```

**Web UI sources, in order of precedence:**

1. **Local monorepo build** at `packages/web/.next/standalone/...` — used when the CLI is run from inside a checkout. The bundle's mtime is compared against source under `packages/web/`, `packages/ui/src/`, and `packages/types/src/`; if any source is newer the bundle is rebuilt with `npm run build` (or skipped if `npm` is unavailable).
2. **Cached download** at `~/.repowise/web/`, keyed by the CLI version in `.version`.
3. **Fresh download** of `repowise-web.tar.gz` from the GitHub release matching the CLI version.

Pass `--refresh-ui` to skip (1) and (2) and force (3).

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
| `--no-workspace` | Force single-repo mode |
| `--repo` | Watch a single workspace repo by alias |

```bash
repowise watch                           # single repo (auto-detects)
repowise watch --debounce 5000           # 5s debounce
repowise watch --workspace               # all workspace repos
repowise watch --repo backend            # just one
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
| `--repo` | Scope to a specific workspace repo by alias |
| `--all` | Fan out across every workspace repo and merge results |
| `--workspace` / `--no-workspace` | Force workspace / single-repo mode |

```bash
repowise search "rate limiting"
repowise search "how are errors handled" --mode semantic
repowise search "AuthService" --mode symbol
repowise search "rate limit" --repo backend     # workspace, one repo
repowise search "rate limit" --all              # workspace, fan-out
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
repowise status                          # auto-detects mode
repowise status --workspace              # all workspace repos
repowise status --no-workspace           # force single-repo even in a workspace
```

In workspace mode, the table includes a **Docs** column with each repo's page count and a per-repo **Docs status** block listing skip reasons (e.g. `cost gate declined`) and the exact remediation command.

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
| `--repo` | In workspace mode, target a specific repo (defaults to primary) |
| `--workspace` / `--no-workspace` | Force workspace / single-repo mode |

```bash
repowise dead-code
repowise dead-code --safe-only --min-confidence 0.8
repowise dead-code --format json
repowise dead-code --repo backend        # workspace, single repo
repowise dead-code resolve <id>          # mark resolved / false positive
```

---

### `repowise risk [REVSPEC]`

Just-in-time change-risk scoring for a commit or diff range. Scores the defect
risk of a change (0–10) from the same calibrated signals the code-health layer
uses — no LLM calls. `REVSPEC` defaults to `HEAD`; pass a `base..head` range to
score a whole branch / PR as one change.

**Options:**

| Flag | Description |
|------|-------------|
| `--path` | Path to the git repository (default: current directory) |
| `--ext` | Comma-separated file suffixes to count (e.g. `.py` or `.ts,.tsx`) |
| `--format` | Output format: `table` (default) or `json` |

```bash
repowise risk                 # score HEAD
repowise risk main..HEAD      # score a branch / PR range as one change
repowise risk --ext .ts,.tsx  # restrict to specific suffixes
```

See [`docs/CHANGE_RISK.md`](./CHANGE_RISK.md) for the scoring model.

---

### `repowise health [PATH]`

Compute per-file code-health scores from 25 deterministic biomarkers (McCabe complexity, nesting, brain methods, LCOM4 cohesion, god classes, native clone detection, untested hotspots, coverage gradient, function/ownership/churn/change-entropy organizational risk, test-quality smells, and more). Zero LLM calls — pure Python over tree-sitter + git data. See [`docs/CODE_HEALTH.md`](./CODE_HEALTH.md) for the user guide and [`docs/architecture/code-health.md`](./architecture/code-health.md) for the internals.

**Options:**

| Flag | Description |
|------|-------------|
| `--file <path>` | Deep-dive a single file (relative path) |
| `--module <prefix>` | Restrict the report to files whose path starts with this prefix |
| `--refactoring-targets` | Print top refactoring candidates ranked by impact / effort |
| `--trend` | Print the last 10 health snapshots + any active alerts (declining / predicted decline) |
| `--coverage <path>` | Ingest a coverage report (LCOV / Cobertura / Clover). Repeat for multiple files |
| `--coverage-format` | Override coverage-format auto-detection: `lcov`, `cobertura`, `clover` |
| `--format` | Output: `table` (default), `json`, `md` |
| `--safe-only` | Confidence ≥ 0.8 only (placeholder for v1 biomarkers) |
| `--repo` | In workspace mode, target a specific repo (defaults to primary) |
| `--no-workspace` | Force single-repo mode |

```bash
repowise health                                       # KPIs + lowest-scoring files
repowise health --file packages/server/.../app.py     # one file in detail
repowise health --module packages/server              # restrict to a directory
repowise health --refactoring-targets                 # ranked by impact / effort
repowise health --trend                               # snapshot history + alerts
repowise health --coverage coverage.lcov              # ingest coverage
repowise health --format json | jq .kpis              # machine-readable
```

`repowise init` and `repowise update` populate the health tables automatically —
no separate command needed. `repowise status` shows a one-line summary
(`Health: 7.4 (avg) · 6.2 (hotspots) · 2.1 (worst: <path>)`).

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

| Flag | Description |
|------|-------------|
| `--by` | Grouping: `operation`, `model`, `day` |
| `--repo` | Scope to a specific workspace repo |
| `--all` | Aggregate across every workspace repo |
| `--workspace` / `--no-workspace` | Force workspace / single-repo mode |

```bash
repowise costs                           # auto-detects mode
repowise costs --by operation            # grouped by operation
repowise costs --by model                # grouped by model
repowise costs --by day                  # grouped by day
repowise costs --all                     # workspace-wide aggregate
repowise costs --repo backend            # one workspace repo
```

---

## Workspace Commands

### `repowise workspace list`

Show all repos in the workspace with their index status.

### `repowise workspace add <path>`

Add a new repo to an existing workspace and index it.

**As of v0.8 this defaults to `--index --docs`** when a provider is configured — the added repo is indexed and gets LLM doc generation in one step, with a cost-gate prompt before any tokens are spent. Pass `--no-docs` to skip generation, or `--no-index` to only register the entry. The provider, model, embedder, and exclude patterns are inherited from the primary repo's `.repowise/config.yaml` unless overridden.

| Flag | Description |
|------|-------------|
| `--alias` | Short name for the repo (defaults to directory name) |
| `--index` / `--no-index` | Run the index pipeline (default: on) |
| `--docs` / `--no-docs` | Run LLM doc generation (default: on when a provider is configured) |
| `--provider` / `--model` | Override the inherited provider/model |
| `--primary` | Mark this repo as the workspace default |

```bash
repowise workspace add ../new-service --alias api-gateway
repowise workspace add ../mobile --no-docs            # index, no LLM
repowise workspace add ../shared --no-index           # register only
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

If `PATH` is omitted, `repowise mcp` first walks upward from the current directory to the nearest initialized `.repowise` repository. This lets project-local Codex config use `args = ["mcp"]` with `cwd` set to the repo root.

**Options:**

| Flag | Description |
|------|-------------|
| `--transport` | `stdio` (default, for editors) or `sse` (for web clients) |
| `--port` | Port for SSE transport (default: 7338) |

```bash
repowise mcp --transport stdio           # for Claude Code, Codex, Cursor, etc.
repowise mcp --transport sse --port 7338 # for web clients
```

See [MCP Tools](MCP_TOOLS.md) for all 9 exposed tools.

---

### `repowise generate-claude-md [PATH]`

Generate or update `CLAUDE.md` with codebase intelligence. Custom instructions at the top are preserved.

```bash
repowise generate-claude-md
repowise generate-claude-md -o custom-path.md
repowise generate-claude-md --stdout
```

---

### `AGENTS.md`

`repowise init --codex` generates managed `AGENTS.md` for Codex. `repowise update` refreshes it when `editor_files.agents_md` is enabled in config, or when `--agents` is passed. User content outside the Repowise managed markers is preserved.

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

Run health checks on the wiki setup. Auto-detects workspace mode; in workspace mode runs a workspace-level table (directory exists, git repo, state.json ↔ workspace config drift) followed by the per-repo check battery for every indexed entry.

| Flag | Description |
|------|-------------|
| `--repair` | Repair detected issues: rebuild FTS, re-embed missing pages, sync drifted workspace state, drop dead workspace entries |
| `--workspace` / `--no-workspace` | Force workspace / single-repo mode |

```bash
repowise doctor                          # auto-detects
repowise doctor --repair                 # fix detected store mismatches
repowise doctor --workspace              # every workspace repo
repowise doctor --workspace --repair     # also drop dead entries / sync drift
```

**CLI update check.** `doctor` also prints a best-effort `CLI version` row that
compares your installed CLI against the latest release on PyPI and, when an
update is available, shows the suggested upgrade command (e.g. `uv tool upgrade
repowise`, `pipx upgrade repowise`, or `python -m pip install -U repowise`). It
shows both the `repowise` resolved on your `PATH` and the command that launched
the current process, since these can differ. This check is advisory: it never
updates anything automatically and does not fail `doctor` when PyPI is
unreachable. After upgrading, **restart Claude/Codex/Cursor or any MCP client**
so it picks up the new executable. (A standalone `repowise version --check` may
be added later.)

---

### `repowise delete [REPO_ID]`

Delete a repository's index and all stored intelligence (wiki, graph, embeddings,
git metadata). Does **not** touch your source files. Prompts for confirmation
unless `--force` is passed.

| Flag | Description |
|------|-------------|
| `--force` / `-f` | Skip the confirmation prompt |
| `--path` / `-p` | Path to the repository directory |

```bash
repowise delete                          # delete the current repo's index (prompts)
repowise delete <repo-id> --force        # delete a specific repo's index, no prompt
```

---

### `repowise augment`

Hook-driven context enrichment engine. Not meant to be called manually — invoked by Claude Code and Codex hooks installed during `repowise init`. Claude Code uses it for search-result enrichment and stale-wiki checks; Codex uses it for `SessionStart`, `UserPromptSubmit`, and `PostToolUse` lifecycle guidance.
