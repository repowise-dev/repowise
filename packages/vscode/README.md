# Repowise for VS Code

Repowise is the codebase intelligence layer for you and your AI coding agent. It indexes your repository once and builds five layers on top: a real dependency graph, git history signals (hotspots, ownership, hidden coupling), always-current architecture docs, mined architectural decisions, and defect-validated code health with concrete refactoring plans. Everything is computed locally and served from a local server.

This extension brings that index into VS Code, for both halves of how code gets written today:

- **For you:** discover or start the local Repowise server for the current workspace, see connection and setup state at a glance, and get guided from a fresh clone to a fully indexed repository without leaving the editor.
- **For your AI agent:** one install registers the Repowise MCP server with VS Code, so agent-mode assistants query the index through task-shaped tools and answer "why does auth work this way?" from grounded architecture, history, and health data instead of guessing from open files.

## What you get

- **Inline health signals** on the files you open: findings in the Problems panel (quiet by default, high severity only), a gutter heat strip for the full set, and file health scores in the status bar.
- **Refactoring plans as CodeLens** above the symbols they target, with one click to copy the plan as a prompt for your agent.
- **Branch risk before you push**: score a branch against its base from the SCM title bar, with the drivers behind the score.
- **Hovers and tree views** for ownership, hotspots, dead code, and architectural decisions, all lazy and refreshed only when the index moves.
- **Dashboards inside the editor**: health overview, C4 architecture, knowledge graph, refactoring plans, decision timeline, and a docs browser, rendered from the same visualizations the web app uses.

<!-- Screenshots / GIFs: drop captures under media/screenshots and link them here before the stable Marketplace release. -->

## Getting started

1. Install the CLI: `pip install repowise` (or `uv tool install repowise`).
2. Open your repository and run **Repowise: Set Up This Repository** to build the index, or follow the Get Started walkthrough.
3. The extension finds the running server automatically, or offers to start one when you need it.
4. Run **Repowise: Configure MCP for this Workspace** to expose the tools to MCP clients through `.vscode/mcp.json`. In VS Code itself, the MCP server is registered automatically.

## Commands

| Command | What it does |
|---|---|
| Repowise: Set Up This Repository | Build the index for this workspace |
| Repowise: Start Server / Stop Server | Manage the local API server |
| Repowise: Configure MCP for this Workspace | Write `.vscode/mcp.json` for MCP clients |
| Repowise: Check Branch Risk | Score the current branch against its base |
| Repowise: Update Index | Sync the index with your latest commits |
| Repowise: Show Health Dashboard | Open the health overview |
| Repowise: Show Architecture Map | Open the C4 architecture view |
| Repowise: Show Knowledge Graph | Open the dependency constellation |
| Repowise: Show Decision Timeline | Browse mined architectural decisions |
| Repowise: Open Docs for This File | Open the generated docs for the active file |
| Repowise: Check Setup | Diagnose install, keys, and index state |
| Repowise: Show Log | Open the extension log |

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `repowise.server.autoStart` | `ask` | Start the local server automatically, ask first, or never |
| `repowise.server.port` | discover | Override the server port instead of using lockfile discovery |
| `repowise.cliPath` | PATH | Absolute path to the `repowise` executable |

## Privacy

The extension talks only to the local Repowise CLI and server on your machine and reads the index under `.repowise/` in your project. It sends no telemetry of its own, and nothing about your code leaves your machine through this extension.

## Learn more

- [repowise.dev](https://www.repowise.dev) and the [live demo](https://www.repowise.dev)
- [Documentation](https://docs.repowise.dev)
- [GitHub](https://github.com/repowise-dev/repowise) (AGPL-3.0, free and open source)
- [Discord](https://discord.gg/cQVpuDB6rh)
