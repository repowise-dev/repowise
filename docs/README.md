# repowise documentation

The codebase intelligence layer for your AI coding agent. Index once, and give
your agent a dependency graph, git history, auto-generated docs, architectural
decisions, and a defect-validated code-health score, all through task-shaped MCP
tools.

New here? Start with the [Quickstart](QUICKSTART.md) (under 5 minutes, no API
key), then wire up [your agent](MCP_TOOLS.md).

## Get started

| Doc | What it covers |
|-----|----------------|
| [QUICKSTART.md](QUICKSTART.md) | Install, index your repo, and connect your agent in under 5 minutes |
| [USER_GUIDE.md](USER_GUIDE.md) | The everyday guide: the CLI, the local dashboard, and every view in it |
| [VSCODE.md](VSCODE.md) | The Repowise VS Code extension: health in the gutter, risk before you push, dashboards in the editor |

## Connect your AI agent

| Doc | What it covers |
|-----|----------------|
| [MCP_TOOLS.md](MCP_TOOLS.md) | The MCP tools, what each answers, and worked multi-tool examples |
| [CODEX.md](CODEX.md) | Wiring repowise into the Codex CLI |
| [OPENCODE.md](OPENCODE.md) | Wiring repowise into opencode |
| [DISTILL.md](DISTILL.md) | `repowise distill`: compress noisy command output before your agent reads it |

## The intelligence layers

| Doc | What it covers |
|-----|----------------|
| [INTELLIGENCE_LAYERS.md](INTELLIGENCE_LAYERS.md) | Overview of the five layers: graph, git, docs, decisions, code health |
| [CODE_HEALTH.md](CODE_HEALTH.md) | Defect risk, maintainability, and performance from 25 deterministic markers, plus refactoring targets |
| [REFACTORING.md](REFACTORING.md) | Concrete, graph-aware refactoring plans (Extract Class, Move Method, Break Cycle, …) |
| [CHANGE_RISK.md](CHANGE_RISK.md) | Score any commit or `base..HEAD` range 0–10 for defect risk |
| [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md) | What works per language, across 15 parsed languages and 9 at the Full tier |
| [WORKSPACES.md](WORKSPACES.md) | Multi-repo intelligence: cross-repo contracts, co-changes, and federated MCP |
| [AUTO_SYNC.md](AUTO_SYNC.md) | Keep the index fresh automatically on every commit |
| [WIKI_STYLES.md](WIKI_STYLES.md) | Selectable documentation styles (comprehensive / reference / tutorial / caveman) |

## Reference

| Doc | What it covers |
|-----|----------------|
| [CLI_REFERENCE.md](CLI_REFERENCE.md) | Every command and flag |
| [CONFIG.md](CONFIG.md) | `.repowise/config.yaml`, `health-rules.json`, and environment variables |
| [COMPUTED_GLOSSARY.md](COMPUTED_GLOSSARY.md) | Definitions for every computed metric and term repowise reports |
| [TELEMETRY.md](TELEMETRY.md) | What anonymous telemetry collects, and how to turn it off |
| [UPGRADING.md](UPGRADING.md) | Notes for upgrading between versions |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## Teams & business

| Doc | What it covers |
|-----|----------------|
| [COMMERCIAL.md](COMMERCIAL.md) | Hosted tier, enterprise (on-prem, SSO/SCIM), and commercial licensing |

## Architecture & internals

How repowise is built, for contributors and the curious. See
[architecture/](architecture/README.md) for the full set: the system
architecture, code-health internals, the language pipeline, graph algorithms,
and more.
