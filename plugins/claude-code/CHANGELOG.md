# Changelog

All notable changes to the Repowise Claude Code plugin are documented here.

## 0.48.0

### Changed
- Version bump only. No command, skill or doc change this cycle: the CLI flags
  each command names are unchanged, `hooks.json` still mirrors
  `claude_config.py`, and every tool named in a command or skill is one the
  server lists. `generate_refactoring_code` is live and deliberately unreferenced.

## 0.47.0

### Changed
- The `change-review` skill leads with `get_change_risk`'s `directive` and
  `health_delta` — what the change made worse — before the percentile and the
  diff-shape drivers, matching the reordered response (#1980). It also names
  `directive.status: unknown` alongside `warning` as the "matched no files"
  signal, and distinguishes the per-change directive from `get_risk`'s per-file
  one.
- No hook change this cycle: `hooks.json` still mirrors `claude_config.py`, and
  every tool named in a command or skill is one the server lists.

## 0.46.0

### Changed
- The `change-review` skill reads `directive.may_break` and
  `directive.may_break_tests`. `get_risk` renamed both fields: the old names
  asserted a certainty the structural heuristic behind them does not have
  (#1892).
- `/repowise:risk`, `/repowise:impacted-tests`, `/repowise:security` and the
  plugin README state the public risk scale the way the tool now reports it,
  and describe fix density as ranked against commits rather than against
  individual files (#1891, #1914).
- No hook change this cycle: `hooks.json` still mirrors `claude_config.py`, and
  every tool named in a command or skill is one the server lists.

## 0.45.0

### Changed
- `/repowise:impacted-tests` and the `change-review` skill describe the map as
  it now works: with no coverage ingested the candidates come from the call
  graph, with the import graph filling in only where it is silent, and every
  candidate carries the `via` marker saying which tier answered. Only
  `via: coverage` proves a test executed the change (#1749, #1755, #1757).
- `/repowise:init` and `/repowise:reindex` list the embedders the CLI actually
  accepts - `gemini`, `openai`, `openrouter`, `ollama`, `edenai` - rather than
  the three-name set they had drifted to, and name the keys that resolve them
  (#705, #1820).
- No hook or MCP tool surface change this cycle: `hooks.json` still mirrors
  `claude_config.py` and every tool named in a command or skill is one the
  server lists.

## 0.44.0

### Changed
- Version bump to track the 0.44.0 release. No command, skill, hook or MCP tool
  surface changed this cycle: the live `list_tools()` set is unchanged, no
  `@click.option` moved on any documented command, and `hooks.json` still
  mirrors `claude_config.py`.

## 0.43.0

### Changed
- `/repowise:risk` documents what the command now leads with: the bug-fix
  history of the files a change touches, with the 0-10 score reported beside it
  as a measure of diff size and spread rather than a verdict on danger. Also
  records that omitting the revspec scores uncommitted work when the tree is
  dirty, and adds `--baseline` and `-t/--target` (#1583, #1593).
- `/repowise:init` describes `--no-editor-setup` as what it now does: it skips
  the four project-local files (`.mcp.json`, `.claude/CLAUDE.md`,
  `.vscode/mcp.json`, `.vscode/extensions.json`) as well as the machine-wide
  registration, so only `.repowise/` is touched. Adds `--save-key` /
  `--no-save-key` (#1572, #1595).
- `/repowise:decision` documents the flag-driven form of `decision add`, which
  records without prompting once `--title` and `--decision` are both present and
  is the form to use with no terminal (#1566).
- `/repowise:why` documents that `--target` with no question now answers about
  those files instead of falling through to the health dashboard, and that a
  question the store cannot answer returns a redirect rather than the closest
  records (#1558, #1566).

## 0.42.0

### Changed
- The six skills now render from a source shared with the Codex plugin rather
  than being maintained as two hand-kept copies that had already drifted in
  wording, headings and one directory name. A drift report fails when the two
  hosts diverge (#1450).

## 0.41.0

### Added
- `/repowise:ask`, `/repowise:context`, `/repowise:symbol`, `/repowise:why` and
  `/repowise:export`: slash commands for the remaining CLI adapters, so Claude
  can synthesise answers, pull triage cards, read live-verified symbol bodies,
  query decisions and archaeology, and export the wiki or a Structurizr model
  without an MCP-only flow (#1428).

## 0.40.0

### Added
- `/repowise:security` runs the full-history secret scan. Working-tree scanning
  already happens during `init` and `update`; this command is for walking git
  history to find secrets that were later removed. No model call, and re-runs
  are idempotent (#1107).

### Changed
- The `PostToolUse` matcher no longer selects `Bash` or `PowerShell`. Across one
  287-session corpus those were 51% of all hook invocations and 0.7% of the
  emissions, and the cost is process start, paid before repowise reads the
  payload. An installed machine narrows itself on the next CLI invocation
  without a re-init (#1382).
- A `PostToolUseFailure` entry is registered, matching
  `Read|Edit|Write|Grep|Glob|NotebookEdit`. When a failed path's basename
  resolves to exactly one indexed file still on disk, repowise names it (#1336).
- Version bump to track the repowise 0.40.0 release. The MCP tool surface is
  unchanged; the CLI flags the commands document still match the shipped CLI.

## 0.39.0

### Changed
- Version bump to track the repowise 0.39.0 release. The MCP tool surface, the
  CLI flags the commands document and the hook matchers in `hooks/hooks.json`
  are unchanged this cycle; the parity check found no drift. `get_answer`
  gained a field in its response payload (#1306), which no command or skill
  documents.

## 0.38.0

### Changed
- Version bump to track the repowise 0.38.0 release. The MCP tool surface, the
  CLI flags the commands document and the hook matchers in `hooks/hooks.json`
  are unchanged this cycle; the parity check found no drift.

## 0.37.0

### Added
- `/repowise:init` documents `--no-editor-setup` (and the matching
  `REPOWISE_SKIP_EDITOR_SETUP=1`), which skips global MCP and hook
  registration. The codebase-exploration skill points at it for scratch
  clones, fixtures and worktrees (#1086).

### Changed
- Version bump to track the repowise 0.37.0 release.
- The augment hook command in `hooks/hooks.json` guards on the console script
  being present, so a shell that cannot find `repowise-augment` stays silent
  instead of reporting a failure on every matched tool call (#1141).
- `/repowise:dead-code` re-synced with the CLI's `--min-confidence` default,
  now anchored to `RISK_CAP_CONFIDENCE` (#1087).
- The code-health skill reflects the `get_health` response surface: targets
  that cannot be resolved are reported rather than dropped, and the response
  no longer repeats itself (#1142).
- `DEVELOPER.md` covers the no-editor-setup path.

## 0.36.0

### Changed
- Version bump to track the repowise 0.36.0 release.
- `/repowise:health` dropped `--safe-only`, which never did anything on
  `health`. The flag remains live on `dead-code` (#1027).

## 0.35.0

### Changed
- Version bump to track the repowise 0.35.0 release.
- `init`, `status` and the reader docs follow the one-renderer wiki model:
  `--prose` / `--no-prose` is the single wiki-spend switch, `init` can start
  keyless from structure, and any page upgrades to model prose later with
  `repowise generate`. The deprecated `--index-only` / `--docs` aliases are no
  longer written into new guidance.
- MCP tool surface and hooks docs re-synced with the server (#1017).

## 0.34.1

### Added
- `/repowise:coverage` — ingest or inspect coverage reports (`coverage add` /
  `coverage status`).
- `/repowise:impacted-tests` — map a commit / range / staged diff to the tests
  that exercise changed lines.

## 0.34.0

### Changed
- Plugin docs: MCP tool surface is the **ten flagship tools** (including
  `get_change_risk`) plus `list_repos`; hooks docs now match bundled
  `SessionStart` + full `PostToolUse` matcher.
- Version bump to track the repowise 0.34.0 release.
- `pre-modification` skill reads the new `defect_profile` block on `get_risk`
  (fix count, last fix age, `bug_magnet`, `top_symbols`) and leads with it.
- `init`, `update`, and `health` commands document `-v, --verbose`; `init` and
  `update` are now quiet by default.

## 0.33.0

### Added
- `change-review` skill for reviewing a change against the risk and blast-radius
  tools (shipped for both the Claude Code and Codex plugins).

### Changed
- Version bump to track the repowise 0.33.0 release.
- `risk` command documents the new `-x/--exclude` flag and `.riskignore` support.

## 0.32.0

### Changed
- Version bump to track the repowise 0.32.0 release. No command, skill, or MCP
  tool-surface changes this cycle.

## 0.31.0

### Changed
- Version bump to track the repowise 0.31.0 release. No command, skill, or MCP
  tool-surface changes this cycle.

## 0.30.0

### Added
- Bundled `SessionStart` hook (`repowise-augment`, matcher `startup|resume|clear`):
  injects a short context block at session start with live index freshness
  (current / behind with a changed-file count / update in progress) and the
  core-tool trust rule, matching the CLI-installed hook.

## 0.27.0

### Changed
- Version bump to track the repowise 0.27.0 release. No command, skill, or MCP
  tool-surface changes this cycle.

## 0.26.0

### Changed
- Version bump to track the repowise 0.26.0 release. No command, skill, or MCP
  tool-surface changes this cycle.

## 0.25.0

### Changed
- Version bump to track the repowise 0.25.0 release.
- Renamed the user-facing "biomarker" term to "marker" across the README and the
  code-health / pre-modification skills, matching the Code Health UI copy change.
  No MCP tool-surface or command-flag changes this cycle.

## 0.24.1

### Changed
- Version bump to track the repowise 0.24.1 release. No command, skill, or MCP
  tool-surface changes this cycle.

## 0.24.0

### Changed
- Version bump to track the repowise 0.24.0 release. No command, skill, or MCP
  tool-surface changes this cycle.

## 0.23.0

### Changed
- Version bump to track the repowise 0.23.0 release. No command, skill, or MCP
  tool-surface changes this cycle.

## 0.22.0

### Changed
- Updated the `search_codebase` skill docs to describe hybrid symbol/path search:
  the new `mode` parameter (`auto`/`concept`/`symbol`/`path`/`hybrid`) and
  identifier/path query routing into structural-index results.

## 0.21.0

### Changed
- Reconciled the documented MCP tool surface to the consolidated, configurable
  set: ten tools in single-repo mode, three more added automatically in
  workspace mode (`get_blast_radius`, `get_conformance`, `get_architecture`),
  and two opt-in tools (`get_dependency_path`, `get_execution_flows`). The six
  removed redundant tools no longer appear in any command or skill.

## 0.20.0

Version bump to track the repowise 0.20.0 release. No changes to the plugin's
commands, skills, hooks, or MCP tool surface this cycle.

## 0.19.1

Version bump to track the repowise 0.19.1 release. No changes to the plugin's
commands, skills, hooks, or MCP tool surface this cycle.

## 0.19.0

Version bump to track the repowise 0.19.0 release. No changes to the plugin's
commands, skills, hooks, or MCP tool surface this cycle.

## 0.18.0

Version bump to track the repowise 0.18.0 release. No changes to the plugin's
commands, skills, hooks, or MCP tool surface this cycle.

## 0.17.0

### Changed
- Widened the bundled `PostToolUse` hook matcher to include `PowerShell`
  (the Windows Claude Code shell tool), matching the CLI-installed augment
  hook.

## 0.16.0

First release distributed through the marketplace at the repo root
(`/plugin marketplace add repowise-dev/repowise`).

### Added
- Marketplace manifest at the repo root pointing at `plugins/claude-code`.
- Bundled `PostToolUse` hook (`repowise-augment`) so proactive context
  enrichment works as soon as the plugin is installed.
- Commands: `/repowise:health`, `/repowise:risk`, `/repowise:dead-code`,
  `/repowise:decision`, `/repowise:doctor`.
- Skills: `code-health` and `change-review` (PR / branch / working-tree review
  combining the whole-change `repowise risk` score with `get_risk`'s per-file
  `directive` block).

### Changed
- Corrected the MCP tool surface to the **9 exposed tools**: `get_overview`,
  `get_answer`, `get_context`, `get_symbol`, `search_codebase`, `get_risk`,
  `get_why`, `get_dead_code`, `get_health`.
- Refreshed `codebase-exploration` to route across all 9 tools with explicit
  trust signals, and tightened `pre-modification`, `architectural-decisions`,
  and `dead-code-cleanup`.
- Documented the fifth layer (Code Health) throughout commands and docs;
  `init` now notes code health is built in index-only mode.

### Removed
- References to `get_dependency_path` and `get_architecture_diagram` (present in
  the server but not exposed as MCP tools).
- The standalone-repo distribution model and the install-time estimate from the
  setup-mode docs.
