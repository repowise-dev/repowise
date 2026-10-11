# Hooks reference

The exact entries repowise writes for its agent hooks, and how to run them by
hand. For what each hook does and when it fires, see [HOOKS.md](../agent/HOOKS.md).

## Entries and commands

| Client | Hook type | Matcher | Command | Written to |
|--------|-----------|---------|---------|------------|
| Claude Code | `SessionStart` | `startup\|resume\|clear` | `repowise-augment` (guarded) | `~/.claude/settings.json` |
| Claude Code | `PostToolUse` | `Grep\|Glob\|Read\|Edit\|Write\|mcp__.*[Rr]epowise.*__.*` | `repowise-augment` (guarded) | `~/.claude/settings.json` |
| Claude Code | `PostToolUseFailure` | `Read\|Edit\|Write\|Grep\|Glob\|NotebookEdit` | `repowise-augment` (guarded) | `~/.claude/settings.json` |
| Claude Code | `PreToolUse` (opt-in) | `Bash\|PowerShell` | `repowise-rewrite` | `~/.claude/settings.json` |
| Claude Code | `PostToolUse` (opt-in decision capture prompt) | `Bash\|PowerShell` | `repowise-augment` (guarded) | `~/.claude/settings.json` |
| Claude Code | `PostToolUse` and `PostToolUseFailure` (opt-in coverage re-ingest) | `Bash\|PowerShell` | `repowise-augment --coverage-only` (guarded) | the repo's `.claude/settings.local.json` |
| Codex | `PreToolUse` (opt-in) | the shell tool | `repowise-rewrite --agent codex` | `~/.codex/hooks.json` |
| Codex | `SessionStart` | `startup\|resume\|clear` | `repowise-augment --client codex` | the repo's `.codex/hooks.json` |
| Codex | `PostToolUse` | `Bash\|shell_command`, `apply_patch\|Edit\|Write` | `repowise-augment --client codex` | the repo's `.codex/hooks.json` |

Which switch turns each opt-in entry on:

| Entry | Switch |
|-------|--------|
| Command rewrite | `repowise hook rewrite install`, or Yes at the `repowise init` prompt |
| Decision capture prompt | `repowise decision config capture-prompt --on` ([decisions](../layers/DECISIONS.md)). The entry is per install, so one repo opting in adds a process start on shell calls for every repo on the machine; repos with the policy off return at once |
| Coverage re-ingest | `hooks.coverage_reingest: true` in `.repowise/config.yaml`, applied by the next `repowise coverage add`, `init` or `update` ([test intelligence](../layers/TEST_INTELLIGENCE.md)) |

The Claude Code plugin does not carry the coverage re-ingest entries.
`repowise uninstall` removes them.

## The presence guard

Claude Code commands marked "guarded" are written wrapped in a presence check:

```sh
if command -v repowise-augment >/dev/null 2>&1; then exec repowise-augment; fi
```

The Claude Code plugin ships these hooks independently of the CLI, so "plugin
installed, `repowise` not installed" is a supported state. A partial install
reaches it too: on Windows a running MCP server holds `repowise.exe` open, so an
installer can stop after writing only some console scripts. Without the guard,
either state prints `command not found` on every matched tool call. The guard is
POSIX (`command -v` plus `exec`), forwards stdin unchanged, and behaves
identically when the script is present. An install carrying the bare name is
rewritten on the next `repowise init`.

Codex hooks keep the bare command name, because a Codex hook command is not
documented to run through a shell.

## Other settings `init` writes

- `SessionStart` excludes `compact`: the block usually survives compaction in
  the summary, and emitting it again would double it.
- `env.ENABLE_TOOL_SEARCH=true` is set in `~/.claude/settings.json` so MCP tool
  schemas load on demand. A value you already set, including `false`, is left
  alone.
- `repowise init --no-editor-setup` (or `REPOWISE_SKIP_EDITOR_SETUP=1`) writes
  none of the Claude Code entries above and skips MCP registration.

## Entry points

`repowise-augment` is the import-isolated console script the agent hooks call;
`repowise augment` is the same engine as a CLI subcommand. `repowise-rewrite`
backs the command-rewrite hook. None of them is meant for everyday manual use.
See [CLI reference](CLI_REFERENCE.md#repowise-augment).

## Hook ledger

`repowise hook stats` and `repowise hook backfill` read and seed the local
ledger in `.repowise/sessions/sessions.db`. Flags:
[`hook stats`](CLI_REFERENCE.md#repowise-hook-stats),
[`hook backfill`](CLI_REFERENCE.md#repowise-hook-backfill).
