# Upgrading repowise

repowise is built so that upgrading is painless: **upgrade the package, run `repowise update`, and your existing index keeps working.** A full reindex is only ever *recommended* for a genuinely breaking change, never forced, and repowise tells you exactly when and how.

## The short version

```bash
pipx upgrade repowise      # or: uv tool upgrade repowise / pip install -U repowise
cd your-repo
repowise update            # picks up the new version against your existing index
```

That's it. No reindex in the normal case.

## What happens on upgrade

When you run `repowise update` (or `repowise serve`) after upgrading, repowise:

1. **Reads your store's format version** and compares it to the running build. The on-disk store records which version wrote it, so repowise knows exactly what, if anything, an upgrade needs.
2. **Applies any automatic, no-cost adjustments in place.** New database columns are back-filled automatically. If your embedding model changed, vectors are re-embedded for you (no LLM calls). You don't run anything.
3. **Keeps your parse cache warm.** Ordinary releases do not invalidate cached work, so updates stay fast.
4. **Shows you what changed.** A short "what's new" summary appears for the versions you crossed, and `repowise whats-new` shows release notes any time. The web UI surfaces a dismissible "update available" banner that names the newer release's highlights, and after an upgrade it opens What's new once for a minor release (a short note for a patch), with the release's highlights, its numbers and every change.

## When a reindex is recommended

Rarely, a release changes the store format in a way an in-place migration can't cover. When that happens repowise **keeps your existing index working** and shows a clear notice with the exact command, for example:

```
Reindex recommended: repowise init --force
```

It is a recommendation, not a requirement. Nothing is wiped until you choose to run it. You can keep using the current index and reindex when convenient.

## Behaviour changes worth knowing about

### 0.63.0: the litellm provider installs separately

**Only if your provider is `litellm`.** The `litellm` package moved out of the
default install into the `repowise[litellm]` extra, so a fresh install or a
rebuilt environment no longer has it. Every other provider is unaffected. When
it is missing, the first command that needs a model says so and, in a
terminal, offers to install it; `repowise doctor` shows the exact command for
how repowise was installed. To keep it in one step when you upgrade:

```bash
uv tool install "repowise[litellm]"     # uv tool: later upgrades keep it
pipx inject repowise litellm            # pipx: later upgrades keep it
pip install -U "repowise[litellm]"      # pip
```

### 0.62.0: health, the parse cache and ownership refresh once

**Your first `update` does the rest once, on its own.** It re-scores health (analyzer 45 to 46), refreshes the parse cache (parser schema 13 to 14) and walks git history once to recompute ownership (git history 4 to 5) and rescans security (scanner 6 to 7). A workspace update re-extracts contracts once (contracts 16 to 17). Database migrations 0124 to 0140 apply automatically. Nothing needs re-indexing: the store format is unchanged.

**What you may notice:**

- Performance cause ids move to the v4 model. Until that first `update`, performance counts read 0 with `stale_model`.
- Bot accounts leave file ownership and the contributor count, and one person counts once across names and emails, so contributor counts can go down.
- Code a data folder holds for the product to read by path gets its own origin, `data`, and leaves dead code, the queues, the area map and security headlines.
- `repowise search` defaults to auto mode and the MCP tool's default limit.
- Runtime evidence is opt in: `repowise evidence setup` prints what can be collected here and how to ingest it.

### 0.61.0: decision mining and the MCP tool list are opt in

**Your first `update` re-scores health and re-detects communities once.** Health re-scores (analyzer 44 to 45), the parse cache refreshes (parser schema 12 to 13) and a workspace update re-extracts contracts once (contracts 15 to 16). Database migrations 0122 and 0123 apply automatically. Nothing needs re-indexing.

**Defaults that change:**

- `repowise update` makes no decision model calls unless `decisions.update_mining` is on, and session start lists decisions only with `decisions.session_start` on. `repowise decision config` sets both.
- MCP clients other than Claude Code get the core tool list and the server instructions that match it. `repowise config set mcp.tools standard` (or any list) gives them the wider set.
- `mcp.always_load` is off; `repowise config set mcp.always_load true` turns it on for Claude Code.
- Health markers judged under 80% precision are advisory in the languages where they missed: `low_cohesion` is advisory everywhere, `dry_violation` scores only in TypeScript and `error_handling` only in Python. Scores held down by those markers can rise.

### 0.60.0: the wiki rebuilds once in its new shape

**Your first `update` rebuilds the wiki once** (store format 2 to 3). It is free for a wiki built from code, priced and confirmed for a model-written wiki, and deferred to `repowise update --yes` when there is no terminal. A rebuilt wiki is usually leaner: a symbol gets its own page only when its production callers make it worth one, at most eight per file, and more files get their own file page.

The same update re-scores health (analyzer 43 to 44), rescans security (scanner 5 to 6), refreshes the parse cache (parser schema 10 to 12) and walks git history once (git history 3 to 4). Database migrations 0119 to 0121 apply automatically. Nothing needs re-indexing.

**What you may notice:**

