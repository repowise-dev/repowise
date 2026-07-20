# repowise documentation

Index your codebase once. Your agent stops greping, your team stops guessing
which PR is dangerous, and both get their answers from the same place.

<div align="center">
  <img src="../.github/assets/one-index.svg" alt="One index producing code health, a dependency graph, git history, generated docs, architectural decisions, and ten MCP tools" width="100%" />
</div>

New here? **[Quickstart](start/QUICKSTART.md)** gets you indexed and connected to
your agent in under five minutes, with no API key.

---

## Pick your path

| You are | Start here | Then |
|---|---|---|
| **A developer wiring up an AI agent** | [Quickstart](start/QUICKSTART.md) | [MCP tools](agent/MCP_TOOLS.md) · [Hooks](agent/HOOKS.md) · [Distill](agent/DISTILL.md) |
| **Living in an editor** | [VS Code extension](agent/VSCODE.md) | [Codex](agent/CODEX.md) · [opencode](agent/OPENCODE.md) |
| **A team lead watching what ships** | [Change risk](layers/CHANGE_RISK.md) | [Code health](layers/CODE_HEALTH.md) · [Bug history](layers/BUG_HISTORY.md) |
| **Running many repos** | [Workspaces](scale/WORKSPACES.md) | [Worktrees](scale/WORKTREES.md) · [Auto-sync](scale/AUTO_SYNC.md) |
| **Evaluating repowise to buy it** | [Commercial](business/COMMERCIAL.md) | [Security & compliance](business/SECURITY_COMPLIANCE.md) · [Benchmarks](BENCHMARKS.md) |
| **Contributing** | [Architecture](architecture/README.md) | [CONTRIBUTING](../.github/CONTRIBUTING.md) |

---

## Get started

| Doc | What it covers |
|-----|----------------|
| [start/QUICKSTART.md](start/QUICKSTART.md) | Install, index your repo, and connect your agent in under 5 minutes |
| [start/USER_GUIDE.md](start/USER_GUIDE.md) | The everyday guide: how the pieces fit and the workflows they support |
| [start/DASHBOARD.md](start/DASHBOARD.md) | Every view in the local web dashboard, and what each one answers |

## Connect your AI agent

| Doc | What it covers |
|-----|----------------|
| [agent/MCP_TOOLS.md](agent/MCP_TOOLS.md) | The ten task-shaped tools, what each answers, and worked multi-tool examples |
| [agent/HOOKS.md](agent/HOOKS.md) | Proactive delivery: context and warnings that arrive without the agent asking |
| [agent/DISTILL.md](agent/DISTILL.md) | `repowise distill`: compress noisy command output before your agent reads it |
| [agent/VSCODE.md](agent/VSCODE.md) | The VS Code extension: health in the gutter, risk before you push, dashboards in the editor |
| [agent/CODEX.md](agent/CODEX.md) | Wiring repowise into the Codex CLI |
| [agent/OPENCODE.md](agent/OPENCODE.md) | Wiring repowise into opencode |

## The intelligence layers

| Doc | What it covers |
|-----|----------------|
| [layers/INTELLIGENCE_LAYERS.md](layers/INTELLIGENCE_LAYERS.md) | Overview of the five layers: graph, git, docs, decisions, code health |
| [layers/CODE_HEALTH.md](layers/CODE_HEALTH.md) | Defect risk, maintainability, and performance from 25 deterministic markers |
| [layers/REFACTORING.md](layers/REFACTORING.md) | Concrete, graph-aware refactoring plans (Extract Class, Move Method, Break Cycle) |
| [layers/CHANGE_RISK.md](layers/CHANGE_RISK.md) | Score any commit or `base..HEAD` range 0-10 for defect risk |
| [layers/BUG_HISTORY.md](layers/BUG_HISTORY.md) | Which files and symbols actually get bug-fixed, and how recently |
| [layers/TEST_INTELLIGENCE.md](layers/TEST_INTELLIGENCE.md) | Coverage ingestion, untested hotspots, and running only the tests a diff touches |
| [layers/DECISIONS.md](layers/DECISIONS.md) | Architectural decisions mined from your repo and from your own agent sessions |
| [layers/DEAD_CODE.md](layers/DEAD_CODE.md) | Unreachable files, unused exports, and zombie packages by confidence tier |
| [layers/LANGUAGE_SUPPORT.md](layers/LANGUAGE_SUPPORT.md) | What works per language, across 16 parsed languages and 11 at the Full tier |
| [layers/WIKI_STYLES.md](layers/WIKI_STYLES.md) | Selectable documentation styles (comprehensive / reference / tutorial / caveman) |

## Scale it

| Doc | What it covers |
|-----|----------------|
| [scale/WORKSPACES.md](scale/WORKSPACES.md) | Multi-repo intelligence: cross-repo contracts, co-changes, and federated MCP |
| [scale/WORKTREES.md](scale/WORKTREES.md) | Linked git worktrees seed their index from the base checkout, with no flags |
| [scale/AUTO_SYNC.md](scale/AUTO_SYNC.md) | Keep the index fresh automatically on every commit |
| [../docker/README.md](../docker/README.md) | Running repowise in Docker |

## Reference

| Doc | What it covers |
|-----|----------------|
| [reference/CLI_REFERENCE.md](reference/CLI_REFERENCE.md) | Every command and flag |
| [reference/CONFIG.md](reference/CONFIG.md) | `.repowise/config.yaml`, `health-rules.json`, and environment variables |
| [reference/COMPUTED_GLOSSARY.md](reference/COMPUTED_GLOSSARY.md) | Definitions for every computed metric and term repowise reports |
| [reference/TELEMETRY.md](reference/TELEMETRY.md) | What anonymous telemetry collects, and how to turn it off |
| [reference/UPGRADING.md](reference/UPGRADING.md) | Notes for upgrading between versions |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## Evidence

| Doc | What it covers |
|-----|----------------|
| [BENCHMARKS.md](BENCHMARKS.md) | Agent-efficiency, distillation, and defect-prediction results, each with what it does not show |

## Teams & business

| Doc | What it covers |
|-----|----------------|
| [business/COMMERCIAL.md](business/COMMERCIAL.md) | Hosted tier, enterprise (on-prem, SSO/SCIM), and commercial licensing |
| [business/SECURITY_COMPLIANCE.md](business/SECURITY_COMPLIANCE.md) | What leaves your machine, what gets stored, and the answers your security team wants |

## Architecture & internals

How repowise is built, for contributors and the curious.

| Doc | What it covers |
|-----|----------------|
| [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md) | The system: package layout, pipelines, MCP server |
| [architecture/code-health.md](architecture/code-health.md) | Health internals: marker computation and calibrated weights |
| [architecture/graph-algorithms.md](architecture/graph-algorithms.md) | Every graph algorithm, the intuition plus the math |
| [architecture/language-support.md](architecture/language-support.md) | The language pipeline and how the tiers work |
| [architecture/chat.md](architecture/chat.md) | Codebase chat: agent loop, streaming, artifact panel |
| [architecture/editor-files.md](architecture/editor-files.md) | How `CLAUDE.md` and `AGENTS.md` get generated |
| [architecture/deep-dives.md](architecture/deep-dives.md) | Systems not covered elsewhere |
| [architecture/pluggable-storage.md](architecture/pluggable-storage.md) | The capability seams: storage, graph, vector, CLI, MCP |
| [design/theme-tokens.md](design/theme-tokens.md) | Resolved design tokens and the WCAG contrast matrix |
