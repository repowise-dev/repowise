# Architecture & Internals

Deep references for how repowise is built. These are for contributors and the
curious; you don't need them to *use* repowise (start with
[the docs index](../README.md) for that).

| Doc | Covers |
|-----|--------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | The single end-to-end system reference: stores, provider abstraction, the init and maintenance pipelines, git intelligence, dead code, decisions, MCP/REST/UI layers, and key design decisions |
| [deep-dives.md](deep-dives.md) | Algorithm-level treatment of the hard parts: dead-code detection, the decision/ADR system, search & vector-store internals, incremental updates & webhooks, the change-cascade algorithm |
| [code-health.md](code-health.md) | The code-health layer as a full vertical slice: pipeline, the 53 registered detectors, scoring, trends, persistence schema, and extension points |
| [change-risk.md](change-risk.md) | The change-risk model: features, offline calibration and why the score is reported as a size statistic, PR structural impact, and the public risk-scale inventory |
| [bug-history.md](bug-history.md) | Bug-fix history internals: fix-shape classification, the `fix_events` table, the bug-magnet rollup, the inducing-commit (SZZ) work that was measured and not shipped, and the suspects ranking measurement |
| [decisions.md](decisions.md) | Decision capture internals: the source registry, evidence verification, confidence, currency, session mining and hook delivery |
| [security.md](security.md) | Security signals internals: per-kind matching rules, placeholder skipping, baseline keys, and the JS/TS precision checks |
| [test-intelligence.md](test-intelligence.md) | The inferred test tier: call and import walks, their measured precision and recall, the resolution-origin filter, and why nothing is stored |
| [language-support.md](language-support.md) | The language pipeline internals and the step-by-step recipe for adding a new language |
| [workspace.md](workspace.md) | The multi-repo workspace layer: per-repo contract extraction, matching, the system graph and its evidence state, artifacts and incremental rules, and the recipes for adding a contract dialect or a service-name source |
| [forges.md](forges.md) | Git host support: the forges package, its invariants, and the recipes for adding a host or a CI system |
| [agent-platform.md](agent-platform.md) | Agent identities, provider specs, integration targets and adapters, and the recipes for adding an agent integration or an agent CLI indexing backend |
| [graph-algorithms.md](graph-algorithms.md) | The graph algorithms (PageRank, betweenness, Tarjan SCC, Leiden/Louvain community detection, shortest path) with the math and complexity |
| [refactoring.md](refactoring.md) | Refactoring intelligence internals: detector registration, plan shape, ranking, REST routes, and code generation |
| [savings-accounting.md](savings-accounting.md) | The savings accounting contract: what counts as a measured reduction versus an inferred avoidance, and what a total may claim |
| [chat.md](chat.md) | The Codebase Chat feature: schema, `ChatProvider` protocol, SSE streaming, the agentic loop, and per-provider notes |
| [structurizr-export.md](structurizr-export.md) | Exporting the architecture as Structurizr DSL: the two-file model, health and layer tags, and how to render it |
| [editor-files.md](editor-files.md) | How `CLAUDE.md` / `AGENTS.md` generation works (no LLM: pure DB + filesystem derivation) and how to add a new editor file |
| [pluggable-storage.md](pluggable-storage.md) | Extension guide for authoring storage / graph / job-store plugins and adding CLI subcommands or MCP tools |

For the design-token and theme tooling, see [../design/](../design/).
