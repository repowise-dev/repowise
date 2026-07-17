# CLI Reference

Complete reference for all `repowise` commands. For a guided introduction, see the [Quickstart](QUICKSTART.md).

Command list (in `--help` order): `augment`, `init`, `delete`, `generate-claude-md`, `costs`, `update`, `dead-code`, `health`, `risk`, `decision`, `coverage`, `impacted-tests`, `search`, `distill`, `expand`, `saved`, `corrections`, `export`, `hook`, `status`, `doctor`, `watch`, `serve`, `mcp`, `reindex`, `restyle`, `wiki-styles`, `whats-new`, `telemetry`, `login`, `logout`, `whoami`, `workspace`. Two more ship as separate console scripts, not subcommands: `repowise-augment`, `repowise-rewrite` (both hook entry points, not meant to be run by hand).

**Do you need an LLM key?** Most commands are pure index/analysis and never call an LLM. The exceptions: `init` (unless `--index-only` or `--mode fast`), `update` (unless `--index-only` or `--no-docs`), `restyle`, `watch` (when it regenerates a page), `health --generate-code`, and `workspace add --docs`. Everything else, `search`, `dead-code`, `health`, `risk`, `impacted-tests`, `decision`, `coverage`, `export`, `mcp`, `reindex`, `doctor`, and so on, works index-only, with no provider configured.

## Workspace auto-detect (cross-cutting)

Most commands auto-detect whether you're in a workspace root and route accordingly. When auto-detection fires, the command prints a one-line `[workspace] …` notice. You can always override:

| Flag | Effect |
|------|--------|
| `--workspace` / `-w` | Force workspace mode. Errors if no `.repowise-workspace.yaml` is found. |
| `--no-workspace` | Force single-repo mode even when invoked from a workspace root. |
| `--repo <alias>` | Scope a workspace command to one repo. Available on commands where it makes sense. |
| `--all` | Fan out across every workspace repo (on `costs`, `search`). |

The commands that grew these flags: `update`, `status`, `watch`, `doctor`, `costs`, `search`, `dead-code`, `decision`, `coverage`, `generate-claude-md`, `hook install/status/uninstall`.

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

1. **Ingestion**, walks every file, parses AST with tree-sitter, builds a two-tier dependency graph (file + symbol nodes), indexes git history (churn, hotspots, ownership, bus factor)
2. **Analysis**, detects dead code, extracts architectural decisions from inline markers, READMEs, and git history. Runs Leiden community detection and execution flow tracing.
3. **Generation**, sends structured prompts to the LLM, generates file-level, module-level, and repo-level wiki pages
4. **Persistence**, stores everything in `.repowise/wiki.db`, builds search indexes, generates editor instruction files, registers MCP server and hooks

In workspace mode, adds: repo scanning, per-repo indexing, cross-repo analysis (co-changes, contracts, package deps), workspace CLAUDE.md generation.

**Interactive modes.** Running a bare `repowise init` on a TTY (no `--provider`, `--index-only`, or `--yes`) opens a menu:

1. **Everything**, index + AI docs. After picking a provider you can answer **"Customize?"** to tune any setting before the run.
2. **Index only**, graph, git, code health, dead code; no LLM, no cost. Answer **"Customize indexing?"** to set exclude patterns, commit limit, skip-tests/infra, submodules, and fast mode.
3. **Advanced**, full control. First choose **"Generate AI docs?"**; the prompts then split into an **Indexing** section (always) and a **Generation** section (provider, concurrency, embedder, wiki style, onboarding, decision harvesting, tiering, only when docs are on).

All three reach the indexing knobs; the LLM-only knobs appear only when docs are enabled. Passing any of the flags below (or `--yes`) skips the menu and runs non-interactively.

**Options:**

