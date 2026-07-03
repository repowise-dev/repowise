# Repowise for VS Code

The Repowise extension brings the local index into your editor and registers the
Repowise MCP server with VS Code, so the same index serves both you and your AI
coding agent. It is a thin client over the local `repowise` CLI and server:
everything is computed on your machine and nothing about your code leaves it
through the extension.

## Install

1. Install the CLI: `pip install repowise` (or `uv tool install repowise`).
2. Install the extension from the Marketplace (search **Repowise**, publisher
   `repowise-dev`) or from Open VSX for VS Code forks.
3. Open a repository and run **Repowise: Set Up This Repository** to build the
   index, or follow the **Get Started with Repowise** walkthrough.

The extension activates only in trusted workspaces and does no work at startup
beyond registering its commands. It discovers a running server from the lockfile
under `.repowise/`, or offers to start one when you first need data.

## What it surfaces

### Editor-native signals

- **Diagnostics** for the files you open, published to the Problems panel. Quiet
  by default: only high-severity findings, capped at Warning. Lower-severity
  findings live in the gutter and tree views.
- **Gutter heat**: a severity-tiered strip next to lines with findings in the
  visible editor.
- **File health in the status bar**: defect, maintainability, and performance
  scores for the active file.
- **File explorer badges** on the worst-health files (threshold configurable).
- **Refactoring CodeLens** above symbols with a detected plan, including
  **Copy plan for agent** (the same payload the web Refactoring tab produces).
- **Hovers** with file health, hotspot flag, primary owner, and governing
  decisions.
- **Change risk** from the SCM title bar: score your change against its base and
  see what it touches. The panel shows the drivers behind the score plus, for the
  files you have changed, what is downstream of them, which files your history
  says usually change alongside them, any changed file with no test, and the best
  reviewers (ownership x co-change), with a one-click copy for the PR.
- **A quiet co-change nudge**: when the files you are editing have a strong
  history of changing together with a file you have not touched, a subtle
  status-bar item offers to show you. It is advisory and dismissible per change
  set, never a popup, and off or tunable from Settings
  (`repowise.changeIntel.*`). Plenty of edits legitimately touch only part of a
  cluster, so this informs without nagging.

### Tree views

A single Repowise activity-bar container with a **Home** overview, a **Findings**
tree (health, hotspots, ownership, dead code), and a **Refactoring** tree. All
lazy: data is fetched on first expand and refreshed only when the index moves.

### Dashboards

Editor-tab webviews rendered from the same shared visualization library the web
app uses (no duplicated components): **health overview**, **C4 architecture**,
**knowledge graph** (with node search, path finder, and community detail),
**refactoring plans**, **decision timeline**, and a **docs browser**.

## MCP for your AI agent

One install registers the Repowise MCP server with VS Code, so agent-mode
assistants query the index through task-shaped tools instead of guessing from
open files. For editors that read a config file, run **Repowise: Configure MCP
for this Workspace** to write `.vscode/mcp.json`.

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `repowise.server.autoStart` | `ask` | Start the local server automatically, ask first, or never |
| `repowise.server.port` | discover | Override the server port instead of using lockfile discovery |
| `repowise.cliPath` | PATH | Absolute path to the `repowise` executable |
| `repowise.diagnostics.enabled` | `true` | Show health findings in the Problems panel |
| `repowise.diagnostics.minSeverity` | `high` | Lowest severity surfaced in the Problems panel |
| `repowise.diagnostics.dimensions` | all | Health dimensions included in the Problems panel |
| `repowise.gutterHeat.enabled` | `true` | Shade the gutter next to findings |
| `repowise.fileDecorations.enabled` | `true` | Badge the worst-health files in the explorer |
| `repowise.codeLens.enabled` | `true` | Show refactoring plan lenses |
| `repowise.hover.enabled` | `true` | Show file health context on hover |
| `repowise.risk.baseBranch` | default branch | Base branch change risk is scored against |
| `repowise.changeIntel.cochangeNudge` | `true` | Show the quiet "usually change together" status-bar hint |
| `repowise.changeIntel.cochangeMinScore` | `4` | Minimum historical co-change count before a related file is surfaced |

## Privacy

The extension talks only to the local Repowise CLI and server on your machine
and reads the index under `.repowise/`. It sends no telemetry of its own. The
CLI's own telemetry opt-out is respected because the extension itself sends
nothing.

The extension source lives in [`packages/vscode`](../packages/vscode).