- Key Concepts and Glossary pages redirect to the overview, and that update removes them. Their config keys warn and are ignored.
- The first ask of each cached `get_answer` question is synthesised again.
- A Claude Code MCP entry written by 0.59 moves to `~/.claude.json` the next time `init`, `agents refresh` or `doctor --repair` runs.
- `repowise mcp --transport sse` prints a deprecation notice. Use `--transport streamable-http`.
- `repeated_nesting` is advisory: listed, with no deduction.

### 0.59.0: health and security refresh once, and decision prompts are on

**Your first `update` re-scores health and rescans security.** Health re-scores (analyzer 41 to 43), the parse cache refreshes (parser schema 9 to 10) and security rescans (scanner 4 to 5). Nothing needs re-indexing.

**What you may notice:**

- For Claude Code, a prompt to record the decision a commit made is on: a PostToolUse entry on Git commands, and the prompt itself fires only on `git commit`. `repowise decision config capture-prompt --off` turns it off per repository.
- Agents can accept decision candidates signed as themselves. `repowise decision config agent-acceptance --off` keeps acceptance for people.
- Accepting a decision requires decision text: pass it with `repowise decision confirm --decision TEXT`.
- `repowise serve` opens the web UI in your browser from a terminal. `--no-open` or `REPOWISE_NO_OPEN=1` turns it off.
- `repowise coverage add` stores nothing from a report that mostly does not map unless you pass `--allow-partial`; it suggests the path prefix that maps it.
- A repository's excluded paths and wiki style live in `.repowise/config.yaml`, written by the CLI, the server and the web settings form alike.

### 0.58.0: health scoring saturates per category

**Your first `update` re-scores the whole repository** (analyzer 40 to 41) and refreshes the parse cache once (parser schema 8 to 9). Nothing needs re-indexing.

**What you may notice:**

- Each defect category's deduction follows a saturating curve, and findings on one function count the largest in full and the rest at a quarter. Scores move on that first update.
- `bumpy_road` is `repeated_nesting`. The old id keeps working in `health-rules.json`, findings and filters.
- `brain_method` is listed without a deduction, since the size markers already charge that function.
- Init, status, update and the editor files name the modes Built from code, Written by <model> and Index only. Flags, state and JSON values are unchanged. `repowise update --full` takes a fast index to a full one.

### 0.57.0: a wider git window and one provider order

- **Git history reads the newest 5,000 commits by default.** An existing index keeps the `commit_limit` its `.repowise/config.yaml` records (`init` wrote 500 before 0.57), and `update` names that value once. Set `commit_limit: 5000` there to read the wider window.
- **One provider order everywhere.** Provider, model and base URL resolve as flag or UI choice, then `.repowise/config.yaml`, then the web UI's global default, then `REPOWISE_PROVIDER` / `REPOWISE_MODEL`. If you set the provider with those variables and `config.yaml` names one, the file decides. A base URL in `config.yaml` takes precedence over `OPENAI_BASE_URL` and the other `*_BASE_URL` variables.
- **Provider defaults.** DeepSeek defaults to `deepseek-flash` and LiteLLM to `groq/openai/gpt-oss-120b`.
- **Renamed fields.** `get_answer` moves `retrieval_quality` behind `include=["diagnostics"]`. `/commits/agent-trend` returns `coverage_pct` and `agent_pct_of_covered` in place of `agent_pct`, and `get_context` ownership carries `agent_share` in place of `agent_authored_pct`.

### 0.56.0: git history is re-read once, and `search_codebase` drops `file`

- **Your first `update` walks the whole git history once,** with no page generation and no model calls.
- **`search_codebase` rows carry `path` only.** The `file` field, deprecated in 0.55, is removed. Read `path`.
- **`anthropic` and `claude_cli` default to `claude-haiku-5-5`.** Pass `--model` to keep another model.
- **`get_risk` replies are leaner.** `structural_impact_score`, `overall_risk_score` and `may_break_tests` are dropped by default; `pr_blast_radius` comes with `include=["blast"]` and detailed owner metrics with `include=["owners"]`.
- **Bare `repowise health` reads the stored analysis** and writes nothing. `--recompute` runs the in-process analysis.
- **Import cycles count only imports that run.** Type-only imports, imports under `TYPE_CHECKING` and imports inside function bodies are left out, so cycle counts can drop, and `break_cycle` plans are advisory.

### 0.55.0: leaner MCP replies, and unanalysed files carry no score

- **`search_codebase` rows name their file as `path`.** `file` stays as a deprecated alias for one minor release (removed in 0.56).
- **MCP results are compact JSON with a lean `_meta`.** `MCP_CONTRACT_VERSION` moves to 3. `REPOWISE_MCP_DEBUG_META=1` restores the diagnostics and `REPOWISE_MCP_PRETTY_JSON=1` restores indented text.
- **`get_risk` owner metrics** (`owner_pct`, `owner_line_pct`, `recent_owner`, `recent_owner_pct`, `bus_factor`, `contributor_count`) move behind `include=["owners"]`.
- **Files in languages health cannot analyse store no score.** They are left out of averages, hotspot health, band counts and trends, so a repository average can move.
- **`repowise dead-code` and `health --refactoring-targets` read the stored results** when the index is at HEAD. `--recompute` runs live analysis for refactoring targets.
- **`update` re-scores and re-parses an unchanged checkout** when the health analyzer or the parser changed since the index was built.