| Flag | Description |
|------|-------------|
| `--provider` | LLM provider: `anthropic`, `openai`, `openrouter`, `gemini`, `deepseek`, `kimi`, `ollama`, `litellm`, `codex_cli`, `opencode`, `mock` |
| `--model` | Model name override (e.g., `claude-sonnet-4-6`) |
| `--embedder` | Embedder for semantic search: `gemini`, `openai`, `openrouter`, `ollama`, `mock` (default: auto-detect) |
| `--index-only` | Skip LLM generation. Only parse, build graph, and index git. Free. |
| `--mode` | Pipeline depth: `standard` (default) or `fast` (graph + essential-git only, no per-file blame/co-change, no LLM, for very large repos; upgrade later with `update --full`) |
| `--skip-tests` | Exclude test files from doc generation |
| `--skip-infra` | Exclude infrastructure files (Dockerfiles, Makefiles, Terraform) |
| `--exclude` / `-x` | Gitignore-style exclusion pattern. Repeatable. |
| `--include-submodules` | Include git submodule directories (excluded by default) |
| `--concurrency` | Max concurrent LLM calls (default: 10) |
| `--reasoning` | Reasoning mode for supported providers: `auto`, `off`/`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` (default: `auto`) |
| `--coverage` | Documentation coverage as a fraction of repo files (e.g. `0.10`, `0.20`, `0.50`). Bypasses the interactive coverage chooser. |
| `--coverage-report` | Test-coverage report to ingest (LCOV / Cobertura / Clover). Repeatable. Auto-discovered when omitted. Distinct from `--coverage`, which controls documentation breadth. |
| `--onboarding` / `--no-onboarding` | Generate the curated Onboarding collection (up to 8 overview pages). Default: on; slots without enough signal are skipped. |
| `--harvest-decisions` / `--no-harvest-decisions` | Harvest architectural decisions during page generation (verified against source before storage). Default: on. |
| `--wiki-style` | Documentation voice/density: `comprehensive` (default), `caveman` (token-condensed, AI-first), `reference` (API-manual), `tutorial` (beginner-friendly). Interactive full runs prompt when omitted. Saved to config so `update` keeps the style. See [WIKI_STYLES.md](WIKI_STYLES.md). |
| `--language` | Output language for generated wiki pages: `en` (default), `ar`, `de`, `es`, `fr`, `hi`, `it`, `ja`, `ko`, `nl`, `pl`, `pt`, `ru`, `tr`, `zh`. Code, file paths, and symbol names stay untranslated. Saved to config so `update` keeps the language. Also asked in advanced interactive mode. To switch an existing wiki's language, set the flag and re-run `init --force`. |
| `--resume` | Resume from the last checkpoint if interrupted |
| `--force` | Regenerate all pages even if they exist |
| `--commit-limit` | Max commits to analyze per file (default: 500, capped at 10000) |
| `--follow-renames` | Track file renames in git history |
| `--no-claude-md` | Don't generate `CLAUDE.md` |
| `--agents` / `--no-agents` | Generate or skip managed `AGENTS.md` for Codex. Persists the preference. |
| `--codex` / `--no-codex` | Generate or skip project-local Codex MCP/hooks setup. Interactive runs prompt when Codex CLI is installed and logged in; non-interactive runs require `--codex`. |
| `--distill-hook` / `--no-distill-hook` | Install or skip the Distill command-rewrite hook (Claude Code PreToolUse). Strictly opt-in: interactive runs prompt (default No); `--no-distill-hook` also gates the repo off in config so a globally installed hook stays inert here. In workspace mode the verdict applies to every selected repo. See [DISTILL.md](DISTILL.md). |
| `--seed-from` | Seed the index from an explicit base checkout instead of the auto-detected one. Rarely needed: inside a linked git worktree the base is detected and seeded automatically. See [WORKTREES.md](WORKTREES.md). |
| `--no-seed` | Disable worktree auto-seeding and run a full init even inside a linked worktree. |
| `--yes` / `-y` | Skip confirmation prompts |
| `--dry-run` | Show generation plan and cost estimate without running |
| `--test-run` | Generate docs for only the top 10 files (by PageRank) |
| `--all` | In multi-repo mode, index every detected repo without prompting |
| `--no-workspace` | Force single-repo mode even when invoked from a workspace root (indexes only the target PATH instead of fanning out across workspace repos) |

**Examples:**

```bash
repowise init                                         # interactive
repowise init --provider anthropic --yes              # automated
repowise init --provider codex_cli --codex --yes       # use authenticated Codex CLI
repowise init --provider opencode --yes               # use local OpenCode CLI
repowise init --index-only                            # free, no LLM
repowise init --dry-run                               # preview cost
repowise init --test-run                              # quick test (10 files)
repowise init --provider openai --model qwen3 --reasoning off
repowise init --provider openrouter --model openai/gpt-5 --reasoning minimal
repowise init --language zh                           # wiki docs in Chinese
repowise init -x vendor/ -x "*.gen.go"               # exclude patterns
repowise init --include-submodules                    # include submodules
repowise init --no-codex --no-agents                  # skip Codex project files
repowise init .                                       # workspace mode
repowise init . --index-only -x "node_modules/"      # workspace, no LLM
repowise init . --no-workspace                        # force single-repo, even in a workspace root
```

---

### `repowise update [PATH]`

Incrementally refresh the index for files changed since the last sync: the
dependency graph, git metadata, health and dead-code findings, and, when the
graph shape changed, the knowledge graph (layers, guided tour, entry points)
plus the exported `knowledge-graph.json`. In docs mode it also regenerates the
affected wiki pages. Index-only updates carry forward the previously generated
layer names and node summaries, so no LLM call is ever made without docs mode.

