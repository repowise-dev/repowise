# Repowise for Pi

The Claude Code hook behaviours (`plugins/claude-code/hooks/hooks.json`),
as a Pi extension. A Pi user with this extension gets the ambient
assistance the skills and MCP server alone do not provide: context
injected at session start, Bash output distilled before it runs, codebase
context after reads and edits, a decision-capture prompt after
`git commit`, and a failure-context rescue when a file tool fails on a
path this tree does not have.

## Install

Copy the extension into Pi's extensions directory (global), or into the
project's `.pi/extensions/` (project-local, needs the project to be
trusted):

```bash
cp plugins/pi/repowise.ts ~/.pi/agent/extensions/
# or, per project:
cp plugins/pi/repowise.ts .pi/extensions/
```

Or install it as a Pi package from this repo (same file, Pi-managed):

```bash
pi install git:github.com/repowise-dev/repowise
```

Then reload extensions (`/reload`) or restart Pi. Check it is alive with
`/repowise`, which reports helper availability and whether the session
context was injected.

Requires the `repowise-augment` and `repowise-rewrite` helpers on PATH
(they ship with the Repowise CLI). If a helper is absent, that surface
stays silent and the tool runs unchanged — a missing helper never breaks
a turn.

## How it maps

| Claude Code hook | Pi event | Helper |
| --- | --- | --- |
| `SessionStart` (`startup\|resume\|clear`) | `before_agent_start` * | `repowise-augment` |
| `PreToolUse` (Bash) | `tool_call` | `repowise-rewrite` |
| `PostToolUse` (read/edit/grep/repowise) | `tool_result` | `repowise-augment` |
| `PostToolUse` (Bash + `git commit`) | `tool_result` | `repowise-augment` |
| `PostToolUseFailure` | `tool_result` (error) | `repowise-augment` |

\* `agent_start` cannot inject context — its handlers have no return
contract — so session-start injection runs in `before_agent_start`,
which can return a message, gated to the first user run of the session.
`session_start` resets the gate, so resume/clear re-inject exactly like
Claude Code's matcher. `tool_execution_end` is deliberately not used:
`tool_result` already observes every completed call, and calling the
helper twice per tool would double-count the hook ledger.

The helpers speak the same `hookSpecificOutput` JSON the Claude Code
hooks read. The extension parses that shape: `additionalContext` is
appended to the result as a text block, and `updatedToolOutput` replaces
the result content (a replacement drops the structured content, per Pi's
`tool_result` contract — so the read-skeleton and grep flood-digest
replacements arrive as text, not as structured results).

The Python side of the contract lives in
`packages/cli/src/repowise/cli/commands/augment_cmd/pi.py` (payload
translation) and `packages/cli/src/repowise/cli/agent_adapters/pi.py`
(tool names, rewrite parsing/rendering). The `repowise-rewrite` call
uses `--agent pi`; only `allow` rewrites are applied — Pi has no
ask-with-mutation, so an `ask` decision passes through untouched, the
same posture as Codex.

## Lens settings

The seven `lens_*` toggles from the Claude Code plugin are reachable
from Pi configuration. Put them in a `repowise` block in `settings.json`
(global `~/.pi/agent/settings.json` or project `.pi/settings.json`):

```json
{
  "repowise": {
    "lens_margin": true,
    "lens_squeeze": true,
    "lens_review": true,
    "lens_flow": true,
    "lens_pane_autoopen": false,
    "lens_map_health": false
  }
}
```

or in the extension's own settings file, `~/.pi/agent/repowise.json`
(or `.pi/repowise.json` in a trusted project) with the same keys at the
top level. `settings.json` wins per key; anything else falls back to the
file, then to the defaults above (the Claude Code plugin's defaults).

**Lens itself is Claude Code-only in this version.** The toggles are
parsed and validated on session start, but no Lens UI exists in Pi yet,
so they currently gate nothing here. Porting the four Lens features
(margin notes, squeeze rows, change review, Flow tab, map pane) to Pi's
TUI component API is a follow-up issue; this extension deliberately does
not pretend the toggles do work they do not do.

## Verification status

No live Pi session was available when this shipped. The extension is
type-checked against the real `@earendil-works/pi-coding-agent` package,
and the Python helpers are covered by mocked tests
(`tests/unit/cli/test_augment_pi.py`, `test_pi_adapter.py`,
`test_pi_plugin.py`). Live-session confirmation — injection, distillation,
decision capture, failure rescue observed in a real Pi run — is still
open; see issue #5308.
