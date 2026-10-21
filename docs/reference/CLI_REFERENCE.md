# CLI Reference

Every `repowise` command and flag. For a guided introduction, see the [Quickstart](../start/QUICKSTART.md).

Top-level commands (alphabetical): `agents`, `ask`, `attribution`, `augment`, `changes`, `check`, `ci-summary`, `components`,
`config`, `context`, `corrections`, `costs`, `coverage`, `dead-code`, `decision`, `delete`, `distill`, `doc-drift`,
`doctor`, `events`, `expand`, `export`, `generate`, `generate-claude-md`, `health`, `hook`, `impacted-tests`, `init`,
`login`, `logout`, `mcp`, `next`, `overlap`, `packages`, `plugins`, `publish`, `reindex`, `report`, `restyle`, `risk`,
`saved`, `savings`, `search`, `security`, `serve`, `status`, `suspects`, `symbol`, `telemetry`, `uninstall`, `update`,
`watch`, `whats-new`, `whoami`, `why`, `wiki-styles`, `workspace`. `repowise --version` prints the version.
`repowise-augment` and `repowise-rewrite` are separate console scripts (hook entry points, not run by hand).

**Do you need an LLM key?** Usually not; most commands only read the index.

- `init` never needs one: it calls an LLM only with a resolvable provider or `--prose`, else renders from structure.
- LLM callers: `update` (unless `--index-only` or `--no-docs`), `generate`, `restyle`, `watch` (when it regenerates a page), `health --generate-code`, `workspace add --docs`.
- Index-only, no provider: everything else (`search`, `dead-code`, `doc-drift`, `health`, `risk`, `impacted-tests`, `decision`, `coverage`, `security`, `export`, `mcp`, `reindex`, `doctor`, ...).

## Contents

Grouped by task; `PATH` and flags are in each command's section.