Docs-mode updates (and `init`) also mine local coding-agent session
transcripts for durable decisions: user corrections, explicit choices with a
stated reason, and failed approaches replaced by working ones. Candidates
pass deterministic gates and a verbatim-quote grounding check; a decision
observed in two or more sessions (or one direct user correction) is promoted
into the decision records with `source: session`. Everything stays on your
machine. Disable with `decisions.session_mining: false` in
`.repowise/config.yaml` (see [CONFIG.md](CONFIG.md)).

If any best-effort step fails (git metadata, decisions, dead code, ...), the
run still exits 0 but lists the degraded steps in the completion panel (and in
the `done` event's `degraded` array with `--progress json`); the next update
retries them. In docs mode each regenerated page is persisted as it completes,
so an interrupted run never pays for the finished pages again, the rerun's
prompt-hash check skips them.

Inside an unindexed linked git worktree, `update` first seeds the index from
the base checkout automatically, then proceeds with the incremental update.
See [WORKTREES.md](WORKTREES.md).

**Options:**

| Flag | Description |
|------|-------------|
| `--provider` | Override LLM provider for this run |
| `--model` | Override model |
| `--since` | Git ref to diff from (overrides `state.json`) |
| `--reasoning` | Reasoning mode for supported providers: `auto`, `off`/`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max` |
| `--cascade-budget` | Max pages to regenerate (default: auto) |
| `--dry-run` | Show what would be updated without regenerating |
| `--workspace` / `-w` | Update all stale repos in the workspace + cross-repo analysis |
| `--no-workspace` | Force single-repo mode (handy when running from a workspace root) |
| `--repo` | Update a specific workspace repo by alias |
| `--index-only` | Refresh the index only, skip doc regeneration for this run |
| `--docs` / `--no-docs` | Regenerate wiki pages for changed files, or skip doc regeneration entirely |
| `--full` | Upgrade a fast (`--mode fast`) index to a full one, see below. Single-repo only; errors in workspace mode. |
| `--no-cost-tracking` | Don't record LLM spend for this run |
| `--agents` / `--no-agents` | Generate or skip managed `AGENTS.md` after update. Persists the preference. |
| `-v`, `--verbose` | Show the full changed-file list and per-phase internals (cascade budget, decision-marker/evolution counts, best-effort skip warnings, detailed generation report). Off by default for a compact summary. |
| `--progress` | `rich` (default) for the interactive progress bar, or `json` for newline-delimited JSON events on stdout (for driving update from another process) |

**First-time indexing:** `update --workspace` runs full first-time indexing for workspace entries that have no `.repowise/` dir yet (previously skipped with `"not_indexed"`). The pipeline runs index-only, no LLM cost, and writes a state.json marker so `repowise update --repo <alias> --docs` later picks up doc generation cleanly.

**Upgrading a fast index to full (`--full`):** a repo first indexed with `repowise init --mode fast` has the full dependency graph + metrics persisted, but only the *essential* git tier (last commits, no per-file blame or co-change) and no LLM docs. `repowise update --full` upgrades it **incrementally**: it backfills the git tier to FULL (per-file blame + repo-wide co-change) using a resumable, checkpointed worker, then generates the docs that fast mode skipped. Crucially, it **reuses the persisted graph**, the dependency graph is rehydrated from SQL rather than re-parsed and re-resolved, so the expensive import/call/heritage resolution and centrality computation the fast index already did are not repeated. This is measurably cheaper than re-running a full `init`. The backfill is resumable: if it is interrupted, re-running `repowise update --full` picks it up. A provider is required (the fast index made no LLM calls), so pass `--provider`/`--model` or have one configured. Single-repo only; it errors if run in workspace mode.

**Examples:**

```bash
repowise update                        # diff since last sync
repowise update --dry-run              # preview
repowise update --since v1.0.0         # diff from a tag
repowise update --reasoning off        # one-off supported-provider thinking-off run
repowise update --workspace            # all workspace repos (incl. first-time indexing)
repowise update --repo backend         # specific workspace repo
repowise update --no-workspace         # force single-repo mode in a workspace root
repowise update -v                     # verbose: full file list + per-phase internals
repowise update --full --provider anthropic   # upgrade a fast index to full
```

---

### `repowise restyle [STYLE] [PATH]`

Switch a repo's wiki **style** and regenerate every page in the new voice. Reuses
the existing index, the dependency graph and git metadata are rehydrated from
SQL (no re-resolution, no re-blame), so only the per-file parse + LLM generation
run. Requires a full (docs-enabled) index and a provider; fails on index-only repos.

With no `STYLE`, prints the current style and the available choices.

Styles only differ in voice and density; the markdown structure (headings,
sections) stays the same, so search, the table of contents, and cross-links keep
working. See [WIKI_STYLES.md](WIKI_STYLES.md).

**Options:**

| Flag | Description |
|------|-------------|
| `--provider` | Override LLM provider for this run |
| `--model` | Override model |
| `--concurrency` | Max concurrent LLM calls (default: 12) |
| `--reasoning` | Reasoning mode for supported providers |
| `--yes` / `-y` | Skip the confirmation prompt |

```bash
repowise restyle                       # show current style + options
repowise restyle caveman               # condensed, AI-first
repowise restyle reference --yes       # API-manual, skip the confirm
```

> Editing `wiki_style` in `config.yaml` by hand and running `update` does **not**
> regenerate existing pages (that path only re-scores health). Use `restyle`.

---

### `repowise wiki-styles [PATH]`

List the available wiki styles (built-ins plus any custom styles defined under
`.repowise/styles/`) and the repo's current one.

```bash
repowise wiki-styles
```

---

### `repowise serve [PATH]`

Start the API server and web UI.

**Options:**

| Flag | Description |
|------|-------------|
| `--port` | API server port (default: 7337; env `REPOWISE_PORT`) |
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

The web UI needs Node >= 20; without it `serve` falls back to API-only.

**Web UI sources, in order of precedence:**

1. **Local monorepo build** at `packages/web/.next/standalone/...`, used when the CLI is run from inside a checkout. The bundle's mtime is compared against source under `packages/web/`, `packages/ui/src/`, and `packages/types/src/`; if any source is newer the bundle is rebuilt with `npm run build` (or skipped if `npm` is unavailable).
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
| `--workspace` / `-w` | Watch all workspace repos |
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

For question answering and synthesized explanations (not keyword lookup), use
the MCP `get_answer` tool from your editor, or the **Chat** tab in the web UI
(`repowise serve`), there is no dedicated CLI command for this.

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
| `--include-internals` / `--no-include-internals` | Include private/underscore symbols (default: off) |
| `--include-zombie-packages` / `--no-include-zombie-packages` | Include unused declared packages (default: on) |
| `--no-unreachable` | Skip unreachable-file findings |
| `--no-unused-exports` | Skip unused-export findings |
| `--repo` | In workspace mode, target a specific repo (defaults to primary) |
| `--workspace` / `--no-workspace` | Force workspace / single-repo mode |

```bash
repowise dead-code
repowise dead-code --safe-only --min-confidence 0.8
repowise dead-code --format json
repowise dead-code --repo backend        # workspace, single repo
```

---

### `repowise risk [REVSPEC]`

Just-in-time change-risk scoring for a commit or diff range. Scores the defect
risk of a change from the same calibrated signals the code-health layer uses -
no LLM calls, and it works without `repowise init` (pure git + learned
constants). `REVSPEC` defaults to `HEAD`; pass a `base..head` range to score a
whole branch / PR as one change.

The headline is **repo-relative**: the change's percentile and review priority
(`Below typical` / `Typical` / `Elevated`) within the repo's own recent commits,
sampled live. The raw 0–10 model score is still shown, but as a secondary,
corpus-anchored number (it skews high on repos whose typical commit is large, so
the percentile is the signal to act on). Each risk driver is reported relative
to the model's baseline commit, not this repo.

**Options:**

| Flag | Description |
|------|-------------|
| `--path` | Path to the git repository (default: current directory) |
| `--ext` | Comma-separated file suffixes to count (e.g. `.py` or `.ts,.tsx`) |
| `--exclude` / `-x` | Gitignore-style path pattern to omit. Repeatable; filters both the change and baseline. Root `.riskignore` patterns also apply. |
| `--baseline` | Recent commits to sample for the repo-relative percentile (default 200; `0` shows only the absolute calibrated band) |
| `--format` | Output format: `table` (default) or `json` |

```bash
repowise risk                 # score HEAD
repowise risk main..HEAD      # score a branch / PR range as one change
repowise risk --ext .ts,.tsx  # restrict to specific suffixes
repowise risk main..HEAD -x 'tests/' -x '*.spec.ts'  # omit tests from scoring
```

See [`docs/CHANGE_RISK.md`](./CHANGE_RISK.md) for the scoring model.

---

### `repowise impacted-tests [REVSPEC]`

Print the tests a change actually exercises, so CI can run "these 40 tests, not
all 4,000". For each changed line it consults the per-test test-to-code map
built by [`repowise coverage add`](#repowise-coverage) and returns the tests
whose recorded coverage intersects the diff. No LLM, no network - a straight
index lookup.

`REVSPEC` is a `base..head` range or a single commit; with no argument (or
`--staged`) it diffs the staged changes. It is honest about what it does not
know, and always says which path fired:

- a changed file with per-test coverage -> the exact covering tests (`via: coverage`);
- a changed file with no coverage rows -> a filename-pattern **guess** at its paired test, labelled as a guess (never presented as coverage-backed);
- a new file with neither coverage nor a paired test -> reported as "unknown, run the full suite" (never implied as "no tests needed");
- no map ingested at all -> a prompt to run `repowise coverage add` on a report with contexts first.

The map only exists when coverage was ingested from a report that carries
per-test contexts (a coverage.py `.coverage` written with dynamic contexts, or a
per-test lcov). Score the same `head` the map was ingested at so line numbers
line up.

**Options:**

| Flag | Description |
|------|-------------|
| `--path` | Repo path (defaults to cwd / workspace primary) |
| `--staged` | Diff the staged changes (`git diff --cached`); the default when no range is given |
| `--format` | `table` (default), `json` (full report), or `list` (test ids one per line, for piping) |

```bash
repowise impacted-tests                        # staged changes
repowise impacted-tests main..HEAD             # a branch / PR range
repowise impacted-tests abc123                 # a single commit
repowise impacted-tests main..HEAD --format list | xargs pytest
```

---

### `repowise health [PATH]`

Compute per-file code-health scores from 25 deterministic markers (McCabe complexity, nesting, brain methods, LCOM4 cohesion, god classes, native clone detection, untested hotspots, coverage gradient, function/ownership/churn/change-entropy organizational risk, test-quality smells, and more). Zero LLM calls by default, pure Python over tree-sitter + git data. See [`docs/CODE_HEALTH.md`](./CODE_HEALTH.md) for the user guide and [`docs/architecture/code-health.md`](./architecture/code-health.md) for the internals.

**Options:**

| Flag | Description |
|------|-------------|
| `--file <path>` | Deep-dive a single file (relative path) |
| `--module <prefix>` | Restrict the report to files whose path starts with this prefix |
| `--refactoring-targets` | Print structured, graph-aware refactoring plans (Extract Class / Helper / Move Method / Break Cycle), ranked `impact × centrality × blast radius`. See [REFACTORING.md](REFACTORING.md) |
| `--generate-code <selector>` | Generate an actual refactoring patch for one target. The only `health` flag that calls an LLM; needs a configured provider. |
| `--trend` | Print the last 10 health snapshots + any active alerts (declining / predicted decline) |
| `--badge` | Print a shields.io-compatible badge URL/JSON for the repo's health score |
| `--format` | Output: `table` (default), `json`, `md` |
| `--safe-only` | Documented no-op placeholder for a future confidence filter; has no effect today |
| `--repo` | In workspace mode, target a specific repo (defaults to primary) |
| `--no-workspace` | Force single-repo mode |

```bash
repowise health                                       # KPIs + lowest-scoring files
repowise health --file packages/server/.../app.py     # one file in detail
repowise health --module packages/server              # restrict to a directory
repowise health --refactoring-targets                 # ranked by impact / effort
repowise health --generate-code packages/server/app.py::handler   # LLM patch for one target
repowise health --trend                               # snapshot history + alerts
repowise coverage add coverage.lcov   # ingest coverage, then:
repowise health
repowise health --format json | jq .kpis              # machine-readable
```

`repowise init` and `repowise update` populate the health tables automatically -
no separate command needed. `repowise status` shows a one-line summary
(`Health: 7.4 (avg) · 6.2 (hotspots) · 2.1 (worst: <path>)`).
Health automatically folds in whatever coverage was already ingested via `repowise coverage add`, no flag needed.

---

### `repowise decision`

Manage architectural decision records.

**Subcommands:**

```bash
repowise decision list [PATH]           # list decisions
repowise decision show ID [PATH]        # full details
repowise decision add [PATH]            # interactive add
repowise decision confirm ID [PATH]     # confirm a proposal
repowise decision dismiss ID [PATH]     # dismiss a proposal (sticky; never re-proposed)
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

### `repowise coverage`

Ingest and inspect test-coverage reports. Coverage is auto-discovered and
ingested during `init` / `update`; this group is the manual path, point it at
a report (or let it auto-discover one) to populate per-file line/branch
coverage, which clears `untested_hotspot` findings for files that are tested
regardless of where their tests live.

**Subcommands:**

```bash
repowise coverage add [PATHS...]        # ingest coverage reports (+ per-test map when contexts are present)
repowise coverage status                # show ingested coverage + the map
```

**`add` options:**

| Flag | Description |
|------|-------------|
| `--path` | Repo path (defaults to cwd / workspace primary) |
| `--format` | Force a parser instead of auto-detecting: `lcov`, `cobertura`, `clover`, `repowise-json` |

`add` ingests per-file line/branch coverage from LCOV, Cobertura, Clover, or a
coverage.py `.coverage` file. It auto-discovers `coverage/lcov.info`,
`.coverage`, and similar reports at the repo root when no path is given, and
merges multiple reports (hit wins). When the report carries per-test contexts,
a coverage.py `.coverage` written with `coverage run --contexts=test`, or a
per-test lcov, `add` also builds the per-test *test-to-code map*, which test
covers which source lines. A report without contexts still ingests the
per-file coverage; it just skips the map.

```bash
repowise coverage add                       # discover coverage/lcov.info, .coverage, etc.
repowise coverage add coverage/lcov.info
repowise coverage add web.lcov api.lcov     # merged, hit wins
coverage run --contexts=test -m pytest      # produce .coverage with contexts
repowise coverage add .coverage             # per-file coverage + per-test map
repowise coverage status                    # coverage summary + "Test-to-code map" counts
```

> The per-test map is a separate dimension from the per-file aggregate that
> `add` always stores (a file is covered, merged over all tests) and from what
> `health` reads. It's used to answer "which tests exercise this change".

---

### `repowise distill <command>`

Run a command and print a compact, reversible rendering of its output. Noise
(pass parades, progress spam, boilerplate) is dropped; errors, failures, and
summaries always survive; the command's exit code is preserved. Dropped
content is stored in `.repowise/omissions/` and referenced by an inline
`[repowise#<ref>: ...]` marker. On any filter problem the raw output is
printed unchanged. See [DISTILL.md](DISTILL.md) for the full feature guide.

```bash
repowise distill pytest -x
repowise distill git status
repowise distill npm run build
```

Honors the `distill:` block in `.repowise/config.yaml` (master switch,
disabled filters, omission-store sizing).

---

### `repowise expand REF`

Restore the original output behind a `[repowise#<ref>: ...]` omission marker.
Accepts a bare 12-hex ref or a pasted whole marker. Looks in the current
repo's store first, then the user-level fallback store.

| Flag | Description |
|------|-------------|
| `--query / -q` | Return only the lines matching this regex (or substring) |

```bash
repowise expand a1b2c3d4e5f6
repowise expand a1b2c3d4e5f6 -q "FAILED"
```

---

### `repowise saved [PATH]`

Report tokens (and estimated dollars) saved by `repowise distill`, direct
invocations and hook rewrites. Covers the distill command/hook path only; MCP
response truncation is not part of this ledger.

| Flag | Description |
|------|-------------|
| `--by` | Grouping: `filter` (default), `day`, `source` |
| `--since` | Only count savings since this ISO date |
| `--model` | Pricing model for the dollar estimate (input-token rate; default `claude-sonnet-4-6`) |
| `--missed` | Report commands that looked distillable but weren't rewritten |
| `--missed-days` | Window in days for `--missed` (default 7.0) |

```bash
repowise saved                       # per-filter rollup + totals
repowise saved --by day              # daily rollup
repowise saved --since 2026-06-01
repowise saved --missed              # what's slipping past the hook
```

---

### `repowise corrections [PATH]`

Mine local agent transcripts for recurring command fumbles, consecutive runs
of the same base command where the first failed and a later variant succeeded
(wrong tool, wrong path, unknown flag, missing argument). Report-only by
default; entirely local. See [DISTILL.md](DISTILL.md#repowise-corrections--recurring-command-fumbles).

| Flag | Description |
|------|-------------|
| `--days` | Transcript window for the scan (default 30) |
| `--write` | Maintain the "Known command corrections" managed block in `.claude/CLAUDE.md` / `AGENTS.md` (opt-in) |
| `--min-count` | Occurrences a rule needs before `--write` includes it (default 2) |

```bash
repowise corrections                 # report recurring fumbles
repowise corrections --days 60
repowise corrections --write         # seed the agent guidance block
```

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

## Workspace Commands

### `repowise workspace list`

Show all repos in the workspace with their index status.

### `repowise workspace add <path>`

Add a new repo to an existing workspace and index it.

This defaults to `--index --docs` when a provider is configured, the added repo is indexed and gets LLM doc generation in one step, with a cost-gate prompt before any tokens are spent. Pass `--no-docs` to skip generation, or `--no-index` to only register the entry. The provider, model, embedder, and exclude patterns are inherited from the primary repo's `.repowise/config.yaml` unless overridden.

| Flag | Description |
|------|-------------|
| `--alias` | Short name for the repo (defaults to directory name) |
| `--index` / `--no-index` | Run the index pipeline (default: on) |
| `--docs` / `--no-docs` | Run LLM doc generation (default: on when a provider is configured) |
| `--provider` / `--model` | Override the inherited provider/model |
| `--concurrency` | Max concurrent LLM calls for this repo's generation |
| `--primary` | Mark this repo as the workspace default |

```bash
repowise workspace add ../new-service --alias api-gateway
repowise workspace add ../mobile --no-docs            # index, no LLM
repowise workspace add ../shared --no-index           # register only
```

### `repowise workspace remove <alias>`

Remove a repo from the workspace (does not delete files).

### `repowise workspace scan [PATH]`

Re-scan the workspace directory for new repos not yet added.

| Flag | Description |
|------|-------------|
| `--yes` / `-y` | Auto-add all discovered repos without prompting |

```bash
repowise workspace scan
repowise workspace scan --yes
```

### `repowise workspace set-default <alias>`

Change which repo is the default for MCP queries.

### `repowise workspace diagnostics`

Explain the cross-repo contract link count: per-repo provider/consumer counts, unmatched consumers grouped by reason, and orphan providers (declared but never consumed).

| Flag | Description |
|------|-------------|
| `--repo` | Limit the report to one repo alias |
| `--json` | Emit raw diagnostics JSON |

```bash
repowise workspace diagnostics            # human-readable report
repowise workspace diagnostics --json     # raw JSON
repowise workspace diagnostics --repo api # limit to one repo alias
```

### `repowise workspace check`

Architecture lint: check the declared `conformance:` rules against the system graph and detect dependency cycles. Exits non-zero on any finding, so it gates CI.

| Flag | Description |
|------|-------------|
| `--json` | Emit the raw conformance report as JSON |

```bash
repowise workspace check                  # human-readable report; exit 1 on findings
repowise workspace check --json           # raw report JSON
```

### `repowise workspace metrics [PATH]`

Architecture-complexity metrics over the system graph built by `repowise update --workspace`: propagation cost (how coupled the whole system is), the cyclic core (which services form circular dependency groups), and a single deterministic 1-10 score. Uses structural edges only (co-change is excluded); declared conformance violations, if any, are folded into the score. Requires a system graph, run `repowise update --workspace` first.

| Flag | Description |
|------|-------------|
| `--json` | Emit the raw metrics as JSON |

```bash
repowise workspace metrics
repowise workspace metrics --json
```

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

### `repowise hook rewrite install|uninstall|status`

Manage the Distill command-rewrite hooks (Claude Code + Codex PreToolUse).
When installed, noisy agent commands (tests, builds, git status/log/diff,
searches, listings) are rewritten to `repowise distill <command>`, pending
your approval by default, so the agent sees a compact, errors-first
rendering.

```bash
repowise hook rewrite install        # writes ~/.claude/settings.json (idempotent)
repowise hook rewrite install -w     # also re-enable every workspace repo
repowise hook rewrite status
repowise hook rewrite uninstall      # removes only the repowise entries
```

`install` also re-enables the target's `distill.commands` config if a prior
`repowise init` opt-out had gated it off, the target repo by default, or
every workspace repo with `--workspace`/`-w` (accepts an optional `PATH` and
`--no-workspace`, like `repowise hook install`). `uninstall` removes the
global hook entries plus the repo's AGENTS.md awareness section and leaves
per-repo config untouched. Per-repo posture (`permission: ask | allow`,
per-family overrides) lives under `distill.commands` in
`.repowise/config.yaml`, see [DISTILL.md](DISTILL.md#configuration).

When `~/.codex` exists, `install` also writes a Codex hook entry to
`~/.codex/hooks.json` (Codex ≥ 0.137 only, older builds can't apply a
rewrite) and maintains an "Output Distillation" section in the repo's
`AGENTS.md` that works without any hook. Codex cannot show a rewritten
command for approval, so there rewrites fire only for families set to
`permission: allow`; `status` reports exactly what your build supports. See
[DISTILL.md](DISTILL.md#3-the-command-rewrite-hook-claude-code--codex).

---

## Utility Commands

### `repowise mcp [PATH]`

Start the MCP server for AI editor integration.

If `PATH` is omitted, `repowise mcp` first walks upward from the current directory to the nearest initialized `.repowise` repository. This lets project-local Codex config use `args = ["mcp"]` with `cwd` set to the repo root.

**Options:**

| Flag | Description |
|------|-------------|
| `--transport` | `stdio` (default, for editors), `streamable-http` (for HTTP clients), or `sse` (legacy) |
| `--port` | Port for HTTP/SSE transports (default: 7338) |
| `--tools` | Override which tools are exposed. A comma-separated list is an explicit allowlist; prefix names with `+`/`-` to adjust the default set (e.g. `+get_dependency_path,-get_dead_code`); `lean` selects the six-tool agent-lean profile. Overrides the `mcp.tools` config block. |
| `--all` | Expose every available tool, including opt-in and workspace tools |

```bash
repowise mcp --transport stdio           # for Claude Code, Codex, Cursor, etc.
repowise mcp --transport streamable-http # for HTTP clients
repowise mcp --transport sse --port 7338 # legacy SSE
repowise mcp --tools "+get_dependency_path,-get_dead_code"
repowise mcp --tools lean
repowise mcp --all
```

See [MCP Tools](MCP_TOOLS.md) for all exposed tools.

---

### `repowise generate-claude-md [PATH]`

Generate or update `CLAUDE.md` with codebase intelligence. Custom instructions above the Repowise markers are preserved; the managed section between markers is auto-updated from the index.

**Options:**

| Flag | Description |
|------|-------------|
| `--output` | Write to a custom path (default: `.claude/CLAUDE.md`) |
| `--stdout` | Print generated content to stdout instead of writing a file |
| `--workspace` / `-w` | Force workspace mode: generates a workspace-level `CLAUDE.md` at the workspace root with cross-repo contracts, co-changes, and per-repo summaries |
| `--no-workspace` | Force single-repo mode even when invoked from a workspace |

Auto-detects workspace mode when invoked from a workspace root.

```bash
repowise generate-claude-md
repowise generate-claude-md -o custom-path.md
repowise generate-claude-md --stdout
repowise generate-claude-md --workspace    # workspace-level CLAUDE.md
```

`repowise init` and `repowise update` keep it current automatically; you rarely need to run this directly.

---

### `AGENTS.md`

`repowise init --codex` generates managed `AGENTS.md` for Codex. `repowise update` refreshes it when `editor_files.agents_md` is enabled in config, or when `--agents` is passed. User content outside the Repowise managed markers is preserved.

---

### `repowise reindex [PATH]`

Rebuild the vector search index by re-embedding all wiki pages. No LLM calls, only embedding API calls.

**Options:**

| Flag | Description |
|------|-------------|
| `--embedder` | `gemini`, `openai`, `openrouter`, `ollama`, `mock`, or `auto` (default: auto) |
| `--batch-size` | Embedding batch size (default: 32) |

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
| `--workspace` / `-w` | Force workspace mode |
| `--no-workspace` | Force single-repo mode |
| `--format` | Output: `table` (default) or `json` |

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
so it picks up the new executable.

**Distill checks.** `doctor` also validates the `distill:` config block
(unknown keys, bad permission values, unknown filter names, non-positive store
sizing), reports the omission store's size against its configured cap, and
shows whether the command-rewrite hook is installed. The hook is opt-in, so
its absence never fails doctor.

---

### `repowise whats-new`

Show release notes for repowise versions you haven't seen yet. By default it
lists releases newer than the last one you viewed, then records the current
version as seen. Works offline from the changelog bundled with the install.

| Flag | Description |
|------|-------------|
| `--version X.Y.Z` | Show notes for a single release |
| `--all` | Show the full changelog history |

```bash
repowise whats-new                       # what changed since you last looked
repowise whats-new --version 0.21.0      # one specific release
repowise whats-new --all                 # full history
```

`repowise update` shows a short "what's new" panel automatically after you
upgrade to a newer version, and both `update` and `serve` print a one-line,
non-blocking notice when a newer release is available on PyPI. See
[docs/UPGRADING.md](UPGRADING.md) for the full upgrade flow.

---

### `repowise telemetry`

Inspect and control anonymous, opt-out usage telemetry.

```bash
repowise telemetry status                # show whether telemetry is enabled, and why
repowise telemetry enable
repowise telemetry disable
```

---

### `repowise login`

Sign in to your hosted repowise.dev account. This is unrelated to LLM provider
keys (`--provider`/`--model` elsewhere), it adds the hosted layer: your
indexed repos on repowise.dev, reindex from local tools, and account status in
`doctor`. Every local feature works without signing in.

Sign-in is browser-based OAuth with PKCE by default: the command opens the
hosted consent page and stores tokens at `~/.repowise/credentials.json`.

| Flag | Description |
|------|-------------|
| `--with-token` | Paste a personal API key (`rw_live_...`) instead of using the browser. Reads from stdin when piped, otherwise prompts. For SSH/headless machines. |
| `--device-name` | Label this machine in your connected apps (default: hostname) |

```bash
repowise login                           # browser sign-in
repowise login --with-token              # headless, paste an API key
repowise login --device-name "build-box"
```

### `repowise logout`

Sign out of your Repowise account on this machine (best-effort server-side revocation, local credentials always removed).

```bash
repowise logout
```

### `repowise whoami`

Show the Repowise account this machine is signed in to.

```bash
repowise whoami
```

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

Hook-driven context enrichment engine. Not meant to be called manually, invoked by Claude Code and Codex hooks installed during `repowise init`. Claude Code uses it for search-result enrichment, stale-wiki checks, and decision injection: session start gets the standing decisions relevant to the session's working set (relevance-ranked, hard token cap, silent when nothing clears the floor), and editing a governed file gets a one-line "governed by" notice once per session per decision. Codex uses it for `SessionStart`, `UserPromptSubmit`, and `PostToolUse` lifecycle guidance. Shown decisions are recorded in `.repowise/sessions/sessions.db` so the next `repowise update` can judge whether the guidance was followed or contradicted and adjust decision staleness.

### `repowise-augment` / `repowise-rewrite`

Two separate console scripts (not `repowise` subcommands) installed alongside the CLI: `repowise-augment` is an import-isolated entry point for the Claude Code/Codex augment hooks above; `repowise-rewrite` backs the Distill command-rewrite hook (`repowise hook rewrite install`). Neither is meant to be run by hand.
