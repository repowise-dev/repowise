# Architecture & Internals

Deep references for how repowise is built. These are for contributors and the
curious; you don't need them to *use* repowise (start with
[the docs index](../README.md) for that).

| Doc | Covers |
|-----|--------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | The single end-to-end system reference: stores, provider abstraction, the init and maintenance pipelines, git intelligence, dead code, decisions, MCP/REST/UI layers, and key design decisions |
| [deep-dives.md](deep-dives.md) | Algorithm-level treatment of the hard parts: dead-code detection, the decision/ADR system, search & vector-store internals, incremental updates & webhooks, the change-cascade algorithm |
| [code-health.md](code-health.md) | The code-health layer as a full vertical slice: pipeline, the 25+ markers, scoring, trends, persistence schema, and extension points |
| [language-support.md](language-support.md) | The language pipeline internals and the step-by-step recipe for adding a new language |
| [graph-algorithms.md](graph-algorithms.md) | The graph algorithms (PageRank, betweenness, Tarjan SCC, Louvain, shortest path) with the math and complexity |
| [chat.md](chat.md) | The Codebase Chat feature: schema, `ChatProvider` protocol, SSE streaming, the agentic loop, and per-provider notes |
| [editor-files.md](editor-files.md) | How `CLAUDE.md` / `AGENTS.md` generation works (no LLM: pure DB + filesystem derivation) and how to add a new editor file |
| [pluggable-storage.md](pluggable-storage.md) | Extension guide for authoring storage / graph / job-store plugins and adding CLI subcommands or MCP tools |

For the design-token and theme tooling, see [../design/](../design/).
