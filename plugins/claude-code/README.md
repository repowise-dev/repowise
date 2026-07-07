# Repowise Plugin for Claude Code

Gives Claude Code deep understanding of your codebase — architecture, ownership,
hotspots, dependencies, architectural decisions, and a defect-validated
code-health score. Claude answers *"why does auth work this way?"* instead of
*"here's what `auth.ts` contains"* — with fewer greps, fewer file reads, and
lower cost per query.

## Install

### From the marketplace

```shell
/plugin marketplace add repowise-dev/repowise
/plugin install repowise@repowise
```

### Local development

```shell
claude --plugin-dir ./plugins/claude-code
```

## Quick start

After installing the plugin, run:

```
/repowise:init
```

Claude walks you through everything: installing repowise, choosing a mode,
configuring your LLM provider, and indexing your codebase. Once indexed, the
MCP tools and skills activate automatically.

## What you get

### Five intelligence layers

Graph (tree-sitter dependency graph, 15 languages) · Git (hotspots, ownership,
co-change, bus factor) · Docs (LLM-generated wiki + semantic search) · Decisions
(architectural rationale mined from eight sources) · Code Health (1–10
defect-validated score from deterministic markers).

### Slash commands

| Command | What it does |
|---------|-------------|
| `/repowise:init` | Interactive setup — installs repowise, asks your preferences, indexes your codebase |
| `/repowise:status` | Health check — sync state, page counts, provider info |
| `/repowise:update` | Incremental update — sync the index with recent code changes |
| `/repowise:search` | Search the codebase wiki (fulltext, semantic, or symbol) |
| `/repowise:reindex` | Rebuild the vector store (re-embed; no LLM calls) |
| `/repowise:health` | Code-health KPIs, lowest-scoring files, refactoring targets, trends |
| `/repowise:risk` | Defect-risk score for a change (commit or `base..head` range) |
| `/repowise:dead-code` | Unreachable files, unused exports, zombie packages by confidence |
| `/repowise:decision` | List, inspect, add, or confirm architectural decisions |
| `/repowise:doctor` | Diagnose (and optionally repair) the setup, keys, and index drift |

### Automatic skills

Claude uses these when relevant — no slash command needed:

- **Codebase exploration** — routes questions to `get_overview` / `get_answer` / `search_codebase` / `get_context` / `get_symbol` instead of raw file reads.
- **Pre-modification check** — calls `get_risk` (and `get_health` for refactors) before editing to assess blast radius.
- **Change review** — for a PR / branch / working-tree diff, combines `repowise risk` (whole-change score) with `get_risk`'s per-file `directive` block (will-break / missing co-changes / missing tests).
- **Code health** — answers quality / complexity / "what to refactor" via `get_health`.
- **Architectural decisions** — queries `get_why` for the *why* before architectural changes.
- **Dead-code cleanup** — uses `get_dead_code`, conservatively, during cleanup.

### MCP tools (9)

Registered automatically when the plugin is enabled:

| Tool | What it answers |
|------|-----------------|
| `get_overview` | Architecture summary, module map, entry points, git health |
| `get_answer` | Cited, synthesised answer to a code question + a calibrated confidence |
| `get_context` | Triage card (docs, signatures, hotspot bit, callers, decisions) for files/modules/symbols |
| `get_symbol` | Raw source of one symbol with exact line bounds |
| `search_codebase` | Hybrid code search — `mode="auto"` routes identifiers to indexed symbols, paths to files, prose to semantic wiki search |
| `get_risk` | Per-file hotspot, dependents, co-changes, owners; PR `directive` block with `changed_files` |
| `get_why` | Architectural decisions — search, path-anchored, or health dashboard |
| `get_dead_code` | Unused/unreachable findings tiered by confidence |
| `get_health` | 1–10 code-health score and marker findings per file |

## Setup modes

| Mode | What you get | Requirements |
|------|-------------|-------------|
| **Index-only** | Graph + Git + Code Health + Dead Code | Nothing (no LLM) |
| **Full** | Index-only **plus** Docs, semantic search, and Decisions | LLM API key |
| **Local (Ollama)** | Full mode, fully offline | Ollama running |

Index-only builds with zero LLM calls; full mode adds the documentation layer on
top, which can continue in the background. Run `/repowise:init` and Claude helps
you choose.

## Proactive context (hooks)

The plugin registers a `PostToolUse` hook that runs `repowise-augment` after
`Bash` / `Grep` / `Glob`. It stays silent unless it has something asymmetric to
add — rescuing a zero-result grep with the closest indexed symbol, ranking a
flood of matches by graph centrality, or flagging a stale index after a commit.
No LLM, no network. (`repowise init` installs the same hook in
`~/.claude/settings.json`; running both is safe — duplicate enrichment is
de-duplicated.)

## Requirements

- Python 3.11+
- Git (for the git-intelligence layer)
- The `repowise` CLI on PATH (`/repowise:init` installs it)

## Troubleshooting

**MCP tools not connecting:** run `/repowise:init` — the plugin auto-registers the
MCP server, but the `repowise` binary must be installed and on PATH.

**`pip install` fails on Windows:** try `python -m pip install repowise`.

**Semantic search / `get_answer` returns nothing:** the repo may be in index-only
mode (no wiki). Re-run `/repowise:init` with an LLM provider, or `/repowise:reindex`
if pages exist but embeddings are missing.

**Stale results after code changes:** run `/repowise:update`.

## License

AGPL-3.0, same as repowise. See the [repository](https://github.com/repowise-dev/repowise).