### 0.54.0: health re-scores once, and some git numbers move

**Your first `update` re-scores the whole repository.** The health analyzer version moved from 31 to 36, so the first update after upgrading re-scores every file and takes longer than usual. The parser schema moved from 3 to 5 (Python import resolution and overload declarations), so that same update re-parses files instead of reading them from the parse cache. Nothing needs re-indexing: the store format is unchanged.

**Some scores and counts change on that first update, by design:**

- History windows are measured from the indexed commit, not the wall clock, so re-indexing an unchanged tree on a later day gives the same scores. Set `REPOWISE_GIT_WINDOW_ANCHOR=now` to keep the wall-clock behaviour.
- A file with no code finding loses at most one point to git history, and `hidden_coupling` is still listed but no longer lowers the score. Files that were held down only by history can score higher.
- "Commits" counts non-merge commits everywhere, and one person's noreply address, handle and local email merge into one contributor. Commit and contributor counts can go down.
- Docs, config, workflow and data files gain commit history on the first update. They stay out of hotspot rankings.
- Entry points are read from the package manifest first, so the entry-point list and the dead-code findings that depend on it can shift.
- The full-text search index is widened and refilled the next time the index is opened. It happens once and needs no action.

### 0.53.0: health re-scores once, and workspace contracts re-extract

**Your first `update` re-scores the whole repository.** The health analyzer version moved from 21 to 31 over this cycle: performance plans now claim only what the loop proves, two new performance markers landed, and Pascal gained loop, performance and assertion coverage. Each of those changes how a stored health number is computed, so the first update after upgrading re-scores every file and takes longer than usual. The next one is back to normal.

**Workspace contracts are re-extracted on the next workspace update.** The contract format moved from version 8 to 13 (Laravel, NestJS, Node clients and ORMs, Angular, queues, sockets and RabbitMQ bindings), so `repowise update --workspace` rebuilds the stored contract maps instead of reusing them.

Nothing needs re-indexing: the store format and parser schema are unchanged. The PHP tree-sitter query did change, so that same first update re-parses files instead of reading them from the parse cache. It happens once and needs no action.

### 0.52.0: decision capture is stricter, and health re-scores once

Three changes you may notice after upgrading to 0.52.0.

**Transcript mining is off by default.** Decisions are no longer mined from coding-agent transcripts unless you ask for it. The lane was binding records to files the repository does not have, so it now stays quiet until you turn it on:

```bash
repowise decision source set session --on
```

**A decision needs a stated reason to be accepted.** `repowise decision confirm` refuses a record whose body only restates its own title. Existing records are untouched, but ids that used to pass may now be rejected; add a reason with `repowise decision add --rationale`, or re-record it.

**Your first `update` re-scores the whole repository.** The health analyzer version moved from 11 to 21 over this cycle, because new test-quality markers, a per-language assertion vocabulary, a `.tsx` grammar fix and re-ranked history gates all change how a stored health number is computed. Instead of waiting out the decay timer, the first update after upgrading re-scores every file, so it takes longer than usual. On a 171-file repository that was 10.2 seconds against a 5.8 second baseline, with the next update back to normal. Nothing needs re-indexing: the store format and parser schema are unchanged.

### Git history is re-read once when its rules change

When a release changes how git history becomes stored rows (change numbers, author identity), the first `update` re-reads the full history once instead of only the changed files. It regenerates no pages and makes no model calls. See [GIT_HOSTS.md](../start/GIT_HOSTS.md#upgrading).

### `health-rules.json` globs now match like `.gitignore`

Per-path rules in `.repowise/health-rules.json` used to be matched with
`fnmatch`, where `*` crossed directory separators. They now use the same
gitignore matching as `exclude_patterns`, `.gitignore` and the file traverser,
so one glob means one thing wherever you write it.

**What changes for you:** a single `*` stops at a path segment. A rule written
as `src/legacy/*` used to silence the whole subtree and now covers one level,
which means biomarkers you had turned off can start firing again.

**The fix is one character:**

```diff
- {"path": "src/legacy/*",  "disabled_biomarkers": ["complex_method"]}
+ {"path": "src/legacy/**", "disabled_biomarkers": ["complex_method"]}
```

You do not have to hunt for them. Any affected pattern logs a warning naming
the pattern when the file is loaded:

```
health_rules_glob_narrowed pattern=src/legacy/* hint='*' no longer crosses '/' …
```

Patterns without a `/`, patterns already using `**`, and directory prefixes
like `vendor/` are unaffected.

## Checking your version

- CLI: `repowise --version`, or `repowise doctor` for an update check with the right upgrade command for your install method.
- Web UI: the version is shown in the sidebar footer, with a dot when a newer release is available.

## See also

- `docs/CHANGELOG.md` - full release notes.
