# Changelog

All notable changes to the Repowise Claude Code plugin are documented here.

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