- **Indexing:** [`init`](#repowise-init-path), [`update`](#repowise-update-path), [`generate`](#repowise-generate-path),
  [`restyle`](#repowise-restyle-style-path), [`wiki-styles`](#repowise-wiki-styles-path),
  [`watch`](#repowise-watch-path), [`reindex`](#repowise-reindex-path)
- **Querying:** [`search`](#repowise-search-query-path), [`ask`](#repowise-ask-question),
  [`context`](#repowise-context-targets), [`symbol`](#repowise-symbol-symbol_id), [`why`](#repowise-why-query), [shared
  `ask`/`context`/`symbol`/`why` options](#shared-options-ask-context-symbol-why), [`status`](#repowise-status-path),
  [`next`](#repowise-next-path)
- **Health and risk:** [`health`](#repowise-health-path), [`health check`](#repowise-health-check-revspec),
  [`risk`](#repowise-risk-revspec), [`overlap`](#repowise-overlap), [`dead-code`](#repowise-dead-code-path),
  [`doc-drift`](#repowise-doc-drift-path), [`security`](#repowise-security), [`check`](#repowise-check-revspec), [`ci-summary`](#repowise-ci-summary-dir),
  [`impacted-tests`](#repowise-impacted-tests-revspec), [`coverage`](#repowise-coverage),
  [`components`](#repowise-components), [`packages`](#repowise-packages), [`changes`](#repowise-changes),
  [`suspects`](#repowise-suspects), [`events`](#repowise-events), [`report
  assessment`](#repowise-report-assessment-path)
- **Decisions:** [`decision`](#repowise-decision)
- **Agent wiring:** [`agents`](#repowise-agents), [`generate-claude-md`](#repowise-generate-claude-md-path), [`AGENTS.md`](#agentsmd), [`attribution`](#repowise-attribution)
- **Hooks and distill:** [`distill`](#repowise-distill-command), [`expand`](#repowise-expand-ref),
  [`saved`](#repowise-saved-path), [`savings sync`](#repowise-savings-sync-path),
  [`corrections`](#repowise-corrections-path), [`hook install`](#repowise-hook-install), [`hook
  status`](#repowise-hook-status), [`hook uninstall`](#repowise-hook-uninstall), [`hook stats`](#repowise-hook-stats),
  [`hook backfill`](#repowise-hook-backfill), [`hook rewrite`](#repowise-hook-rewrite-installuninstallstatus), [`hook
  read-skeleton|search-digest|read-reread`](#repowise-hook-read-skeletonsearch-digestread-reread),
  [`hook health-gate`](#repowise-hook-health-gate-installuninstallstatus), [`augment`](#repowise-augment)
- **Server:** [`serve`](#repowise-serve-path), [`mcp`](#repowise-mcp-path)
- **Workspace:** [`workspace list`](#repowise-workspace-list), [`workspace add`](#repowise-workspace-add-path),
  [`workspace remove`](#repowise-workspace-remove-alias), [`workspace scan`](#repowise-workspace-scan-path), [`workspace
  set-default`](#repowise-workspace-set-default-alias), [`workspace diagnostics`](#repowise-workspace-diagnostics),
  [`workspace check`](#repowise-workspace-check), [`workspace metrics`](#repowise-workspace-metrics-path), [`workspace
  impacted-tests`](#repowise-workspace-impacted-tests-repopath)
- **Maintenance:** [`doctor`](#repowise-doctor-path), [`whats-new`](#repowise-whats-new),
  [`telemetry`](#repowise-telemetry), [`plugins`](#repowise-plugins), [`config hints`](#repowise-config-hints-onoff),
  [`login`](#repowise-login), [`logout`](#repowise-logout), [`whoami`](#repowise-whoami),
  [`publish`](#repowise-publish-path), [`delete`](#repowise-delete-repo_id), [`uninstall`](#repowise-uninstall-path),
  [`costs`](#repowise-costs), [`export`](#repowise-export-path)

---

## Workspace auto-detect (cross-cutting)

Most commands detect a workspace root and route to it, with a one-line `[workspace] …` notice.

| Flag | What it does | Default |
|------|--------------|---------|
| `--workspace` / `-w` | Force workspace mode; errors without `.repowise-workspace.yaml`. | auto-detect |
| `--no-workspace` | Force single-repo mode. | auto-detect |
| `--repo <alias>` | Scope to one repo, where sensible. | all repos or primary, per command |
| `--all` | Fan out across every repo (`costs`, `search`). | off |

- `--workspace`: `update`, `status`, `watch`, `doctor`, `generate-claude-md`, `hook` (install, status, uninstall,
  rewrite, read-skeleton, search-digest, read-reread), `report assessment`/`inventory`/`changes`.
- `--no-workspace`: those, plus `search`, `dead-code`, `doc-drift`, `costs`, `health`, `next`, `mcp`, `ask`, `context`, `symbol`, `why`.
- `--repo`: `update`, `report`, and the `--no-workspace` extras except `mcp`.

---

## Core Commands

### `repowise init [PATH]`

Index a codebase and generate its wiki; the starting point.

```bash
repowise init                                        # interactive
repowise init --provider codex_cli --codex --yes     # authenticated Codex CLI
repowise init --no-prose -x vendor/ -x "*.gen.go"    # free, wiki from structure
repowise init --provider openai --yes --no-save-key  # CI: key never on disk
repowise init . --yes --repos api,web                # workspace dir, two of its repos
```

**Phases.** (1) Ingestion: tree-sitter ASTs, file and symbol dependency graph, git history (churn, hotspots, ownership,
bus factor). (2) Analysis: dead code, decisions (inline markers, READMEs, git), Leiden communities, execution flows. (3)
Generation: file, module, repo pages (LLM, or structure with `--no-prose`). (4) Persistence: `.repowise/wiki.db`, search
indexes, editor files, MCP server, hooks.

Workspaces add repo scanning, per-repo indexing, cross-repo analysis (co-changes, contracts, package deps), workspace CLAUDE.md.

**Model, content and scope**

| Flag | What it does | Default |
|------|--------------|---------|
| `--provider` | `anthropic`, `openai`, `openrouter`, `gemini`, `deepseek`, `kimi`, `ollama`, `litellm`, `codex_cli`, `copilot_cli`, `claude_cli`, `opencode`, `edenai`, `mock`. Terminals prompt for a missing key (`openai`: plus optional compatible base URL). | from environment |
| `--model` | Model (e.g. `claude-sonnet-4-6`). | provider default |
| `--embedder` | `gemini`, `openai`, `openrouter`, `ollama`, `edenai`, `mock`. | auto-detect |
| `--prose` / `--no-prose` | The one LLM-spend knob; see notes. | prose when a key exists |
| `--index-only` | Deprecated hidden alias of `--no-prose`. | |
| `--docs` | Deprecated hidden alias: `llm` = `--prose`, `deterministic` = `--no-prose`. | |
| `--mode` | `standard`, or `fast` (graph and essential git; no per-file blame, co-change or LLM docs; for very large repos; upgrade with `update --full`). | `standard` |
| `--reasoning` | `auto`, `off`/`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` (supported providers). | `auto` |
| `--concurrency` | Max concurrent LLM calls. | `10` |
| `--wiki-style` | `comprehensive`, `caveman` (token-condensed, AI-first), `reference` (API manual), `tutorial` (beginner). Saved for `update`. [WIKI.md](../layers/WIKI.md#styles). | `comprehensive`; interactive full runs prompt |
| `--language` | `en`, `ar`, `de`, `es`, `fr`, `hi`, `it`, `ja`, `ko`, `nl`, `pl`, `pt`, `ru`, `tr`, `zh`. Saved for `update`. Code, paths, symbols untranslated. Advanced mode asks. Switch: `init --force`. | `en` |
| `--onboarding` / `--no-onboarding` | Onboarding collection (up to 8 pages); see notes. | on |
| `--max-file-pages` | File-page cap by importance; `0`: no cap, a page per eligible file. Saved to `config.yaml`. | size policy (Page volume) |
| `--test-run` | Model docs for top 10 files by PageRank. Not with `--no-prose` or `--mode fast`. | off |
| `--dry-run` | Plan and cost estimate; no wiki. | off |
| `--exclude` / `-x` | Gitignore-style pattern. Repeatable. | |
| `--skip-tests` | No docs for test files. | off |
| `--skip-infra` | No docs for infra (Dockerfiles, Makefiles, Terraform). | off |
| `--include-submodules` | Include git submodules. | excluded |
| `--coverage-report` | LCOV, Cobertura, Clover, JaCoCo, Go coverprofile. Repeatable. Feeds code health only; every code file is documented regardless. | auto-discovered |
| `--commit-limit` | Max commits per file and for co-change; cap 10000. Saved. | `5000` |
| `--follow-renames` | Track renames via `git log --follow` (slower, more accurate). Saved. | off |
| `--resume` | Continue a previous run; see notes. | off |
| `--force` | Regenerate all pages; see notes. | off |
| `--seed-from` | Explicit base checkout to seed from; rarely needed (linked worktrees auto-seed). [WORKTREES.md](../scale/WORKTREES.md). | auto-detected base |
| `--no-seed` | Full init even in a linked worktree. | seeding on |

**Agent wiring and running**

| Flag | What it does | Default |
|------|--------------|---------|
| `--no-claude-md` | Skip `CLAUDE.md`; saves `editor_files.claude_md: false`. | generated |
| `--agents` / `--no-agents` | Managed Codex `AGENTS.md`. Persists. | config, else on |
| `--codex` / `--no-codex` | Project-local Codex MCP and hooks. Required for non-interactive runs. | prompt if Codex CLI is logged in |
| `--distill-hook` / `--no-distill-hook` | Distill hooks (Claude Code PreToolUse) compacting what the agent sees; notes, [DISTILL.md](../agent/DISTILL.md). | off; interactive prompt (default No) |
| `--hook` / `--no-hook` | Post-commit `repowise update` hook; see notes. | on |
| `--editor-setup` / `--no-editor-setup` | Editor wiring; see notes. | on |
| `--save-key` / `--no-save-key` | Save the run's key to `.repowise/.env` (git-ignored, owner-only); see notes. | on |
| `--yes` / `-y` | No plan screen, all defaults; see notes. | off |
| `--all` | Folder of repos: index all (as do `--yes` and piped stdin, printing the repos taken). | picker |
| `--repos` | Folder of repos: only these (`api,web`); see notes. | picker |
| `--no-workspace` | Index only PATH from a workspace root. | auto-detect |
| `--progress` | `rich`: live checklist (TTY), else one line per step (max 100 columns). `json`: JSON event lines on stdout, ending in a summary. | `rich` |
| `--no-cost-tracking` | Skip DB-backed LLM cost tracking. | tracking on |
| `-v`, `--verbose` | Per-phase internals, debug logs (else debug and info logs are suppressed). | off |

**Flag notes**

- **`--prose` / `--no-prose`.** File, symbol, cycle (SCC), API, infra pages: always from structure, free.
  - Model set: subsystem (concept) tree, repo overview, architecture diagram, onboarding. `--prose` writes it (key needed); `--no-prose` leaves stubs.
  - `--no-prose` makes no model calls even with a key, so decision extraction from PRs, commits and comments waits; [`repowise generate`](#repowise-generate-path) fills both later.
  - Full-text search always; semantic search needs an embedder.
- **`--onboarding`.** Slots: Project Overview, Architecture Guide, Getting Started, Codebase Map, Key Concepts, How It Works, Development Guide, Active Landscape; thin ones skipped.
- **`--resume`.** No-op when fully indexed. Skips done phases (indexing, analysis), keeps the git tier, writes only
  missing pages, never paying twice: for interrupted runs or failed pages (outage, rate limits). Per-page matching
  survives provider switches.
- **`--force`.** Re-indexes from scratch in the invoked mode, skipping worktree seeding. Never calls a model (`--force --no-prose` re-renders free); `repowise update --full` does.
- **`--distill-hook`.** One consent, four behaviours: noisy commands (tests, builds, git, searches) run through
  `repowise distill`; whole-file Reads return a skeleton (`hooks.read_skeleton`); many-file greps a per-file digest
  (`hooks.search_digest`); re-reads of unchanged, unedited files a pointer (`hooks.read_reread`). `--no-distill-hook`
  also gates the repo off in config, keeping a global hook inert.
- **`--hook`.** Asked interactively; `--yes` and non-interactive runs install it, printing the undo (`repowise hook uninstall`).
- **Workspace.** `--distill-hook` and `--hook` apply to every selected repo.
- **`--editor-setup`.** Machine-wide: Claude Code (`~/.claude/settings.json`) and Claude Desktop MCP entry, Claude Code
  PostToolUse/SessionStart hooks. Repo: `.mcp.json`, `.claude/CLAUDE.md`, `.vscode/mcp.json`, `.vscode/extensions.json`.
- **`--no-editor-setup`.** Writes only `.repowise/` (scratch checkouts, throwaway venvs, worktrees, CI); skips the post-commit hook (git hooks dir) and `--distill-hook` offer (user-level hook).
  - One `repowise` MCP key per config, so a second `init` repoints it; `repowise mcp .` prints config for manual setup.
  - `--no-distill-hook`, `--no-claude-md`, `--no-agents` still save to repo config: they mean "never".
  - `REPOWISE_SKIP_EDITOR_SETUP=1`: same switch, beats `--editor-setup`.
- **`--save-key`.** On so scripted `init` leaves an answering MCP server (env keys die with their shell). Read by
  `repowise mcp`, `serve`, `update`; without it `get_answer` degrades to `no-llm-provider` (retrieval-only).
  - `--no-save-key` or `REPOWISE_NO_SAVE_KEY=1` for per-process keys (CI secrets, shared machines); a No at the key prompt also wins.
  - Adds a `.gitignore` line, so use `--no-save-key` for `--no-editor-setup`'s nothing-in-the-repo guarantee.
- **`--yes`.** Model docs only with a configured provider (key in env or `.repowise/.env`), else from code; logged-in
  Claude Code, Codex or OpenCode needs `--provider`; `--no-prose` guarantees no model calls.
  - Accepts the cost prompt; advanced defaults (standard mode at any size, comprehensive style); connects detected agents; repoints the global MCP entry here, printing old and new.
  - Installs the post-commit hook, not distill. With `--no-prose`, replaces model-written docs unasked.
- **`--repos`.** Alias, directory name, or relative path (for duplicates). First listed is a new workspace's primary; existing ones keep theirs. Unknown names stop the run, listing options.

**Plan screen.** TTY runs without `--provider`, `--no-prose`, `--mode fast`, `--resume` or `--yes` show scan, ETA, plan,
and an opt-in model line (adds, costs). EOF (agent-piped stdin): notice, then defaults. Keys:

- Enter: free run (docs from code). `m`: pick a model. `s`: leave the global MCP entry. `q`: quit.
- `e`: edit plan (advanced options, agent checklist, post-commit hook, `.git/info/exclude` for machine-specific files, off).
- `r`: folder of repos (listed): pick repos and primary.

**Page volume.** File pages cost no tokens; caps cut wiki size, embeddings, retrieval noise. Thresholds from real repos:

- Over **2,000** documentable files (p95): the `e` step offers the top 2,000 by importance, in pages and MB. No automatic cap.
- Over **4,500** file pages (~40 MB, near p99): every run caps at 4,500 and says so; beyond it a wiki may not publish.
- `--max-file-pages` sets `max_file_pages` in `config.yaml`, honoured by `update --full` and `generate`; hosted or scripted callers set it directly.

**Cost gate.** Confirms the estimate before spending (Yes up to $25, No above). No TTY: declines unprompted, keeps the index, renders the structural wiki.

**Exit codes.**

- `0` ready; `130` interrupted (`repowise init --resume`); other non-zero: failure.
- `2`: gaps, all saved; the last line names the gap and fix (e.g. docs from code as the cost prompt needed a terminal: re-run with `--yes`). Also usage errors, before running.
- Real-embedder vector-write failure (e.g. broken LanceDB): pages and full-text kept, index scope gets `search.semantic:
  unavailable`, exit `2` names the fix (reinstall, `repowise reindex`). Keyless `mock` never fails so.
- Workspace `init` exits `2` after saving every repo, naming those with gaps. `update` exits `1` on a failed embed.

<details><summary>Driving init from an agent or script</summary>

`repowise init --yes --no-prose --progress json`: JSON lines on stdout, human text on stderr.

- Each line: `v` (schema, `1`), `event` (`stage`, `phase`, `warning`, last `summary`). `phase`: `state` (`start`/`done`/`failed`), `completed`, `total`, `unit`, `elapsed_s`.
- `summary`: `status` (`ready`/`degraded`/`interrupted`/`failed` = exit `0`/`2`/`130`/other), `mode` (`key` from index
  scope, `label`, `run_mode`), `restart_required` (restart or `/mcp` to load tools), `spend` (`tokens`, `usd`),
  `warnings`, `files_written`, `next` (commands, `repowise serve` first).

</details>

**Documentation output limit.** `max_tokens` in `.repowise/config.yaml` (not a flag) caps each model page for `init`,
`update`, `generate`, `restyle`, workspace and server generation. See [Configuration](CONFIG.md#configyaml).

---

### `repowise update [PATH]`

Refresh the index for files changed since the last sync, plus affected wiki pages in docs mode.

```bash
repowise update                        # diff since last sync
repowise update --since v1.0.0 --working-tree
repowise update --workspace --docs     # every stale repo, docs forced
repowise update --repo backend         # one workspace repo
repowise update --full --provider anthropic   # fast index to full
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--provider` / `--model` | Override for this run. | persisted choice |
| `--reasoning` | Same choices as `init`. | |
| `--since` | Git ref to diff from (overrides `state.json`). | last sync |
| `--cascade-budget` | Max pages to regenerate. | auto-scaled |
| `--concurrency` | Max concurrent LLM calls. | `10` |
| `--yes` / `-y` | Skip the `--full` cost prompt. | off |
| `--dry-run` | Preview only. | off |
| `--docs` / `--no-docs` | This run: `--docs` LLM-regenerates changed pages (even if indexed without docs); `--no-docs` = `--index-only`. | `docs_mode` in `.repowise/state.json` (from `init`) |
| `--index-only` | No doc regeneration; in a workspace, for every stale repo. | off |
| `--working-tree` | Also index staged, unstaged, untracked work; see notes. | off |
| `--full` | Fast (`--mode fast`) index to full (below). Errors in workspace mode. | off |
| `--workspace` / `-w` | All stale workspace repos plus cross-repo analysis; see notes. | auto-detect |
| `--repo` | One workspace repo by alias (implies `--workspace`). | |
| `--no-cost-tracking` | Don't record spend; see notes. | tracking on |
| `--agents` / `--no-agents` | Managed `AGENTS.md` after update. Persists. | |
| `-v`, `--verbose` | Changed-file list, per-phase internals (cascade budget, decision-marker and evolution counts, best-effort skip warnings, generation report), debug logs. | compact summary |
| `--progress` | `rich` bar, or `json` events on stdout for another process. | `rich` |

**What it refreshes.** Graph, git metadata, health, dead code; on graph-shape changes, the knowledge graph (layers, guided tour, entry points) and `knowledge-graph.json`.

- Index-only runs reuse stored layer names and node summaries (no LLM call). Docs mode saves pages as they finish; reruns skip them by prompt hash.
- Failed best-effort steps (git metadata, decisions, dead code, ...) still exit 0, listed in the completion panel and `done` event's `degraded` array (`--progress json`); the next update retries.
- Unindexed linked worktrees seed from the base checkout first ([WORKTREES.md](../scale/WORKTREES.md)).

**Flag notes**

- **`--working-tree`.** Uncommitted renames reach the graph. Off so hooks and plain runs stay commit-anchored. Workspace: uncommitted changes make a repo stale. `repowise watch` always does this.
- **`--workspace`.** Each repo's `docs_mode` (`docs_enabled` beside it) plus `--docs` / `--no-docs` / `--index-only`
  picks docs or index-only. `--docs` needs each repo's provider and key (or `--provider`).
  - Docs repos run the full single-repo docs path (pages, diagrams, decisions); others the fast parallel index-only path.
  - Entries without `.repowise/` get first-time indexing (index-only, free, `state.json` marker); docs follow on a later `--docs` run or `docs_enabled`.
- **`--no-cost-tracking`.** No second `wiki.db` engine contending with generation; live readout stays, `repowise costs` history is skipped. Also `REPOWISE_NO_COST_TRACKING`.

**Session decisions.** Docs-mode updates (and `init`) can mine local agent transcripts for user corrections, reasoned
choices, failed approaches replaced by working ones. Past deterministic gates and a verbatim-quote grounding check, 2+
sessions (or one direct correction) promote a decision (`source: session`). Local only; **off by default**: `repowise
decision source set session --on` ([CONFIG.md](CONFIG.md)).

**Upgrading a fast index to full (`--full`).** A `--mode fast` index has the full graph and metrics, but *essential* git
only (last commits, no per-file blame or co-change) and no model docs. `update --full` upgrades **incrementally**:

- Backfills git to FULL from a resumable checkpoint, reuses the graph, builds missing docs, rebuilds full-text search, embeds pages if possible, recomputes health on FULL history.
- Records the transition first and checkpoints each stage: failure leaves the prior scope truthful and retryable, never marked full early.
- Reuses valid persisted provider, model, embedder; overrides explicit; shows model cost before paid generation. Unavailable embedding is stated in the scope with the fix, `repowise reindex`.
- No key needed: without a model, docs come from code (no spend), embedder stays free unless chosen; else the model writes them. One repo at a time (`--repo <alias>` in a workspace).

<details><summary>Index scope and its MCP projection</summary>

**Index scope (`state.json:index_scope`).** Canonical index description for `status --format json`, `/api/repos`, agent guidance, MCP `_meta.index_scope`.

- Run mode, content provenance (`none`, `template`, `model`), Git tier, configured commit cap, achieved Git coverage,
  configured/effective file-page caps, eligible/generated/omitted file-page counts, unavailable or skipped analysis,
  search availability, provider choices, upgrade state.
- Older indexes show `unknown`/`null`. Caps never count as achieved coverage, nor missing analysis as clean.

**Index scope over MCP.** A **compact projection**: run mode, provenance, Git tier, `status`, degraded analyses, canonical-object `fingerprint`.

- `status`: `complete` (evidence exists and is clean), `partial`, `degraded`, `upgrading`, `unknown` (coverage never recorded).
- `get_overview(include=["scope"])`: full object plus `fingerprint`, so held copies check against later digests.
- Since `_meta` contract 3 a `complete` digest is omitted; only gaps ride.
- `REPOWISE_MCP_INDEX_SCOPE=full` always sends the full object, as builds before `_meta.contract_version` 2 did.

</details>

---

### `repowise generate [PATH]`

Write subsystem (concept) pages with a model: the upgrade path for a `--no-prose` repo.

```bash
repowise generate                 # write the unwritten subsystem pages
repowise generate --path src/api
repowise generate --page module_page:src/api --cascade none   # one page, nothing else
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--unwritten` | Pages still on a stub ("finish writing the wiki"). | on, non-interactive |
| `--all` | Every page; rewrites written prose. | off |
| `--stale` | Pages whose code changed since written. | off |
| `--path <glob>` | Pages under a prefix or glob. Repeatable. | |
| `--page <id>` | One page id. Repeatable; structural ids error. | |
| `--cascade none\|dependents\|full` | Dependent-page policy, below. | `dependents` |
| `--provider` / `--model` / `--reasoning` | LLM overrides. | |
| `--concurrency` | Max concurrent LLM calls. | `12` |
| `--dry-run` | Print plan and cost estimate only. | off |
| `--yes` / `-y` | Skip cost confirm and chooser. | off |
| `--verbose` / `-v` | Pipeline debug logs. | off |

- Selection flags combine as a union.
- **Cascade** (priced in the estimate). A rewritten page makes its layer page, overview, architecture and onboarding
  pages drift. `none`: dependents marked stale (truthful, free, instant). `dependents`: also regenerate summarizing
  overview / layer pages; rest stale. `full`: regenerate all; nothing stale.
- **Writes** only model pages: the numbered concept tree above file level, overview, architecture diagram, onboarding.
  Structural pages (file, symbol, API, infra, cycle) refresh on `repowise update`, never an LLM re-render.
- **Reuses the index** like `update --full` and `restyle`: graph and git metadata rehydrate from SQL. No provider: an error naming the key-setup path.
- **Chooser.** No selection flag on a terminal: shows written / unwritten / stale, asks about cascade only if it changes
  the pages, then confirms cost. Piped, `--yes` or flagged runs just use `--unwritten`.

<details><summary>Skipped decisions and embedding</summary>

A keyless or `--no-prose` index cannot read decisions from PRs, git history or code comments. The first `generate` (or
`update --full`) with a model runs those lanes once, priced on their own line; `repowise status` shows a Decisions row
until then.

`generate` embeds what it writes. Over `embedder: mock` in `config.yaml` it resolves a real embedder from the
environment and prints `Embedder: openai (was mock, index-only's default)`. The new vector width rebuilds the store: the
whole wiki re-embeds (no LLM) and `config.yaml` records the embedder for later `update` runs.

</details>

---

### `repowise restyle [STYLE] [PATH]`

Switch a repo's wiki **style** and regenerate every page in the new voice.

```bash
repowise restyle                       # show current style + options
repowise restyle caveman               # condensed, AI-first
repowise restyle reference --yes       # API-manual, skip the confirm
repowise restyle tutorial
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--provider` / `--model` | LLM overrides. | |
| `--concurrency` | Max concurrent LLM calls. | `12` |
| `--reasoning` | Reasoning mode, supported providers. | |
| `--verbose` / `-v` | Pipeline debug logs. | off |
| `--yes` / `-y` | Skip the confirmation. | off |

- Needs a provider and a wiki; rehydrates graph and git metadata from SQL (no re-resolution, no re-blame).
- On an `--index-only` (template) repo it upgrades every page after a spend warning; for part, or the same style, use [`repowise generate`](#repowise-generate-path).
- Styles change voice and density, not headings, so search, table of contents and cross-links work. See [WIKI.md](../layers/WIKI.md#styles).

> Editing `wiki_style` in `config.yaml` and running `update` does **not**
> regenerate pages (it only re-scores health). Use `restyle`.

---

### `repowise wiki-styles [PATH]`

List wiki styles (built-ins plus custom ones under `.repowise/styles/`) and the current one. No flags.

```bash
repowise wiki-styles
```

---

### `repowise serve [PATH]`

Start the API server and web UI.

```bash
repowise serve --port 8080 --ui-port 8081
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--port` | API port. Env `REPOWISE_PORT`. | `7337` |
| `--host` | Bind host. | `127.0.0.1` |
| `--workers` | Uvicorn workers. | `1` |
| `--ui-port` | Web UI port. | `3000` |
| `--no-ui` | API only. | off |
| `--refresh-ui` | Re-download the UI tarball (skips sources 1 and 2). | off |

The UI needs Node >= 20, else API-only. UI sources, in precedence:

1. **Local build** at `packages/web/.next/standalone/...` in a checkout; `npm run build` (if `npm` exists) when `packages/web/`, `packages/ui/src/` or `packages/types/src/` is newer.
2. **Cached download** at `~/.repowise/web/`, keyed by CLI version in `.version`.
3. **Fresh download** of `repowise-web.tar.gz` from the matching GitHub release.

---

### `repowise watch [PATH]`

Watch for file changes and auto-update wiki pages. `Ctrl+C` stops.

```bash
repowise watch --debounce 5000           # 5s debounce
repowise watch --workspace --index-only
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--provider` / `--model` | LLM overrides. | |
| `--debounce` | Ms after the last change. | `2000` |
| `--workspace` / `-w` | All workspace repos. | auto-detect |
| `--no-workspace` | Single-repo mode. | auto-detect |
| `--index-only` | No LLM page regeneration per trigger (workspace mode always). | off |
| `--verbose` / `-v` | Pipeline and update debug logs. | off |

- Unlike the post-commit hook, indexes staged, unstaged and untracked files.
- Writes in `.repowise/`, `.git/`, `node_modules/`, build output, `CLAUDE.md`, `AGENTS.md`, `.mcp.json` never trigger.
- With docs each trigger calls a model; `--index-only` keeps index, graph and health current free, prose waits for `repowise update`.

---

## Query Commands

### `repowise search QUERY [PATH]`

Search wiki pages by keyword, meaning, or symbol name.

```bash
repowise search "how are errors handled" --mode semantic
repowise search "AuthService" --mode symbol
repowise search "rate limit" --all              # workspace, fan-out
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--mode` | `fulltext`, `semantic`, `symbol`, `auto`, `concept`, `path`, `hybrid`. | `fulltext` |
| `--limit` | Max results. | `10` |
| `--all` | Every workspace repo, merged. | off |

Plus `--repo`, `--no-workspace`, `--format` and `--full` as in the [shared options](#shared-options-ask-context-symbol-why).

- `search_codebase` retrieval: full-text and vector fused, excludes and tombstones honoured, decision / test pages demoted unless asked for.
- `fulltext` and `semantic` both run the fused `concept` search; without an embedder the vector leg drops and `--mode
  semantic` says so. `auto` routes identifier to symbol, path to files, prose to concept, mixed to `hybrid`.
- Hits: `score`, `title`, `page_type`, `path`, `snippet`, plus `candidates` (openable files). Symbol hits add `name`,
  `qualified_name`, `kind`, `line`, `symbol_id`. Synthesized answers: `repowise ask`.

---

### `repowise ask QUESTION`

Answer a question about the codebase, with citations.

```bash
repowise ask "how is width resolved?" --scope packages/cli/
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--scope` | Restrict retrieval to a path prefix. | whole repo |

Plus the [shared options](#shared-options-ask-context-symbol-why).

- `get_answer` synthesis (hybrid retrieval, then LLM): the one query command that costs an LLM call.
- `confidence: high` is content-grounded, citable directly. Low confidence returns `best_guesses` (a file plus why) instead of nothing.

---

### `repowise context TARGETS...`

Triage card for files, modules or symbols: relationships and risk signals, not source bytes. Batch targets in one call.

```bash
repowise context src/api/routes.py src/api/auth.py
repowise context src/api/routes.py::login --include callers --include metrics
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--include` | Opt-in block, repeatable; values below. | |
| `--no-compact` | Add structure, imports, docstrings. | off |
| `--symptom` | Issue text, error or stack trace for `--include suspects`. | |

Plus the [shared options](#shared-options-ask-context-symbol-why).

**`--include` values:** `full_doc`, `ownership`, `last_change`, `callers`, `callees`, `references`, `metrics`,
`community`, `decisions`, `health`, `skeleton`, `skeleton+`, `doc_drift`, `symbols`, `changes`, `provenance`,
`suspects`.

- Card: title, summary, layer, signatures with lines, hotspot and bug-fix history, doc freshness, skeleton shape.
  `--include skeleton` adds the file body-elided and line-verified; `--full` only then carries one.

---

### `repowise symbol SYMBOL_ID`

Read one function, class or constant with live-verified line bounds.

```bash
repowise symbol "src/api/routes.py::login"
repowise symbol "src/api/routes.py:140-180"     # live range read
repowise symbol "repowise#a1b2c3d4e5f6"          # a distill omission ref
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--context-lines` | Extra lines around the body (0-50). | `0` |
| `--query` | Omission refs: regex or substring filter on restored lines. | |

Plus the [shared options](#shared-options-ask-context-symbol-why).

- `source` is line-numbered like a file read; `verified: true` means bounds checked against the live file.
- An ambiguous id (overloads, re-exports) returns every body. A truncated body carries a `continuation` to pass back.

---

### `repowise why [QUERY]`

Why the code is shaped this way (decisions, rationale, git archaeology); run before a refactor or pattern divergence.

```bash
repowise why "why the retry cap?" --target src/api/client.py
repowise why src/api/auth.py                     # governing decisions + origin story
repowise why                                     # decision health dashboard
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--target` | File to anchor the search. Repeatable. | |

Plus the [shared options](#shared-options-ask-context-symbol-why). A path without decisions falls back to git archaeology, so it is never empty.

---

### Shared options: `ask`, `context`, `symbol`, `why`

Thin adapters over the same-named MCP tools.

| Flag | What it does | Default |
|------|--------------|---------|
| `--path` | Repo or workspace root (a flag, since `context`'s variadic targets would swallow a trailing `[PATH]`). | cwd |
| `--repo` | Workspace repo alias. | |
| `--no-workspace` | Single-repo mode inside a workspace. | auto-detect |
| `--format` | `table` or `json`. | `table` |
| `--full` | Raw tool payload, as an MCP client gets it (implies `--format json`). | off |

`--format json` is a **trimmed CLI projection** (see each module's `project()`). Errors, not-founds, did-you-mean lists,
truncation markers, continuation tokens and ambiguity signals always survive. `ask` names omitted blocks in
`dropped_blocks`.

<details><summary>Measured sizes on this repository</summary>

Trimmed / `--full`: `ask` 3.4 / 19.5 KB; `context` (one file) 0.9 / 12.4 KB; `why` (question) 10.4 / 20.9 KB; `why`
(path) 10.4 / 28.5 KB; `symbol` 0.7 / 1.0 KB; `search` (8 hits) 4.9 / 6.8 KB; `search --mode symbol` 4.2 / 5.7 KB; `risk
--target` (one file) 4.5 / 5.3 KB.

`symbol` drops only the envelope; `search` and `risk --target` drop ranking internals, so `--full` there means exactness, not size.

</details>

---

### `repowise status [PATH]`

Show wiki sync state, page statistics, and coverage.

```bash
repowise status --workspace --format json
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--workspace` / `-w` | All workspace repos. | auto-detect |
| `--no-workspace` | Single-repo mode. | auto-detect |
| `--format` | `table` or `json`. | `table` |

- **Workspace:** a **Docs** column (page count) and per-repo **Docs status** with skip reasons (e.g. `cost gate declined`) and fix command.
- **Single repo:** a **Next** block: count worth doing this week (or quarter if quiet) and the first; JSON `next_actions`; `repowise next` lists them.

---

### `repowise next [PATH]`

The few things worth doing next, ranked from the index.

```bash
repowise next --horizon quarter --all
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--horizon` | `week` or `quarter` (90 days). | week, or quarter if the week holds no work |
| `--all` | Up to 20 rows instead of 5. | off |
| `--repo` | Workspace: one repo by alias. | |
| `--no-workspace` | Single-repo mode. | auto-detect |
| `--format` | `table` or `json` (whole stored view). Hidden alias `--json`. | `table` |

- Same stored actions as the web overview and MCP `get_overview` (`next_actions`).
- Groups **Now**, **Worth planning**, **Improve what Repowise can see**; rows give impact, facts, done-when, and any command.
- An index predating a store names it and suggests `repowise update`. Under five commits, history-ranked rows are dropped and JSON sets `context.history_too_short`.

---

## Analysis Commands

### `repowise dead-code [PATH]`

Detect dead and unused code.

```bash
repowise dead-code --safe-only --min-confidence 0.8 --repo backend
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--min-confidence` | Minimum confidence. | `0.4` |
| `--safe-only` | Only safe-to-delete findings. | off |
| `--kind` | `unreachable_file`, `unused_export`, `unused_internal`, `zombie_package`. | all |
| `--format` | `table`, `json`, `md`. | `table` |
| `--include-internals` / `--no-include-internals` | Private/underscore symbols. | off |
| `--include-zombie-packages` / `--no-include-zombie-packages` | Unused declared packages. | on |
| `--no-unreachable` | Skip unreachable files. | off |
| `--no-unused-exports` | Skip unused exports. | off |
| `--repo` | Workspace repo. | primary |
| `--no-workspace` | Force single-repo mode. | auto-detect |
| `-v`, `--verbose` | Live-analysis debug logs (table only). | off |

---

### `repowise doc-drift [PATH]`

Docs the tree does not satisfy: a missing named path, a link to a renamed heading, a `make` target the manifest lacks.

```bash
repowise doc-drift --check --format sarif > drift.sarif
repowise doc-drift --check --baseline .doc-drift-baseline.json --since auto
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--min-confidence` | Hide findings below this. | show everything stored |
| `--kind` | `path`, `link`, `anchor`, `command`, `symbol`. Repeatable. | all |
| `--document` | Only this document. Repeatable. | all |
| `--check` | CI gate on the working tree, no index; non-zero on failure. `--kind symbol` needs an index (else 2). | off |
| `--fail-on-confidence` | With `--check`: fail at or above this. | `0.7` |
| `--baseline` | With `--check`: accept findings in this file; new ones fail. | |
| `--write-baseline` | With `--check`: record findings to this file, exit 0. | |
| `--fail-on-baseline-growth` | With `--baseline`: as in `security check`; base is `--since`, else the CI target branch. | off |
| `--since` | With `--check`: gate only drift this change answers for; see notes. | |
| `--format` | `table`, `json`, `markdown`, `github` (annotations; markdown to `$GITHUB_STEP_SUMMARY` if set), `sarif`, `gitlab` (GitLab Code Quality report). | `table` |
| `--repo` | Workspace repo. | primary |
| `--no-workspace` | Force single-repo mode. | auto-detect |

- **`--since`**: gates edited documents and anchors into them, documents naming deleted or renamed files, edited
  manifests' commands, symbols whose file is edited or removed. Bare ref: `REF...HEAD` plus uncommitted; `auto`: CI
  target branch.
- Without `--check`: reads what `init` / `update` stored, as `get_health(include=["doc_drift"])` does.
- Checks only resolvable references (most are uncheckable by design); clean does not mean every sentence is true.
- Suggested fixes (never applied): module turned package, git rename, close heading or build target, renamed symbol.

**Exit codes.** Without `--check`: non-zero for no readable index or one predating drift storage (JSON names why; no
empty list). `--check`: `0` passed; `1` failed; `2` not evaluable (no git repository, unreadable baseline, undiffable
`--since` revision).

---

### `repowise risk [REVSPEC]`

Score a commit or range with code-health's calibrated signals: pure git + learned constants, no LLM, no `repowise init`. Model: [`docs/layers/CHANGE_RISK.md`](../layers/CHANGE_RISK.md).

```bash
repowise risk main..HEAD -x 'tests/' -x '*.spec.ts'
repowise risk --fail-above-percentile 95 --format github
```

**REVSPEC.** None: uncommitted work, else `HEAD`. `HEAD`: last commit. `base..head`: a branch as one change. `base...head`: from the merge-base, excluding commits landed on `base`.

| Flag | What it does | Default |
|------|--------------|---------|
| `--path` | Git repository. | cwd |
| `--ext` | Comma-separated suffixes (`.py`, `.ts,.tsx`). | all |
| `--exclude` / `-x` | Gitignore-style pattern omitted from change and baseline; repeatable; root `.riskignore` too. | |
| `--baseline` | Recent commits sampled for the percentile; `0`: absolute model-score band only. | `200` |
| `--target` / `-t` | History for these **files**, not a change (`get_risk`); repeatable. | |
| `--changed-file` | With `--target`: PR mode; a directive leads: what may break, missing co-changes and tests, what to run. | |
| `--fail-above-percentile` | CI size gate (0-100); see notes. | off |
| `--format` | `table`, `json`, `markdown`, `github` (`::error::` on gate failure, else `::notice::`; markdown to `$GITHUB_STEP_SUMMARY`). Last two: not for `--target`; no `REVSPEC` means the CI change. | `table` |
| `--full` | With `--target`: full payload as JSON (implies `--format json`). | off |

- **`--fail-above-percentile`**: exit `1` above this percentile of recent commits (`risk_percentile`, not the 0-10
  score), `2` with none (`--baseline 0`, under 8 commits). No `REVSPEC`: CI target branch `...HEAD`. JSON: `"gate":
  {"fail_above_percentile": P, "percentile": <unrounded>, "status": "pass" | "fail"}`. About (100 - P)% fail by design;
  answer with a split or second reviewer, not a fix (no baseline).
- Unreadable revspec: exit `2` even ungated; JSON `{"error", "message"}`.
- **Headline**: live percentile by size and spread among recent repo commits (size, not danger), then touched files'
  bug-fix record. Raw 0-10 score: secondary, corpus-anchored, high where commits run large. Drivers: versus the model's
  baseline commit.
- **`--target`**: bug-fix pressure, churn trend, dependents, co-change partners, ownership; needs `repowise init`; `--path` is the repository.

**[Independent changes](../layers/CHANGE_RISK.md#independent-changes).** With an index, says if the diff is one change
or several, linking files by indexed imports, calls, type references, co-change pairs and, for `base..head`, shared
commits (the author's grouping).

- Groups indexed non-test source files a resolver links; docs, config, data, tests list under `Left out of the grouping:` (first ten).
- Each group lists files and those alone holding it together; `Basis:` names what was checked (a shared commit only if commits existed).
- Silent for one change; no score; JSON `independent_changes`.

---

### `repowise overlap`

Other open branches editing the files this change edits.

```bash
repowise overlap --base main
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--base` | Base ref for both sides. | trunk from `origin/HEAD`, else `main` or `master` |
| `--branch` | The change. | `HEAD` |
| `--path` | Git repository. | cwd |
| `--limit` | Branches diffed, newest committer date first. | `50` |
| `--format` | `table` or `json`. | `table` |

- Pure git (fresh clones work); an index orders shared files, adds history pairs.
- Rows give a basis (`same file`, `co-change pair, N of M commits`), never a score.
- Skips stacked branches (either way), noise paths, dependency manifests; no shared file, no row.
- Reports branches scanned of existing; one line if no overlap. [Branch overlap](../layers/CHANGE_RISK.md#branch-overlap).

---

### `repowise security`

`scan` walks **full git history** for leaked secrets and risky patterns later removed; `check` gates a change in CI. `init` / `update` scan the working tree.

**`security scan`**

```bash
repowise security scan --history --since v1.0.0 --to HEAD
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--history` | Required; else a hint and exit (no working-tree rescan). | off |
| `--since <rev>` | Lower bound (exclusive). | all history |
| `--to <rev>` | Upper bound (inclusive). | HEAD / all history |
| `--path <dir>` | Repo path. | cwd / workspace primary |
| `--all-patterns` | Code smells (`eval`, `os.system`, weak hashes, …) besides leaked secrets (credential assignments, vendor key shapes). | off |
| `--format` | `table` or `json`; deprecated alias `--output` wins. | `table` |

Findings land in `security_findings` (idempotent; local server security API / UI); the command prints counts.

#### `repowise security check [REVSPEC]`

Gate a change on security findings it adds.

```bash
repowise security check origin/main...HEAD --format github --baseline .security-baseline.json
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--fail-on` | Exit 1 at this or above: `high`, `med`, `low` (secrets on test, fixture, spec, mock, example paths). | `high` |
| `--baseline` | Accept findings in this file; new ones fail. | |
| `--write-baseline` | Add the change's findings to this file (keeping its and `--baseline`'s); exit 0. | |
| `--fail-on-baseline-growth` | With `--baseline`: exit 1 on findings the file lacks at the change's base (re-saving hides nothing); new baselines pass. | off |
| `--path` | Path inside the repository. | cwd |
| `--staged` | Staged lines from the index (pre-commit hook): no history scan or REVSPEC; working-tree `security.patterns`. | off |
| `--format` | `table`, `json`, `markdown`, `github`, `sarif`, `gitlab` (GitLab Code Quality report). | `table` |

- **CI-ready:** git only (no index, database, API key); stores nothing. Keeps findings on changed lines of touched files
  at head; checks secrets per commit (added then deleted still fails). Snippets masked.
- **REVSPEC:** `origin/main...HEAD` (pull-request view), `base..head`, or a commit; else base from CI pull-request variables (GitHub, GitLab, Jenkins, Bitbucket) or the remote default branch.
- Custom secrets: [`security.patterns`](CONFIG.md#the-security-block). Use `fetch-depth: 0`. Scoping, GitHub Actions SARIF recipe: [In CI](../layers/SECURITY.md#in-ci-repowise-security-check).

<details><summary>Silencing a finding</summary>

A `repowise-security-ignore` comment silences its own line only (exact, case-sensitive; no next-line or whole-file form).

- Bare: every kind; `: kind, kind`: those (`custom:<name>` for custom patterns); free text: nothing.
- A secret from an earlier commit stays silenced only if that commit has the marker (squash or rotate).
- Silenced: never fail or enter `--write-baseline`. JSON: `gate.suppressed` (no matched text), `gate.suppressed_count`;
  SARIF: `inSource`; table, markdown, `github`: counted; GitLab: omitted, count logged.

</details>

**Exit codes.** `0` passed; `1` failed; `2` not evaluable: no git repository, unknown revision, shallow clone missing
the merge-base or a change commit, unreadable baseline, invalid `security.patterns`, internal error, REVSPEC with
`--staged`.

---

### `repowise impacted-tests [REVSPEC]`

Tests a change exercises, so CI runs "these 40 tests, not all 4,000".

```bash
repowise impacted-tests main..HEAD --format list | xargs pytest
repowise impacted-tests main...HEAD --format args --runner pytest --prioritize
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--path` | Repo path. | cwd / workspace primary |
| `--staged` | Staged changes (`git diff --cached`). | on with no range outside CI (in CI: the pull request's change) |
| `--format` | `table`, `json` (report + selection), `list` (an id per line), `args` (runner arguments or `:all`; reasons on stderr). | `table` |
| `--runner` | For `args`: `auto` (`files` when mixed), `pytest` (node ids or files), `go` (package dirs), `jest` (files; use `--runTestsByPath`), `files`. | `auto` |
| `--explain` | Test file or node id: why (not) selected (changed file, evidence, import route, or rule), run-order place; JSON `explain`. | |
| `--prioritize` | With `args`, `list`, `json`: whole suite, selected first; skips nothing; orders full runs. | off |

**REVSPEC**: `base...head` (since the fork, as a pull request), `base..head`, or a commit; none: staged. Looks up
changed lines (no LLM, no network) in the per-test map from [`repowise coverage add`](#repowise-coverage), naming the
path that fired:

- per-test coverage: covering tests (`via: coverage`);
- a test file: itself (`via: changed-test`);
- no coverage rows, reached in the import graph: those tests (`via: import-graph`), under "NOT coverage-backed";
- else a name match (`via: filename-pattern`), same table;
- nothing: "unknown, run the full suite" (never "no tests needed");
- no map: a prompt to `repowise coverage add` a report with contexts, plus graph guesses.

Only `coverage` proves lines ran; passing others clears nothing. The map needs per-test contexts (coverage.py `.coverage` with dynamic contexts, per-test lcov); score at the ingested `head`.

**Exit codes.** `--format args`: `0` for a subset or all; `2` for an unreadable change (unknown revision, missing
history) or `tests.*` config. Full runs: [CI](../start/CI.md#selecting-the-tests-a-change-needs).

<details><summary>JSON fields and run order</summary>

- `indexed_commit`, `map_current`; per-file `selected.basis`: `full-run`, `no-tests-needed`, `test-tree`,
  `test-package`, `conftest`, `helper-importers`, `deleted-test`, `coverage`, `changed-test`, `call-graph`,
  `import-graph`, `filename-pattern`, `unknown`, `none` (no index).
- Per-test `selected.why`: first changed file and evidence, or why it always runs.
- `selected.broadcast`: test file -> the conftest or test-package `__init__.py` on a route keeping every test under it
  (sole reason to run). `selected.import_closure`: test file reached only via imports, over one file away, no call path
  -> distance. Agent views count both apart.

Run order (`args`, `json`; `list` keeps report order unless `--prioritize`), likeliest failure first:

- Tiers: edited by the change; map hits a changed line, then file; calls, fixtures or direct import reach it; reached via other files; runs with every subset.
- In a tier: failed last local pytest run (`.pytest_cache`, repo-root ids), fewer hops (if measured), more co-changes with changed files, more changed files reached, path.
- JSON `order`: `tier`, `hops`, `co_change`, `failed_last_run`. `--prioritize` runs a file selected by some node ids whole.

</details>

---

### `repowise health [PATH]`

Per-file scores from 51 deterministic detectors, pure Python over tree-sitter + git; no LLM by default.

```bash
repowise health                     # KPIs + lowest-scoring files
repowise coverage add coverage.lcov && repowise health --recompute  # or wait for the next update
```

Detectors: McCabe complexity, nesting, brain methods, LCOM4 cohesion, god classes, native clones, untested hotspots,
coverage gradient, function/ownership/churn/change-entropy organizational risk, test-quality smells, more. Guide:
[`docs/layers/CODE_HEALTH.md`](../layers/CODE_HEALTH.md); internals:
[`docs/architecture/code-health.md`](../architecture/code-health.md).

| Flag | What it does | Default |
|------|--------------|---------|
| `--file <path>` | One file in depth (relative path). | |
| `--module <prefix>` | Files under this path prefix. | all |
| `--scope` | `all` or `production`: files every figure covers (`production` scores lower with no defect: tests score higher). | `all` |
| `--counts` | `everything` or `code_shape` (drops the git-derived half, which rises as a file is worked on: is the code itself improving?). | `everything` |
| `--refactoring-targets` | Stored queue: per file, graph-aware plans (Extract Class / Helper / Method, Move Method, Split File) as ordered steps, Break Cycle advisory; MCP and web UI order. Needs `repowise init`; [REFACTORING.md](../layers/REFACTORING.md). | off |
| `--recompute` | Analyze the working tree in-process (slow on large repos; needed without an index); see notes. | off |
| `--score <path>` | Score on-disk files pre-update or PR; repeatable. Bytes give structure, the index the rest (history, coverage, test pairing, dependents), as the next update would; no index: code shape. JSON: `files`, `score_change`. | |
| `--against <rev>` | With `--score`: before, after, change, nloc-weighted average at `<rev>`. | |
| `--generate-code <selector>` | Patch for one `path::symbol` target; the only LLM flag (needs a provider). | |
| `--plan <id>` | Stored plan as an agent prompt; JSON: recipe (preconditions, steps, postconditions). Reads the index. | |
| `--trend` | Last 10 snapshots + alerts (declining / predicted decline). | off |
| `--badge` | shields.io badge URL/JSON. | off |
| `--format` | `table`, `json`, `md`. | `table` |
| `--repo` | Workspace repo. | primary |
| `--no-workspace` | Force single-repo mode. | auto-detect |
| `--verbose`, `-v` | Pipeline debug logs. | off |

- **`--recompute`**: whole-repo `table` runs write scores, findings, trend snapshot to the index; `json`, `md`, `--file`/`--module` never do. Applies `--scope`/`--counts` to `--refactoring-targets`.
- **Default**: reads, never writes, what `init` / `update` stored (MCP and web UI rows, with `repowise coverage add`
  coverage). `repowise status`: `Health: 7.4 (avg) · 6.2 (hotspots) · 2.1 (worst: <path>)`.
- **Preview languages**: a dim line per layer and language, e.g. `Refactoring on Java: preview, measured 22% precision
  on 55 judged plans from 2 Java repos, 2026-10-15` (`repowise dead-code` too). Previews: health on C++; refactoring,
  performance, dead code on Java, C#, C, C++ (dead code also Kotlin, Swift); none held back.

#### `repowise health check [REVSPEC]`

Gate a change on code-health findings it introduces.

```bash
repowise health check origin/main...HEAD --format sarif > health.sarif
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--fail-on` | `introduced` or `worsened`. | `introduced` |
| `--fail-at` | Lowest failing severity: `medium`, `high`, `critical`. | `high` |
| `--diff-risk` | `warn`, `error`, `off`; `LEVEL` for all three rules, `RULE=LEVEL` for one; repeatable. | `warn` |
| `--path` | Path inside the repository. | cwd |
| `--format` | `table`, `json`, `markdown`, `github`, `sarif`, `gitlab` (GitLab Code Quality report). | `table` |

Git only (no index, database, API key); matches changed files' base and head findings by file, marker, symbol, never line:

- Introduced on changed lines, at `--fail-at` or above: fails.
- Worsened, at `--fail-at` or above: warns; fails with `--fail-on worsened`.
- Either below `--fail-at`, or in a test file: warns.
- Moved or renamed with its code, off changed lines, or not code shape (performance, history, coverage): context only.

**Diff-risk rules** read changed text in any supported language; markers moved with their code to another file are ignored.

- `added-suppression`: new lint, type-check, coverage or repowise suppression comment or attribute, or biomarker newly disabled in `.repowise/health-rules.json`.
- `weakened-tests`: new unconditional skip or focus marker, fewer test cases or assertions, looser coverage threshold.
- `test-only-branch`: new test-environment check in production code.

**REVSPEC** as for [`security check`](#repowise-security-check-revspec). Both sides use the base's
`.repowise/health-rules.json`; a change cannot loosen its gate. An unread code file (unparsable, over 1.5 MB) warns;
touching no readable file passes with a note.

**Exit codes.** `0` passed (warnings included); `1` failed; `2` not evaluable: no git repository, unknown revision, no
merge-base in a shallow clone, invalid `health-rules.json` at the base, over 300 changed code files, no readable changed
code file, internal error.

---

### `repowise decision`

Manage architectural decision records.

```bash
repowise decision list
repowise decision show ID
repowise decision add                   # interactive
repowise decision add --title T --decision D  # no prompts, lands proposed
repowise decision candidates            # awaiting review; these govern nothing
repowise decision confirm ID...         # accept: this makes them govern
repowise decision dismiss ID...         # tombstone (sticky; never re-proposed)
repowise decision merge ID INTO_ID      # fold into an existing decision
repowise decision dedupe                # fold duplicate candidates (dry run unless --apply)
repowise decision split ID              # flag as bundling two choices; never splits it
repowise decision deprecate ID          # optionally naming its successor
repowise decision health
repowise decision status                # what capture did and cost
repowise decision export                # accepted decisions to .repowise/decisions.yaml
repowise decision import                # reconcile the store to that file
repowise decision migrate               # classify pre-split rows (dry run unless --apply)
repowise decision config show                   # resolved capture policy
repowise decision config preset NAME            # default | off | local_only | balanced | full
repowise decision config discovery              # broad discovery call budget
repowise decision config agent-acceptance --on|--off  # may an agent grant authority? (off)
repowise decision config capture-prompt --on|--off    # agent records what it committed (off);
                                                      # --on installs the shell hook
repowise decision source list                   # source registry and state
repowise decision source set SRC --on|--off     # switch one source
repowise decision source set SRC --llm|--no-llm # switch only its model stage
repowise decision llm --on|--off                # all decision-extraction model calls
```

Every subcommand takes `--format` (`table` default, or `json`) and an optional `[PATH]` last.

**`add`**

| Flag | What it does | Default |
|------|--------------|---------|
| `--title` | Short title. | |
| `--decision` | What was chosen. | |
| `--context` | What forced it. | |
| `--rationale` | Why. | |
| `--alternative` | Rejected alternative. Repeatable. | |
| `--consequence` | Accepted tradeoff. Repeatable. | |
| `--affects` | File or module governed. Repeatable. | |
| `--tag` | Tag. Repeatable. | |
| `--evidence-commit` | Commit it was made in. Repeatable. | |
| `--kind` | `architectural` governs code; `agreement` governs how work is done (no files, never checked against code). | `architectural` |

- `--title` plus `--decision`: no prompts, prints the new id. Any flag, or `--format json`, without both is refused.
- Flag-driven records land `proposed`, interactive `active`; promote with `repowise decision confirm <id>`.

**`candidates`**

| Flag | What it does | Default |
|------|--------------|---------|
| `--state` | `open`, `accepted`, `merged`, `needs_split`, `dismissed`, `all`. | `open` |
| `--lane` | Extraction lane: `pr`, `session`, `session_discovery`, `comment`, `git_archaeology`, `adr`, `inline_marker`, `conventions`, `cli`. Not the Decisions page review lanes. | all lanes |
| `--limit` | Max listed. | `30` |

**`confirm`, `dismiss`, `deprecate`, `split`**

| Flag | What it does | Default |
|------|--------------|---------|
| `--reason TEXT` | `confirm`: rationale, or why a constraint needs none; corrects the record. `dismiss`: why. `split`: what the two choices are. | |
| `--scope PATH` | `confirm`: file or module governed. Repeatable; replaces the proposed scope. | proposed scope |
| `--evidence REF` | `confirm`: commit, file or link. Repeatable. | |
| `--as NAME` | `confirm`: accepter other than git identity. | git identity |
| `--superseded-by ID` | `deprecate`: lineage edge to the replacement; deprecated id keeps resolving. | |
| `--agent SLUG` | Not `split`: sign as an agent (e.g. `claude_code`). | |
| `--session ID` | Not `split`: signing agent session. | |
| `--yes` / `-y` | `dismiss`: skip the prompt (as does `--format json`). | off |
| `--preview` | `confirm`, `dismiss`: report each id's outcome, write nothing. | off |

**`dedupe`, `migrate`, `export`, `import`**

| Flag | What it does | Default |
|------|--------------|---------|
| `--apply` | `dedupe`, `migrate`: write the plan; else report only. | off |
| `--tau` | `dedupe`: cosine fold threshold, `0.5` to `1.0`. | `0.83` |
| `--limit` | `dedupe`: clusters listed. `migrate`: reasons listed. | `10` |
| `--force` | `export`: overwrite a committed manifest from a store that accepted nothing. | off |
| `--dry-run` | `import`: write nothing. | off |

**`list`**

| Flag | What it does | Default |
|------|--------------|---------|
| `--status` | `active`, `proposed`, `deprecated`, `superseded`, `dismissed`, `all`. | `all` |
| `--source` | `adr`, `cli`, `comment`, `commit`, `conventions`, `git_archaeology`, `inline_marker`, `llm_inferred`, `pr`, `session`, `all`. | `all` |
| `--proposed` | `--status proposed`. | off |
| `--stale-only` | Only stale. | off |

**Capture control**

| Flag | What it does | Default |
|------|--------------|---------|
| `--dry-run` | `config preset`, `config discovery`, `source set`, `llm`: print the change, write nothing. | off |
| `--format` | `json` returns the full source registry. | `table` |
| `--max-sessions` | `config discovery`: session deltas one broad call reads (1-24). | `12` |
| `--max-input-tokens` | `config discovery`: input-token ceiling per broad call (2000-60000). | `30000` |

- `config discovery` without budget flags prints the resolved policy. Broad discovery is the `session_discovery` source: off in `default`, on in `balanced` and `full`.

**Acceptance.** Only `confirm` promotes to `active`; extraction, recurrence across sessions and confidence stop at `proposed`.

- `confirm` needs a reason, a scope and an evidence reference (flags supply what is missing), else refuses. JSON refusal: `{"error": "acceptance_refused", "blockers": [...]}`, exit 1 (not a crash).
- `candidates` lists first what the contract would accept at the last index; `Acceptable` reads `yes` or `confirm`'s blockers (JSON: `blockers`).
- `list --status` filters a status column kept for pre-split readers, not "what governs"; `candidates` is the
  authoritative unaccepted list. The Decisions page shows five lanes over the acceptance join.
- `confirm` and `dismiss` take many ids, each applied independently: a refused id commits the rest and exits 1. `--preview` runs the contract, then rolls back.
- JSON: one id emits `{"id", "status", "action"}`; several, or `--preview`, emit `action`, `preview`, `succeeded`,
  `failed` and `results[]` (`given`, `id`, `title`, `ok`, then `action` / `status` or `error` / `blockers`, e.g. `"no
  scope: name the files or modules it governs"`).

**`status`.** Policy, preset; per source: state, why no call, what it captured and when; five review lanes, backlog
acceptable today versus blocked, its age; staging queues; decision-extraction model spend. No run ledger: spend is
all-time with the last call named; a queue the store predates reads absent, not zero.

```
repowise decision status

  Decision capture

    Capture on  ·  LLM extraction on  ·  preset default

  Source             Status               Records  Accepted  Last seen   Why
  inline_marker      enabled              4        2         2026-08-31  Runs with model structuring.
  pr                 skipped_no_provider  213      0         2026-08-31  No LLM provider is configured.
  session_discovery  disabled             0        0         -           This source is switched off.
```

**Scripting.** Unknown id: `confirm`, `dismiss`, `deprecate`, `show` exit non-zero with `{"error": "decision_not_found",
"decision_id": "..."}`. JSON `list` gives full ids (table: 8-character prefixes); JSON `show` / `health` skip display
caps.

---

### `repowise coverage`

Ingest coverage reports and gate a change on patch coverage in CI.

```bash
repowise coverage add [PATHS...]        # ingest (+ per-test map when contexts exist)
repowise coverage status                # ingested coverage + "Test-to-code map" counts
repowise coverage check [REVSPEC]       # gate a change on patch coverage (CI)
repowise coverage suggest-gates
repowise coverage add web.lcov api.lcov          # merged, hit wins
repowise coverage add 'artifacts/**/lcov.info'   # every shard
repowise coverage add web/coverage/lcov.info=web # paths relative to web/
pytest --cov=<package> --cov-context=test        # .coverage with contexts (needs pytest-cov)
repowise coverage add .coverage                  # per-test map
```

`init` / `update` auto-ingest; `add` is the manual path. Per-file line/branch coverage clears `untested_hotspot` for tested files wherever their tests live.

| Flag | What it does | Default |
|------|--------------|---------|
| `--path` | Repo path. | `add`: cwd / workspace primary; `status`: cwd |
| `--format` | `add`: force a parser (`lcov`, `cobertura`, `clover`, `repowise-json`, `go-coverprofile`, `jacoco`). `status`: `table` or `json`. | `add`: auto-detect; `status`: `table` |
| `--strict` | `add`: also exit non-zero when some report files do not map. | off |
| `--allow-partial` | `add`: store even when over half does not map; rows flagged partial. | off |
| `--verbose` / `-v` | `add`: debug logs. | off |

- `PATH`: report path or quoted glob, relative to cwd, as in `check --report`; read as `PATH=PREFIX` (like `path_prefix`
  in `coverage.paths`) only when it matches nothing. No match exits 2. Success syncs the opt-in hook
  `hooks.coverage_reingest`.
- Over half unmapped: stores nothing, exits `1`, suggests a `coverage.strip_prefix` or `coverage.path_prefix`. `init` /
  `update` keep nothing from it, so their re-read clears an `--allow-partial` fragment.
- Formats: LCOV, Cobertura, Clover, JaCoCo XML, Go coverprofile (`go test -coverprofile`). With no path, discovers
  `coverage/lcov.info`, `.coverage`, `coverage.out`, `target/site/jacoco/jacoco.xml` and similar.
- *Test-to-code map* (which test covers which lines: "which tests exercise this change"): from a context-recorded
  `.coverage` or per-test lcov; without contexts, per-file only. Separate from the per-file aggregate and `health`.
  `.coverage` lacks executable lines, so gives only this map; `coverage lcov` exports per-file data.

#### `repowise coverage check [REVSPEC]`

Gate a change on its **patch coverage**: the share of changed executable lines the tests ran.

```bash
repowise coverage check origin/main...HEAD --report coverage/lcov.info --fail-under 80
repowise coverage check origin/main...HEAD --report 'artifacts/**/lcov.info' --min-coverable-lines 5
```

Needs only git and a report (no index, no LLM key). Paths resolve against `git ls-files`, added files included.

| Flag | What it does | Default |
|------|--------------|---------|
| `--report` | Path or glob, relative to cwd; `PATH=PREFIX` as in `add`. Repeatable, merged hit-wins. | `coverage.paths`, else discovery |
| `--report-format` | Force a parser (as `add --format`). | auto-detect |
| `--fail-under` | Exit 1 below this % (0-100). Unset anywhere: no gating. | `coverage.fail_under` |
| `--min-coverable-lines` | Fewer changed measured executable lines: reported, never fails (`too_small`). | `coverage.min_coverable_lines` |
| `--fail-under-risky` | Exit 1 when risky files' % is below this. | `coverage.fail_under_risky` |
| `--fail-under-branches` | Exit 1 when branches taken on changed lines are below this % (0-100); never blended. | `coverage.fail_under_branches` |
| `--base-report` | Report at the base (merge-base for `A...B`), read like `--report`, repeatable. Adds `project`. | |
| `--max-drop` | Exit 1 when project coverage drops over this many points (0-100). Needs `--base-report` or an index ingest at the base. | `coverage.max_drop` |
| `--path` | Path inside the repo. | cwd |
| `--format` | `table`, `json`, `markdown`, `github`. | `table` |

- `--report`: `**` skips dependency, cache, build dirs; `=` splits on the last one.
- Risky: index hotspots or bug magnets, else the top bug-fix quartile. No risky or no branching changed line: `no_data`, exit `0`.
- REVSPEC: `base...head` (since the fork, the PR view), `base..head`, or one commit; explicit in CI. Omitted:
  `<base>...HEAD`, base from `GITHUB_BASE_REF`, `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`, `CHANGE_TARGET` or
  `BITBUCKET_PR_DESTINATION_BRANCH` (as `origin/<name>`), else `origin/HEAD`, local `main` / `master`, `origin/main` /
  `origin/master`; else exit 2.
- Reports: `--report`, else `coverage.paths` in `.repowise/config.yaml`, else (with `coverage.auto_discover`)
  `coverage/lcov.info`, `coverage.xml`, `coverage.out`, `**/target/site/jacoco*/jacoco.xml`, root-module
  `build/reports/jacoco/**/*.xml`; nested Gradle modules need `--report`. Config is read at the git toplevel, even with
  `--path`.
- Export `.coverage` with `coverage lcov` or `coverage xml`. A new untested file must still be in the report; see [Coverage reports per language](../start/CI.md#coverage-reports-per-language).

**File statuses.** Changed comment or blank lines count as neither.

| Status | Meaning | Counts toward the % |
|--------|---------|---------------------|
| `measured` | Named; some changed lines executable | Yes |
| `no_coverable_changes` | Named; no changed line executable | No |
| `not_in_report` | Measured extension, not named (e.g. new untested file) | No, listed "not in report", never 0% |
| `no_line_data` | Named without an executable-line set | No |

- Out of scope, only counted: changed tests, unmeasured types (docs, config). Left out, counted as ignored: `coverage.ignore` (gitignore syntax) matches.
- Go coverprofile paths under a `go.mod` module path map to its directory, unless a per-report prefix or `coverage.path_prefix` is set.
- Percentages floor to one decimal (79.99% shows 79.9%); the gate uses the unrounded value.

**Risk and hints.** Risk per file: git bug-fix history, plus hotspot, bug-magnet and dependent counts with an index (a
missing one never fails). Risky files first; worded "Risk" column. With an index, each uncovered range names a test to
extend ("Extend" column, annotation suffix, JSON `hints`): "extend tests/test_auth.py (inferred: calls reach `login`)",
"(measured: runs other lines of `login`)", "(inferred: imports this file)", "no test reaches this; add one". Hints never
change the verdict.

**Path-scoped gates.** Each `coverage.gates` entry (`name`, `paths` as gitignore-style globs from the repo root,
optional `fail_under` 0-100 and `informational`) judges its matching measured changed files like the whole change; a
file can count in several.

- The whole change's small-change tolerance applies: every gate (risky too) reads `too_small` under it.
- Matched unmeasured files: `unmeasured_file_count`, outside the %.
- A failing non-informational gate makes `gate` `fail` and leads the headline with its counts.
- Table lists every gate; markdown up to 10, failing first. See [Path-scoped gates](../start/CI.md#path-scoped-gates) and `repowise coverage suggest-gates`.

**`github` format**, in order: up to 10 `::warning` annotations (riskiest file, then largest range; risky ones titled
"Uncovered change in a risky file") plus a `::notice` counting the rest; `::error::` per failing gate (whole,
non-informational path, risky, branch); `::notice::` per informational gate below threshold, tolerance-exempted gate, or
set but unapplied branch gate; "Partly taken branch" `::warning` per partly taken range (same cap); markdown to
`$GITHUB_STEP_SUMMARY` when set. Markdown and `github` also list files that lost coverage outside the change.

<details><summary><code>json</code> fields</summary>

- Top: `patch_coverage_pct`, `covered_line_count`, `coverable_line_count`, `threshold`, `min_coverable_lines`, `gate` (`pass`, `fail`, `no_data`, `not_set`, `too_small`; `fail` if any gate fails).
- `file_counts`: `measured`, `not_in_report`, `no_line_data`, `no_coverable_changes`, `stale_file` (stored coverage only, always 0 here), `out_of_scope`.
- `files[]`: `file_path`, `status`, `changed_line_count`, `coverable_line_count`, `covered_line_count`,
  `patch_coverage_pct`, `uncovered_ranges`, `branch_taken`, `branch_total`, `partial_ranges` (0/empty without branch
  data); `risk` (`fix_pressure`, `dependents`, `hotspot`, `bug_magnet`, `basis`, `risky`, `reasons`); `hints` (null
  without index; per range: `range`, `symbol`, `tests`, `basis` `per_test`/`call_graph`/`import_graph`/`none`, `total`).
- `scope`: `label`, `source_formats`, `reports`, `report_path_count`, `unmatched_report_path_count`, `ignored_file_count`, `config_errors`, `branch_data` (`per_line`, `none`, `stored_before`).
- `path_gates[]`: `name`, `paths`, `informational`, `measured_file_count`, `unmeasured_file_count`; `risky`:
  `file_count`. Both also carry `covered_line_count`, `coverable_line_count`, `patch_coverage_pct`, `threshold`, `gate`.
- `project` (null without base): `basis` (`base_report`, `history`), `base_commit`, `head_commit`, `base` / `head`
  (`covered_line_count`, `coverable_line_count`, `coverage_pct`), `delta_pct` (points), `max_drop`, `gate`,
  `incomparable` reasons, `outside_change_note`, `outside_change[]` (null on history basis or when most files do not
  line up: `file_path`, `status` `changed`/`no_longer_measured`, `newly_uncovered_ranges`, `newly_uncovered_line_count`,
  `newly_covered_line_count`, `base_pct`, `head_pct`, `causes` (`kind`
  `test_deleted`/`test_modified`/`dependent_changed`, `path`, `basis` `per_test`/`graph`/`name`)).
- `branches` (null with no branch data and no branch gate): `branch_taken`, `branch_total`, `branch_coverage_pct`, `partial_line_count`, `threshold`, `gate`.

</details>

**Exit codes.** `0` pass or nothing to judge (no threshold, no measurable lines, under tolerance). `1` a gate fails
(`--fail-under`, non-informational path gate, `--fail-under-risky`, `--fail-under-branches`, `--max-drop`). `2` could
not run:

<details><summary>Exit 2 cases</summary>

- No report, a `--report` matching nothing, none readable or matching a repo file, all in `coverage.ignore`; unknown
  revision, no merge-base (shallow clone), single commit at a shallow boundary, not a git repo.
- Malformed `coverage.fail_under` / `coverage.min_coverable_lines` / `coverage.fail_under_risky` / `coverage.fail_under_branches`, or an unusable `coverage.gates` entry (named).
- `--fail-under-risky` on a shallow clone or a measured file with unreadable risk, unless the flat, a path or the branch gate already failed (then `1`).
- `--fail-under-branches` without per-line branch data (`no_branch_data`); from config alone, a note, exit `0`.
- `--max-drop` / `--base-report` with a base missing, incomparable, or with no coverable line on a side (same exception); from `coverage.max_drop` alone, a note, exit `0`.

</details>

**Shallow clones.** Shallow CI clones lack the merge-base (exit 2); fetch full history. Action, GitLab template, recipes: [Repowise in CI](../start/CI.md).

#### `repowise coverage suggest-gates`

Propose path-scoped gates for `coverage.gates`.

```bash
repowise coverage suggest-gates --format json
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--path` | Path inside the repo. | cwd |
| `--format` | `yaml` (paste below `coverage:`) or `json`: `{"sources": [{"source", "detail", "gates": [{"name", "paths"}]}]}`. | `yaml` |

**Sources**, in order, each labelled in a comment:

- **CODEOWNERS:** first of `.github/CODEOWNERS`, `CODEOWNERS`, `docs/CODEOWNERS`, `.gitlab/CODEOWNERS`; a gate per
  owner. A later, possibly overlapping pattern not theirs (owner-less too) becomes a `!` exclusion; `*` drops earlier
  gates, makes none; a fully overridden owner gets none. Handles escaped spaces, tab-separated comments, GitLab
  `[Section] @owners` defaults.
- **Top-level packages from `git ls-files`:** each directory under `packages/`, `apps/`, `services/`, `libs/` with a non-test source file, else each top-level source directory.
- **Graph communities** (indexed): 3+ source files, over their directories, named by common directory; 10 largest, minus
  package-gate glob duplicates. Comment names the indexed commit, or no index, unreadable, no communities.

**Output.** First two sources need no index. Writes nothing, sets no `fail_under`. Pastes below `coverage:`; repeated names get `-2`, so it is valid config as printed.

### `repowise components`

Validate, list and audit ownership of declared components.

```bash
repowise components check --format json
repowise components ownership
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--path` | Repo path. | cwd |
| `--format` | `table` or `json`. | `table` |

- **`check`**: validates `.repowise/components.yaml` and a repo-root `catalog-info.yaml` (Backstage, see
  [CONFIG.md](CONFIG.md#repowisecomponentsyaml)) against git-tracked files. Exits 1 on any error (CI-ready).
- **`list`**: declared and derived components with file counts.
- **`ownership`**: declared owners (catalog `spec.owner`, CODEOWNERS,
  [`.repowise/teams.yaml`](CONFIG.md#repowiseteamsyaml)) against indexed commits of the last 90 days. Flags a high or
  critical component with no active declared owner or bus factor 1, an owner with no recent changes, a catalog entry
  claiming no files.

<details><summary>Ownership matching</summary>

- An owner with no identity in indexed history is unknown, never inactive; `critical_no_active_owner` does not fire when every declared owner is unknown.
- Individuals match only by exact git email or forge noreply login. A bare `@name` outside `.github/CODEOWNERS` naming no declared team stays unresolved (on GitLab it may be a group).
- Findings name components and teams; individuals are counted, never named.

</details>

---

### `repowise packages`

Find third-party package imports; export or join package data. See [PACKAGE_USAGE.md](PACKAGE_USAGE.md).

```bash
repowise packages where requests
repowise packages export -o usage.cdx.json
repowise packages import sbom.json
```

| Flag | Subcommands | What it does | Default |
|------|-------------|--------------|---------|
| `--path` | all | Repo path. | cwd |
| `--format` | all | `text` or `json`; `export`: `cyclonedx`. | `text`; `export`: `cyclonedx` |
| `--output` / `-o` | `export` | Output file. | stdout |

- **`where NAME`** (name or `ecosystem:name`): importing files with import line, component, owner teams and reachability, each with basis.
- **`export`**: declared packages and import sites as CycloneDX 1.6 JSON (`evidence.occurrences`). Not an SBOM.
- **`import FILE`**: joins your Syft, SPDX, CycloneDX or OSV JSON to usage by package name. Fetches nothing, matches no vulnerability.

---

### `repowise changes`

List, show and export merged changes read from git at each index and update.

```bash
repowise changes list --needs-attention
repowise changes show pr-123 --format in-toto
```

A change is one first-parent commit, identified by its message's PR or MR number, then Gerrit `Change-Id`, then merge or
commit sha. A merge lands its branch's commits; rebase merges give one change per commit.

| Flag | Subcommand | What it does | Default |
|------|-----|--------------|---------|
| `REF` | `show` | Id (`pr-123`) or any sha the change landed. | |
| `--path` | all | Repo path. | cwd |
| `--category` | `list` | Category (feature, fix, ...). | |
| `--shape` | `list` | `merge`, `squash` or `commit`. | |
| `--grep` | `list` | Substring of title, id or a file path. | |
| `--needs-attention` | `list` | Kept checklist flags an item. Builds none, so unopened, unexported changes are skipped. | off |
| `--limit` / `--offset` | `list` | Page size / skip. | `30` / `0` |
| `--since` / `--until` | `export` | Merged on or after / before (UTC, `--until` exclusive). | |
| `--format` | all | `table`, `json` (`show`: versioned record); `show` adds `in-toto` (Statement v1, commits as subjects); `export` is `json` (JSON Lines) or `csv` (row per change and item). | `table`; `export`: `json` |
| `--evidence` / `--no-evidence` | `show` | Print the checklist. | on |
| `--refresh` | `show`, `export` | Rebuild checklists. | off |

**Evidence checklist**: components touched, declared owners, suggested reviewers, reaching tests, contracts, consumers
in other repos, decisions crossed, health delta, AI provenance, diff size rank. Items carry a status (`ok`, `attention`,
`unknown`) and basis. Built on first read and kept; `export` streams each change with it, building a missing one in a
few seconds.

<details><summary>Outcomes item</summary>

The last item, `outcomes`, is reread whenever HEAD moves. It quotes reverts naming the change and later descendant
commits naming it as their fix's cause (`Fixes: <sha>`, "regression from/in/since", "caused by", "broken by"). It also
counts the change's files that got fix commits within 30 days (`changes.outcome_fix_days` in `.repowise/config.yaml`):
where fixes landed, never that the change caused them.

</details>

---

### `repowise suspects`

Rank changes merged in a window against a symptom: where to start reading.

```bash
repowise suspects --symptom "TypeError in checkout" --trace trace.txt
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--symptom` | Issue text or error message; first 20,000 characters read. | |
| `--files` | A file involved. Repeatable, up to 50. | |
| `--trace` | Stack trace file. | |
| `--since` | Merged on or after (UTC). | 14 days before `--until` |
| `--until` | Merged before (UTC); when the symptom was seen. | now |
| `--limit` | Changes ranked. | `10` |
| `--path` | Repo path. | cwd |
| `--format` | `table` or `json`. | `table` |

- Rows list fired signals (text match, import distance, co-change, recency, trace hits) with evidence. Order is where to read first, never a cause; `git bisect run` confirms one.
- Text match dominates and most regressions surface after 14 days, so an unlisted change is not cleared: widen `--since`. Results and bisect recipe: [CHANGES.md](../layers/CHANGES.md#suspects).

---

### `repowise check [REVSPEC]`

Evaluate `.repowise/policies.yaml` (see [CONFIG.md](CONFIG.md#repowisepoliciesyaml)) against a change.

```bash
repowise check origin/main..HEAD --sarif-file check.sarif
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--path` | A path inside the repository. | cwd |
| `--policies` | Policy file. | `.repowise/policies.yaml` |
| `--sarif-file` | Also write SARIF 2.1.0 (code scanning, reviewdog). | |
| `--junit-file` | Also write JUnit from the same run. | |
| `--format` | `table`, `json`, `sarif` or `junit`. | `table` |

- Evaluates components touched, tags, git-marker provenance; with an index also reaching tests, decisions crossed, bus factor.
- Gate rules run `security check`, `coverage check` or `doc-drift --check` exactly as alone; with no policy file gates run as alone and `check` reports nothing to evaluate.
- Exit `0` no `fail` rule fired; `1` one did; `2` invalid policy or an unevaluable `fail` gate.
- **OPA.** `evidence` from `--format json` is OPA `input`: one checklist item per kind (`components_touched`,
  `provenance`, `reaching_tests`, `decisions_crossed`, `contracts`) with status, basis, per-file details. `opa eval
  --input check.json --data policy.rego 'data.repowise.deny'` runs Rego over `input.evidence.items`, beside or instead
  of `policies.yaml`.

---

### `repowise ci-summary DIR`

Combines the gates a CI job ran into one verdict table, appended to the GitHub job summary, and prints the combined verdict (`pass`, `warn` or `fail`). Each gate run with `--format github` while `REPOWISE_GATE_SUMMARY_DIR` names a directory leaves its result there, and each `--gate NAME=EXIT_CODE` reconciles one with its exit code; a gate that left nothing reads as could not evaluate. `--comment-file` writes a pull-request comment that also lists each gate's findings, `--previous` (the comment it replaces) adds how many findings were resolved since, and `--comment-key` keeps one comment per working directory.

```bash
repowise ci-summary DIR --gate NAME=EXIT_CODE... [--comment-file FILE] [--previous FILE] [--comment-key KEY]
```

---

### `repowise events`

List typed events recorded between consecutive index runs, oldest first.

```bash
repowise events --since 7d --kind bus_factor_drop
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--path` | Repo path. | cwd |
| `--since` | `7d`, `12h` or ISO date. | |
| `--kind` | Only this kind. Repeatable. | all kinds |
| `--limit` | 1 to 10000. | `200` |
| `--format` | `table`, `json` or `jsonl`. | `table` |

**Kinds**: `ownership_shift`, `owner_inactive`, `bus_factor_drop` (critical components), `new_hotspot` (off by default),
`contract_added`, `contract_broken`, `decision_stale`, `dead_code_growth`, `test_reach_drop`.

- Each `init` and `update` snapshots every component (ownership counts, bus factor, hotspots, dead code, governing decisions, provided contracts, test reach) under its analyzer and parser stamp.
- Same stamp as the previous run: what moved becomes an event. A stamp change, or a run after one with no snapshot
  (events off, snapshot failed), starts a baseline. Already snapshotted commits (even `--working-tree`) are skipped.
- Stored as CloudEvents JSON lines in `.repowise/events.jsonl`.

<details><summary>Contract kinds (workspace only)</summary>

- Measured after the cross-repo pass extracts contracts at the run's commit; outside a workspace they never fire.
- Index-only `update` (post-commit hook) skips that pass, leaving contracts unavailable; contract kinds compare with the last earlier measured run, passing over those.
- Known limit: consumer counts are read at the provider's runs, so a consumer-only change moves no event.
- Known limit: past `compact_after_days`, one run per week is kept; the comparison run can be dropped, falling back to an older measured run or none.

</details>

**Config**: `events:` in `.repowise/config.yaml`.

| Key | What it sets | Default |
|-----|--------------|---------|
| `enabled` | On or off. | |
| `retention_days` | Days kept. | `365` |
| `compact_after_days` | Age after which runs keep one per week. | `30` |
| `kinds` | Per-kind switches. | |
| `sinks` | `jsonl`, or `stdout` for CI. | `jsonl` |

**Web UI**: Commits page Events view, newest first; `GET /api/repos/{id}/events` adds `snapshot_runs`, `empty_reason`
(`baseline_only` before two snapshots, `nothing_moved`, `no_match`) and `empty_note` (this command's empty-list
sentence).

---

### `repowise report assessment [PATH]`

One hand-off document for a repo or every workspace repo.

```bash
repowise report assessment --format html --out assessment.html
repowise report inventory --format csv
repowise report changes --since all --no-build
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--sections` | Comma-separated section ids. | all |
| `--format` | `json`, `markdown`, `html`, `csv`, or a plugin renderer. | `markdown` |
| `--out` | Output file. | stdout |
| `--workspace` / `--no-workspace` | Every workspace repo / force single repo. | auto-detect |
| `--repo` | Workspace repo alias. | |

- Items keep OpenSSF Scorecard keys (`name`, `score` 0 to 10 or -1 inconclusive, `reason`, `details`, `documentation`)
  plus `method` and `basis`. Counts and lists have `score: null`; missing or held-back evidence is -1, never a low
  score; basis `unresolved` means missing.
- Owners are counted, never ranked or named. Local paths removed.
- JSON is canonical (`schema_version: 1`). HTML is one file, inline CSS, no scripts, all fetches blocked: opens offline, prints as is.

<details><summary>Sections</summary>

| Section | Measures | Basis |
|---|---|---|
| `components` | NLOC-weighted health, catalog facts, `components ownership` findings | `measured`; `gated` where language health is held back |
| `inventory` | Owner teams, languages, entry points, imported packages ([package usage](PACKAGE_USAGE.md)) | `measured`; owners `declared` |
| `hotspots` | High recent churn files | `measured` |
| `bus-factor` | Fewest primary owners holding over half of owned files; 1 or 2 named in summary unless the catalog marks them | `measured`; `inferred` under five owned files |
| `dead-code` | Open findings by tier; no `safe_to_delete` | `inferred`; `gated` for held-back languages |
| `test-reach` | Ingested line coverage and test-reached production files, separate items | `measured`, `inferred` |
| `contracts` | Provided contracts and other-repo consumers | `measured` when workspace store matches the indexed commit |
| `decisions` | Accepted governing decisions, and stale ones | `declared` |
| `outcomes` | Changes later reverted or named as a fix's cause; from checklists kept on Change Records, omitted when none | `declared` |
| `provenance` | Commits in the last 90 indexed days with known source (count, not score); agents and models listed, not counted | `measured`, `declared`; `unresolved` with no markers |
| `trend` | Moves since a same-stamp snapshot 90+ days older, by `events` rules, for enabled kinds | `measured` |

Per-component sections open with a summary row counting components by outcome. A component gets its own item only for an
ownership finding, bus factor 2 or less where the catalog gives criticality or tags, hotspots, a stale decision,
provided contracts, or unresolved or gated evidence; the rest are named in the summary and JSON `data` keeps all
figures.

**Extending.** Sections are `Section`s on `SeamRegistry("report-section")`, one per module in
`repowise/core/report/sections/`; plugins register the same way. Absent data returns `None` and omits the section (like
`outcomes` before any checklist is opened). Renderers live on `SeamRegistry("report-renderer")`, chosen by `--format`.
No modernization candidates: no signal measures them.

</details>

**`repowise report inventory [PATH]`** renders `inventory` alone, for software inventory review (PCI DSS 6.3.2). Same
flags as `assessment` minus `--sections`. JSON `data` keeps all owner teams, languages, entry points and packages; text
lists components with no owner team.

**`repowise report changes [PATH]`**: change-management audit evidence (SOC 2 CC8.1, ISO 27001 A.8.32, PCI DSS 6.5).

| Flag | What it does | Default |
|------|--------------|---------|
| `--since` | Merged on or after (UTC); `all` for every change. | 90 days before newest indexed commit (or `--until`, when earlier) |
| `--until` | Merged before (UTC). | |
| `--format`, `--out`, `--workspace` / `--no-workspace` / `--repo` | As `assessment`. | |
| `--refresh` | Rebuild every checklist in the period. | off |
| `--no-build` | Use kept checklists only; rest count unknown. | off |

- Per change: components and owning teams, review record ("not recorded in git" unless `Approved-by` trailers or a
  `change-metadata` source supply one), measured and inferred reaching tests, policy verdict (condition rules of
  `.repowise/policies.yaml`, else "no policy configured"; gates not re-run), provenance, later revert or fix-cause
  naming.
- A period summary and a method appendix (what git alone cannot attest) both state the window. Unknown counts apart, never as failure; people counted, never named.
- Shares the `changes export` evidence pass: missing checklists are built and stored a few at a time, after stderr says how many and how long.

---

### `repowise distill <command>`

Run a command and print a compact, reversible rendering of its output. Guide: [DISTILL.md](../agent/DISTILL.md).

```bash
repowise distill pytest -x
repowise distill git status
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--source` | Hidden ledger surface label; the rewrite hook sets `hook-bash` / `hook-powershell`. On Windows, `hook-bash` runs POSIX syntax in a POSIX shell, not cmd.exe. | `cli` |

- Drops noise (pass parades, progress spam, boilerplate); errors, failures, summaries and exit code survive.
- Dropped content goes to `.repowise/omissions/` behind an inline `[repowise#<ref>: ...]` marker. Any filter problem prints raw output.
- Honors `distill:` in `.repowise/config.yaml` (master switch, disabled filters, omission-store sizing).

---

### `repowise expand REF`

Restore the output behind a `[repowise#<ref>: ...]` omission marker.

```bash
repowise expand a1b2c3d4e5f6 -q "FAILED"
```

| Flag | What it does | Default |
|------|--------------|---------|
| `REF` | 12-hex ref or a whole pasted marker. | |
| `--query` / `-q` | Only lines matching this regex (or substring). | all lines |

Checks the repo's store, then the user-level fallback store.

---

### `repowise saved [PATH]`

Input tokens your coding agent never had to read, and their worth.

```bash
repowise saved --by surface
repowise saved --missed
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--by` | `operation`, `surface`, `agent`, `model`, `day`. | `operation` |
| `--since` | ISO date; rounded up to whole days, that day included. | |
| `--model` | Prices `--missed` estimates only. | latest agent session model, else `claude-sonnet-4-6` |
| `--missed` | Distillable-looking commands not rewritten. | off |
| `--missed-days` | `--missed` window. | `7.0` |
| `--format` | `table` or `json` (table, net and advisories in one document). | `table` |

- Same ledger and report service as the savings endpoint and dashboard overview. Covers `repowise distill`, tool-result hooks, MCP calls.
- *Measured* (known before and after) and *inferred* (exploration replaced) savings are the two parts of the total, shown with it.
- Each event uses its recorded rate; rateless savings count as unpriced, never valued at today's model.

---

### `repowise savings sync [PATH]`

Record savings agents were shown but the ledger never banked.

```bash
repowise savings sync --dry-run
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--dry-run` | Report, write nothing. | off |
| `--budget` | Seconds of transcript reading; raise for a big backlog. | `20.0` |
| `--format` | `table` or `json`. | `table` |

Reads only transcript text appended since the last run. Non-zero `deferred` means the budget ran out; run again.

---

### `repowise corrections [PATH]`

Mine local agent transcripts for recurring command fumbles. See [DISTILL.md](../agent/DISTILL.md#repowise-corrections-recurring-command-fumbles).

```bash
repowise corrections --write
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--days` | Transcript window. | `30` |
| `--write` | Opt-in: maintain the "Known command corrections" block in `.claude/CLAUDE.md` / `AGENTS.md`. | off |
| `--min-count` | Occurrences a rule needs for `--write`. | `2` |
| `--format` | `table` or `json`. | `table` |

A fumble: same base command run consecutively, first failing, a later variant succeeding (wrong tool, path, unknown flag, missing argument). Local, report-only by default.

---

### `repowise costs`

Show LLM spend tracking.

```bash
repowise costs --all --by model
```

| Flag | What it does | Default |
|------|--------------|---------|
| `PATH` / `--repo-path` | Repository path. | cwd |
| `--since` | ISO date. | |
| `--by` | `operation`, `model`, `day`. | `operation` |
| `--repo` | One workspace repo (implies workspace mode). | primary repo |
| `--all` | Every workspace repo. | off |
| `--no-workspace` | Force single repo. | auto-detect |
| `--format` | `table` or `json`. | `table` |

---

### `repowise export [PATH]`

Export wiki pages to files, or the architecture model as Structurizr DSL.

```bash
repowise export --format html -o ./wiki/
repowise export --format structurizr --standalone -o arch/
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--format` | `markdown`, `html`, `json`, `structurizr`. | `markdown` |
| `--output` / `-o` | Output directory; for `structurizr`, a `.dsl` path names the file. | `.repowise/export` |
| `--full` | JSON: add decisions, dead code, hotspots, provenance. | off |
| `--standalone` | `structurizr`: full workspace with default views (renders alone), not a fragment. | off |
| `--components` | `structurizr`: component level, one box per directory. | off |
| `--no-externals` | `structurizr`: omit third-party dependencies. | externals included |
| `--force` | `structurizr`: overwrite a file Repowise did not write (`--standalone` writes `workspace.dsl`, maybe yours). | off |

`structurizr` writes one file (`repowise-model.dsl` by default), not pages. See [Structurizr DSL export](../architecture/structurizr-export.md).

---

## Workspace Commands

Full multi-repo guide: [Workspaces](../scale/WORKSPACES.md).

### `repowise workspace list`

Show workspace repos and their index status.

```bash
repowise workspace list
```

### `repowise workspace add <path>`

Add a repo to the workspace and index it.

```bash
repowise workspace add ../new-service --alias api-gateway
repowise workspace add ../shared --no-index           # register only
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--alias` | Short name. | directory name |
| `--index` / `--no-index` | Run the index pipeline; `--no-index` only registers. | on |
| `--docs` / `--no-docs` | Model writes docs / index only. Ignored with `--no-index`. | docs built from code, no model calls |
| `--provider` / `--model` | Override (`--provider` also means model docs). | primary repo's |
| `--concurrency` | Max concurrent LLM calls. | `10` |
| `--save-key` / `--no-save-key` | Save this run's provider key to the repo's git-ignored `.repowise/.env`, so its MCP server can answer. As in `init`. | on |
| `--verbose`, `-v` | Debug logs. | off |

- Provider, model, embedder and excludes inherit from the primary repo's `.repowise/config.yaml`.
- To make it the default: [`repowise workspace set-default`](#repowise-workspace-set-default-alias).

### `repowise workspace remove <alias>`

Remove a repo from the workspace; files stay.

```bash
repowise workspace remove api-gateway
```

### `repowise workspace scan [PATH]`

Find repos in the workspace directory not yet added.

```bash
repowise workspace scan --yes
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--yes` / `-y` | Add all without prompting. | off |
| `--exclude` | Skip paths matching this glob, relative to the workspace root (e.g. `'test-repos/*'`). Repeatable. | |
| `--verbose`, `-v` | Debug logs. | off |

### `repowise workspace set-default <alias>`

Set the default repo for MCP queries.

```bash
repowise workspace set-default api-gateway
```

### `repowise workspace diagnostics`

Explain the cross-repo contract link count: per-repo provider/consumer counts, unmatched consumers by reason, orphan providers (never consumed).

```bash
repowise workspace diagnostics --repo api --format json
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--repo` | One repo alias. | all |
| `--format` | `table` or `json` (`--json` deprecated alias). | `table` |

### `repowise workspace check`

Architecture lint: declared `conformance:` rules and dependency cycles over the system graph. Exits 1 on any finding (CI gate).

```bash
repowise workspace check --format json
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--breaking` / `--no-breaking` | Also fail on breaking contract changes (a removed endpoint or retyped field another repo still calls), read from the last `repowise update --workspace` report; nothing to declare. `--no-breaking`: your rules only. | on |
| `--format` | `table` or `json` (`--json` deprecated alias). | `table` |

### `repowise workspace metrics [PATH]`

Architecture-complexity metrics over the system graph; run `repowise update --workspace` first.

```bash
repowise workspace metrics --format json
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--format` | `table` or `json` (`--json` deprecated alias). | `table` |

Reports propagation cost (system coupling), the cyclic core (services in circular groups) and a deterministic 1-10
score, from structural edges only (no co-change), with declared conformance violations folded in.

### `repowise workspace impacted-tests <repo:path>...`

List consumer-repo tests worth running for changed provider files.

```bash
repowise workspace impacted-tests backend:app/routers/users.py --target-repo frontend --format list
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--path` | Workspace root. | auto-detect from cwd |
| `--call-depth` | Call graph depth, 1-8. | `3` |
| `--import-depth` | Import fallback depth, 1-3. | `1` |
| `--no-measured` / `--no-inferred` | Drop coverage-backed / graph-inferred picks. | off |
| `--min-confidence` | Min contract link confidence, 0.0-1.0. | `0.0` |
| `--target-repo` | Consumer aliases. Repeatable. | all |
| `--format` | `table`, `json`, or `list` (`repo:test-file` lines). `--json` deprecated alias. | `table` |

Each consumer call site is measured, inferred, none or unresolved; see [Cross-repo test impact](../scale/WORKSPACES.md#cross-repo-test-impact).

## Auto-Sync Commands

All sync methods (hooks, file watcher, webhooks, polling): [Auto-Sync](../scale/AUTO_SYNC.md). `hook install`, `status`
and `uninstall` take an optional `PATH` plus `--workspace`/`-w` (every workspace repo) or `--no-workspace` (single
repo); default auto-detect.

### `repowise hook install`

Install a post-commit hook that runs `repowise update` in the background.

```bash
repowise hook install --workspace --security
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--security` | Add a marked pre-commit block running `repowise security check --staged`: exit 1 (finding at `high`+) blocks, exit 2 or no `repowise` passes. Skip once: `git commit --no-verify`. | off |

### `repowise hook status`

Report whether the post-commit hook and `--security` block are installed.

```bash
repowise hook status
```

### `repowise hook uninstall`

Remove the post-commit hook and any `--security` block.

```bash
repowise hook uninstall
```

### `repowise hook stats`

Per surface: what the Claude Code agent hooks said, whether the agent acted, invocation counts and wall time, from `.repowise/sessions/sessions.db`.

```bash
repowise hook stats --format json
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--format` | `table` or `json` (raw per-surface rows). | `table` |

Notices that ask nothing (stale-read, silent read-after-served) show `n/a`, not a rate.

### `repowise hook backfill`

Replay Claude Code transcripts into the ledger so `hook stats` has pre-upgrade history. Local, single-pass, re-runnable
(firings keyed by text hash); usually one-time, as `repowise update` classifies recent sessions.

```bash
repowise hook backfill --days 30
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--all-projects` | Include this repo's worktrees. | this checkout |
| `--days` | Transcripts modified in the last N days. | all |
| `--reset` | Clear hook-surface rows (never decisions) first; once, after upgrading from a release keyed by hook input, not emitted text. | off |

### `repowise hook rewrite install|uninstall|status`

Manage the Distill command-rewrite hooks (Claude Code + Codex PreToolUse): noisy commands (tests, builds, git
status/log/diff, searches, listings) run as `repowise distill <command>` (errors-first) with no prompt by default
(`permission: allow`).

```bash
repowise hook rewrite install        # writes ~/.claude/settings.json (idempotent)
repowise hook rewrite uninstall      # removes only the repowise entries
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--workspace` / `-w`, `--no-workspace` | Every workspace repo / single repo (all three). | auto-detect |
| `--allow-rule` / `--no-allow-rule` | `install`: seed a `Bash(repowise distill:*)` / `PowerShell(repowise distill:*)` allow rule, needed only under `permission: ask`. | off |

- `install` takes an optional `PATH` and re-enables `distill.commands` where an `init` opt-out turned it off.
- `uninstall` removes the global entries and the AGENTS.md awareness section; repo config stays. Posture (`permission:
  ask | allow`, per-family overrides): `distill.commands` in `.repowise/config.yaml`, see
  [DISTILL.md](../agent/DISTILL.md#configuration).
- **Codex** (`~/.codex` exists): also writes `~/.codex/hooks.json` (Codex ≥ 0.137 only; older versions cannot apply a rewrite) and a hook-free "Output Distillation"
  section in `AGENTS.md`. Codex cannot approve a rewrite, so only `allow` families rewrite; `ask` passes through.
  `status` shows what your build supports
  ([DISTILL.md](../agent/DISTILL.md#3-the-command-rewrite-hook-claude-code--codex)).

### `repowise hook read-skeleton|search-digest|read-reread`

Toggle the Claude Code read and search surfaces per repo. Each has `install`, `uninstall`, `status`, optional `PATH`, `-w`/`--no-workspace`.

```bash
repowise hook read-reread status -w
```

| Group | Config key | What `install` turns on |
|-------|------------|-------------------------|
| `read-skeleton` | `hooks.read_skeleton` | Unbounded Read of a large indexed file returns signatures, bodies elided with the line range to read each; a second Read returns it whole. |
| `search-digest` | `hooks.search_digest` | A grep over many files returns per-file match counts and anchor lines plus a dropped-tail count; single-file `-C`/`-A`/`-B` untouched. |
| `read-reread` | `hooks.read_reread` | Re-reading an unchanged, unedited file returns a notice naming the earlier read; changed content comes in full, flagged; reading again returns content. |

- No hook to install: `init`'s PostToolUse hook carries all three; these set the verdict in `.repowise/config.yaml` (`init` sets it from `--distill-hook`).
- `uninstall` returns whole results (the digest rides alongside again). `status` says on/off and, when off, points to `repowise saved`. With no `.repowise/` it asks for `repowise init`.

### `repowise hook health-gate install|uninstall|status`

Opt-in, off by default: Claude Code's `git commit` and `git push` in this repo run
[`repowise health check`](#repowise-health-check-revspec) first and are denied when the gate fails (exit 1). Optional
`PATH`, `-w`/`--no-workspace`.

- `install` sets `hooks.health_gate: true` in `.repowise/config.yaml` and adds a PreToolUse entry to the repo's
  `.claude/settings.local.json`; `uninstall` (and `hook uninstall`) removes both. `init` and `upgrade` never write either.
- A commit is checked on the staged change, including `git add` earlier in the same command and `-a`; a push on
  `@{u}...HEAD`, else the default branch's merge-base. `--amend`, pathspecs, merges in progress, other branches, tags,
  `--delete`, and a `git add` after another command in the chain pass
  unchecked, as does anything that keeps the check from finishing within 20 s.

## Utility Commands

### `repowise mcp [PATH]`

Start the MCP server. Without `PATH`, walks up from cwd to the nearest `.repowise` repo (so Codex config can use `args = ["mcp"]` with `cwd` at the repo root).

```bash
repowise mcp --transport sse --port 7338
repowise mcp --tools "+get_dependency_path,-get_dead_code"
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--transport` | `stdio` (editors), `streamable-http` (HTTP clients), or `sse` (legacy). | `stdio` |
| `--port` | HTTP/SSE port. | `7338` |
| `--host` | HTTP/SSE bind; `0.0.0.0` without `REPOWISE_API_KEY` warns that every tool is unauthenticated and network-accessible. | `REPOWISE_HOST`, else `127.0.0.1` |
| `--tools` | Comma list = allowlist; `+`/`-` adjust defaults; `lean` selects the six-tool agent-lean profile. Overrides config. | `mcp.tools` config block |
| `--all` | Every tool, including opt-in and workspace. | off |
| `--no-workspace` | Nested repo ignores the enclosing workspace's default. | auto-detect |

Tool list: [MCP Tools](../agent/MCP_TOOLS.md).

### `repowise generate-claude-md [PATH]`

Generate `CLAUDE.md`; `init` and `update` keep it current. Text above the Repowise markers is kept; between them updates from the index.

```bash
repowise generate-claude-md --stdout
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--output` | Custom path. | `.claude/CLAUDE.md` |
| `--stdout` | Print, don't write. | off |
| `--workspace` / `-w`, `--no-workspace` | Workspace file at the root (contracts, co-changes, per-repo summaries) / single repo. | auto-detect |
| `--verbose` / `-v` | Debug logs. | off |

### `AGENTS.md`

`repowise init --codex` writes a managed `AGENTS.md`; `repowise update` refreshes it with `editor_files.agents_md` or `--agents`. Text outside the markers is kept.

### `repowise agents`

Wire agents after `init`: Claude Code, Codex, VS Code, Cursor, OpenCode, Hermes, GitHub Copilot CLI, Kiro, and any host
via `print-config`. With no subcommand, lists each agent for the cwd repo: tier, installed or not, every wiring.

```bash
repowise agents --format json
repowise agents add [PATH] --target=opencode --scope=project
repowise agents remove [PATH] --target=hermes --scope=user
repowise agents refresh [PATH]
repowise agents print-config claude-code [PATH]
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--format` | Listing and `add`/`remove`/`refresh`: `table` or `json` (writes: every file and outcome). | `table` |
| `--target` | `add`/`remove`: comma-separated ids or `auto`/`all`/`none`. Required for `remove` (destructive). | `add`: prompts, installed pre-ticked |
| `--scope` | `add`/`remove`/`refresh`: `project` (repo), `user` (machine), `both`. | `both` |
| `--yes` / `-y` | `add`: never prompt. | off |
| `print-config --scope` | `project` or `user` snippet. | `project` |
| `print-config --format` | `table`: bare paste-ready snippet; `json`: with target, scope, repo, docs URL. | `table` |

- `refresh` only repoints wired agents (after an upgrade or repo move), never adds, so `repowise doctor --repair` calls it.
- `print-config` prints the MCP entry, writing nothing, for hosts like Cline, Windsurf, Zed, Gemini CLI.

Support matrix: [Agent integrations](../agent/INTEGRATIONS.md).

### `repowise reindex [PATH]`

Re-embed all wiki pages; embedding calls only, no LLM. A full run with a real embedder marks semantic search available again, clearing a failed `init`/`update` record.

```bash
repowise reindex --embedder gemini --batch-size 50
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--embedder` | `gemini`, `openai`, `openrouter`, `ollama`, `edenai`, `mock`, `auto`. | `auto` |
| `--batch-size` | Batch size. | `32` |

### `repowise doctor [PATH]`

Health-check the setup.

```bash
repowise doctor --workspace --repair
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--repair` | Rebuild FTS, re-embed missing pages, sync workspace drift, drop dead workspace entries. | off |
| `--workspace` / `-w`, `--no-workspace` | Workspace table (dir exists, git repo, state.json ↔ config drift) then per-repo checks / single repo. | auto-detect |
| `--format` | `table` or `json`. | `table` |

- **Vector store** fails when `.repowise/lancedb` won't open, naming the error. An open store with no indexable pages
  reports all missing; `--repair` won't re-embed it with a paid embedder, it prints the count and points to `repowise
  reindex`.
- **`CLI version`** (best-effort, vs PyPI): upgrade command (`uv tool upgrade repowise`, `pipx upgrade repowise`,
  `python -m pip install -U repowise`), the `repowise` on `PATH` and the launching command. Never updates, never fails
  offline. After upgrading, **restart Claude/Codex/Cursor or any MCP client**.
- **Distill:** validates `distill:` (unknown keys, bad permission values, unknown filters, non-positive store sizing);
  shows omission store size vs cap and whether the opt-in rewrite hook is installed (absence passes).

### `repowise whats-new`

Release notes since you last looked, then marks the current version seen. Offline, from the bundled changelog.

```bash
repowise whats-new --version 0.21.0
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--version X.Y.Z` | One release. | unseen releases |
| `--all` | Full history. | off |
| `--format` | `table` or `json` (`summary`, `highlights`, `stats` per release). | `table` |

- Each release: highlights and a "By the numbers" line (pull requests, contributors, files and lines changed). The panel caps at 5 releases, 8 bullets each.
- `repowise update` shows a short panel after an upgrade; `update` and `serve` print a non-blocking line when PyPI is newer. See [docs/reference/UPGRADING.md](UPGRADING.md).

### `repowise telemetry`

Inspect and control anonymous, opt-out usage telemetry.

```bash
repowise telemetry status                # enabled or not, and why
repowise telemetry disable               # or: enable
```

### `repowise plugins`

List plugins from `repowise.plugins` entry points and `REPOWISE_PLUGINS`: loaded or why not, seams registered,
implementation per seam. Plugin commands load only for an unknown command name or a command listing.

```bash
repowise plugins --format json
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--format` | `table` or `json`. | `table` |

See [pluggable storage and capability seams](../architecture/pluggable-storage.md).

### `repowise attribution`

Inspect and clear AI attribution. Settings: [CONFIG.md](CONFIG.md#the-attribution-block) `attribution:`.

```bash
repowise attribution status [PATH] [--format json]   # what is on, capture per agent, coverage, last backfill
repowise attribution backfill [PATH] --agent claude-code,codex [--format json]
repowise attribution purge [PATH] [--yes]
```

- **`backfill`** (on request; agents both `capture` and `agents` allow): hashes lines of edits to this repo (worktrees
  too) from local Claude Code/Codex transcripts, binds them to commits within 14 days after, prints commits scanned,
  bound, coverage before/after. The next `update` re-reads git history once to label them. First run reads all
  transcripts (about 4 minutes for 7 GB on a laptop), later runs only new ones.
- **`purge`** deletes index labels, `.repowise/attribution/` and git-ai notes Repowise wrote; history, trailers and others' notes stay.

### `repowise config hints on|off`

Toggle the repowise.dev hint: one dim stderr line with a link at a helpful moment (finished `init`, slow `update`); max
one per run, each once a week. Never with `--format json`, in CI, non-terminal stderr, signed in, or from `mcp`,
`serve`, `watch`, hooks.

```bash
repowise config hints off       # also: REPOWISE_NO_HINTS=1 or DO_NOT_TRACK=1
```

### `repowise login`

Sign in to repowise.dev (browser OAuth with PKCE; tokens in `~/.repowise/credentials.json`). Not an LLM key; adds hosted
repos, reindex from local tools and account status in `doctor`. Local features never need it.

```bash
repowise login --with-token
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--with-token` | Paste an API key (`rw_live_...`): stdin when piped, else prompt. For SSH/headless. | off (browser) |
| `--device-name` | Machine label in connected apps. | hostname |

### `repowise logout`

Sign out here: best-effort server revocation, local credentials always removed.

```bash
repowise logout
```

### `repowise whoami`

Show the signed-in account.

```bash
repowise whoami
```

### `repowise publish [PATH]`

Have repowise.dev index the GitHub `origin` (nothing uploads: pushed work only; no GitHub remote, no publish). Signs in
first if needed. About 10 minutes; prints (and opens) the indexing page, plus the repo page and MCP address for
Claude.ai or ChatGPT.

```bash
repowise publish --ref release --no-open
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--ref` | GitHub branch or tag. | current branch if pushed, else default |
| `--no-open` | Don't open the browser. | off |
| `--format` | `table` or `json` (one object, never a browser sign-in; signed out = `signed_out`). | `table` |

Refusals say why and link the fix: private repo on free → repo page (Pro trial, 10 days free, card required, plus GitHub
App install); paid without the App → install link; free 2-repo limit → trial checkout.

### `repowise delete [REPO_ID]`

Delete a repo's index and stored intelligence (wiki, graph, embeddings, git metadata); source files untouched. Works on a shared `REPOWISE_DB_URL` database with no local `.repowise/wiki.db`.

```bash
repowise delete --path /workspace/api -f
```

| Flag | What it does | Default |
|------|--------------|---------|
| `--force` / `-f` | No confirmation. | prompts |
| `--path` / `-p` | Pick the repo whose stored `local_path` matches (no numbered choice on shared PostgreSQL). | list all, prompt |

### `repowise uninstall [PATH]`

Remove what repowise wrote; report what stays and why. Wider than `delete` (index rows only). Terminal, no flags: shows the inventory and asks. The four groups are chosen independently; without a terminal, name what to remove (`--all`, `--keep-index`).

```bash
repowise uninstall --keep-index          # drop the wiring, keep the index
repowise uninstall --all --dry-run --format json
```

| Group | What it covers |
|------|-------------|
| `agents` | Every wired agent, both scopes, via each target's uninstall |
| `repo-files` | Managed blocks in `.claude/CLAUDE.md`, `AGENTS.md` |
| `index` | The repo's `.repowise/` |
| `global` | `~/.repowise/`: login, caches, telemetry preference |

| Flag | What it does | Default |
|------|--------------|---------|
| `--all` | Everything, including index and machine-wide state. | off |
| `--keep-index` | Reinstall case: wiring and generated blocks only. | off |
| `--dry-run` | Plan only. | off |
| `--format table\|json` | `json`: plan, every path, outcome. | `table` |

- **No `--yes`:** a partial uninstall is no safe default. No terminal and no scope: prints inventory, removes nothing, exits non-zero.
- **Never pre-selected:** the index (costly to rebuild), `~/.repowise/` (shared by every repo). **Never touched:** the
  package (pip, uv, pipx) and Claude Code plugin (`/plugin uninstall`); it prints each command.
- One agent only: [`repowise agents remove --target=<id>`](../agent/INTEGRATIONS.md).

**Exit codes** (each refusal prints its reason on the row):

| Code | Meaning |
|------|---------|
| `0` | All selected removed. |
| `1` | A removal failed. |
| `3` | A removal was refused; something remains. |
| `4` | No scope and nobody to ask. |

### `repowise augment`

Context enrichment run by `init`'s Claude Code and Codex hooks, not by hand.

```bash
repowise augment --client codex          # payload on stdin
```

| Flag | Meaning | Default |
|---|---|---|
| `--client` | `codex` or `cursor`, passed by those hooks. | Claude Code |
| `--coverage-only` | Only the shell-tool coverage re-ingest (repo-local hook). | off |
| `--verbose`, `-v` | Debug logs. | off |

- **Claude Code:** search enrichment, stale-wiki checks, decisions: relevant standing decisions at session start
  (ranked, hard token cap, silent below the floor), one "governed by" line per decision per session on editing a
  governed file.
- **Codex:** `SessionStart` and `PostToolUse` guidance.
- Shown decisions land in `.repowise/sessions/sessions.db`; the next `update` judges followed or contradicted and adjusts staleness.

### `repowise-augment` / `repowise-rewrite`

Console scripts installed with the CLI, not subcommands, not run by hand: `repowise-augment` is the import-isolated augment hook entry; `repowise-rewrite` backs `repowise hook rewrite install`.
