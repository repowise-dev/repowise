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

Graph (tree-sitter dependency graph, 16 languages) · Git (hotspots, ownership,
co-change, bus factor) · Docs (LLM-generated wiki + semantic search) · Decisions
(architectural rationale mined from five sources) · Code Health (1–10
defect-validated score from deterministic markers).

### Slash commands

| Command | What it does |
|---------|-------------|
| `/repowise:init` | Interactive setup — installs repowise, asks your preferences, indexes your codebase |
| `/repowise:status` | Health check — sync state, page counts, provider info |
| `/repowise:update` | Incremental update — sync the index with recent code changes |
| `/repowise:search` | Search the codebase wiki (fulltext, semantic, or symbol) |
| `/repowise:ask` | Cited, synthesised answer to a codebase question (LLM call) |
| `/repowise:context` | Triage card for files/modules/symbols (layer, hotspot, freshness) |
| `/repowise:symbol` | Read one symbol body with live-verified line bounds |
| `/repowise:reindex` | Rebuild the vector store (re-embed; no LLM calls) |
| `/repowise:health` | Code-health KPIs, lowest-scoring files, refactoring targets, trends |
| `/repowise:coverage` | Ingest or inspect coverage reports (lights up untested hotspots + per-test map) |
| `/repowise:impacted-tests` | Tests whose coverage intersects a change (commit / range / staged) |
| `/repowise:risk` | Repo-relative review priority plus a supporting diff-shape score |
| `/repowise:security` | Full-history secret scan (`repowise security scan --history`) |
| `/repowise:dead-code` | Unreachable files, unused exports, zombie packages by confidence |
| `/repowise:export` | Export wiki pages or a Structurizr architecture model |
| `/repowise:decision` | List, inspect, add, or confirm architectural decisions |
| `/repowise:why` | Why the code is shaped this way (decisions + archaeology) |
| `/repowise:doctor` | Diagnose (and optionally repair) the setup, keys, and index drift |

### Automatic skills

Claude uses these when relevant — no slash command needed:

- **Codebase exploration** — sends exact names to Grep, Glob and Read, and questions about how code works, its callers, impact and history to `get_answer` / `get_context` / `get_risk` / `get_why` / `search_codebase` / `get_symbol`.
- **Pre-modification check** — calls `get_risk` (and `get_health` for refactors) before editing to assess blast radius.
- **Change review** — for a PR / branch / working-tree diff, combines `get_change_risk` (whole-change score) with `get_risk`'s per-file `directive` block (will-break / missing co-changes / missing tests).
- **Code health** — answers quality / complexity / "what to refactor" via `get_health`.
- **Architectural decisions** — queries `get_why` for the *why* before architectural changes.
- **Dead-code cleanup** — uses `get_dead_code`, conservatively, during cleanup.
- **Settings** — lists, explains and (after asking you) changes Repowise settings with `repowise config`.

### MCP tools (10 flagship, `list_repos` in a workspace)

Registered automatically when the plugin is enabled. Claude Code's default
single-repo surface is the ten flagship tools below; a workspace adds
`list_repos` (see
[MCP_TOOLS.md](../../docs/agent/MCP_TOOLS.md)):

| Tool | What it answers |
|------|-----------------|
| `get_overview` | Architecture summary, module map, entry points, git health |
| `get_answer` | Cited, synthesised answer to a code question + a calibrated confidence |
| `get_context` | Triage card (docs, signatures, hotspot bit, callers, decisions) for files/modules/symbols |
| `get_symbol` | Raw source of one symbol with exact line bounds |
| `search_codebase` | Hybrid code search — `mode="auto"` routes identifiers to indexed symbols, paths to files, prose to semantic wiki search |
| `get_risk` | Per-file hotspot, dependents, co-changes, owners; PR `directive` block with `changed_files` |
| `get_change_risk` | Health delta of a change, its size-and-spread percentile and the touched files' bug-fix record |
| `get_why` | Architectural decisions — search, path-anchored, or health dashboard |
| `get_dead_code` | Unused/unreachable findings tiered by confidence |
| `get_health` | 1–10 code-health score and marker findings per file |

## Setup modes

| Mode | What you get | Requirements |
|------|-------------|-------------|
| **Default (keyless)** | Graph + Git + Code Health + Dead Code + a full wiki rendered from structure | Nothing |
| **Model-written wiki** | Same, with LLM-written pages and decision mining | Provider key |
| **Local (Ollama)** | Model-written wiki, fully offline | Ollama running |

The keyless default makes zero LLM calls and still produces a complete wiki; a
provider key changes who writes the pages, not whether you get them, and that
layer can continue in the background. Run `/repowise:init` and Claude helps
you choose.

## Proactive context (hooks)

`hooks/hooks.json` registers five command hooks. Each runs a console script
of the `repowise` you installed (`repowise-augment` or `repowise-rewrite`)
when it is on PATH, and does nothing when it is not. The plugin installs no
package for them. Claude Code hands each hook the call it fired on as JSON on
stdin; this is what each one does with it:

| Event and matcher | Runs | What it does with the call it sees |
|---|---|---|
| `SessionStart` (`startup`, `resume`, `clear`) | `repowise-augment` | Reads the session's working directory and the local index, and adds a short block for Claude: how fresh the index is and how to use the tools. |
| `PostToolUse` after `Grep`, `Glob`, `Read`, `Edit`, `Write` and repowise MCP calls | `repowise-augment` | Reads the tool name, its arguments (a pattern or a path) and the size and shape of its result, and looks them up in the local index. Silent unless it has something to add: the closest indexed symbol for a search that found nothing, a ranking for a flood of matches, a stale-file or governing-decision note for a read or an edit. |
| `PostToolUse` after `Bash` and `PowerShell` | `repowise-augment`, only past a shell filter | The shell reads the payload and starts nothing unless it names `git commit`, `git pull`, `git merge`, `git rebase` or `git cherry-pick`. For those it hands the payload over, and repowise checks whether the index is now behind `HEAD` and, after a commit, whether a decision is worth recording. |
| `PostToolUseFailure` after `Read`, `Edit`, `Write`, `Grep`, `Glob`, `NotebookEdit` | `repowise-augment` | Reads the failed call's path or pattern and suggests the indexed path it most likely meant. |
| `PreToolUse` on `Bash` and `PowerShell` | `repowise-rewrite` | Reads the command about to run. A recognized noisy command (tests, builds, git, searches) is wrapped in `repowise distill`, so Claude sees errors first and a short summary; any other command passes unchanged. A repo turns it off with `distill.commands.enabled: false` in `.repowise/config.yaml`. |

The hooks make no model calls. They read your repo, its `.repowise/` index and
Claude Code's `settings.json` (to tell whether the rewrite hook is installed),
write what they add to stdout for Claude, and count each run in a ledger in
the repo's `.repowise/` folder. They send nothing beyond
the CLI's anonymous usage telemetry described under
[What this plugin runs and sends](#what-this-plugin-runs-and-sends).
(`repowise init` installs the same hooks in `~/.claude/settings.json`; running
both is safe, since duplicate enrichment is de-duplicated.)

## Requirements

- Python 3.11+
- Git (for the git-intelligence layer)
- The `repowise` CLI on PATH (`/repowise:init` installs it)
- [uv](https://docs.astral.sh/uv/), which starts the plugin's MCP server

## API keys

Most of Repowise needs no key. In the MCP server, semantic search uses a
Gemini or OpenAI key, and `get_answer` writes its answer with the provider your
repo configures (Gemini, OpenAI or Anthropic) when it has that provider's key.
Save a key in the plugin's settings (`/plugin`, then Repowise, then
Configure): the field is masked and the key is kept in your system keychain.
`server.py` hands it to the MCP server under its usual name; Claude Code also
exports plugin options to this plugin's hook processes as
`CLAUDE_PLUGIN_OPTION_*`, which the hooks do not read. A field left empty
changes nothing. For `repowise init` and `repowise generate`, which run in your
terminal, let `repowise init` ask for the key and save it in `.repowise/.env`
(it also adds the gitignore rule), or write that file yourself, for example
`GEMINI_API_KEY=YOUR_API_KEY`, and keep it out of git. Never paste a key into
the chat.

## Troubleshooting

**MCP tools not connecting:** run `/repowise:init`. The plugin registers the
MCP server, but it needs `uv` and the `repowise` binary installed and on PATH.
`/mcp` shows the server's error, for example `repowise is not installed or not
on PATH`.

**`pip install` fails on Windows:** try `python -m pip install repowise`.

**Semantic search / `get_answer` returns nothing:** the wiki may be
template-rendered (no embeddings). Run `/repowise:reindex`, or `repowise generate`
to upgrade pages with a model.

**Stale results after code changes:** run `/repowise:update`.

## What this plugin runs and sends

The plugin itself is Markdown (commands and skills), a `hooks.json`, an
`.mcp.json`, and a small `server.py` with its uv project
files. Everything else it runs comes from the `repowise` CLI you install
(`uv tool install repowise`, pipx or pip):

- **MCP server:** `.mcp.json` runs `uv run --project ${CLAUDE_PLUGIN_ROOT}
  --frozen --isolated --no-dev --no-build --no-config --no-python-downloads
  ${CLAUDE_PLUGIN_ROOT}/server.py`. The plugin root is a uv project whose
  committed `uv.lock` pins no packages, so this installs nothing and reads no
  package index. `server.py` uses only the Python standard library: it starts
  `repowise mcp` from your install on the same stdin and stdout, and hands it
  the keys you saved in the plugin's settings (see [API keys](#api-keys)). The
  server reads the index in your repo's `.repowise/` folder.
- **Hooks:** the five hook entries run `repowise-augment` or
  `repowise-rewrite` when they are on PATH and do nothing otherwise. They read
  the local index only; [the hooks table](#proactive-context-hooks) says what
  each does with the calls it sees.

Network traffic from the `repowise` CLI and MCP server:

- **Anonymous usage telemetry** to `https://api.repowise.dev/telemetry/events`:
  command and tool names, coarse counts and timings, and an anonymous install
  id. Never source code, file paths, repo or symbol names, or query text. Turn
  it off with `DO_NOT_TRACK=1` or `REPOWISE_TELEMETRY_DISABLED=1`; details in
  [docs/reference/TELEMETRY.md](https://github.com/repowise-dev/repowise/blob/main/docs/reference/TELEMETRY.md).
- **Your model and embedding provider**, only if you configure one: page
  generation in `init` and `update`, and `get_answer`, send code excerpts and
  your question to that provider. With no provider configured nothing is sent,
  and `get_answer` answers from local retrieval.
- **Version checks and downloads:** the CLI reads the latest version from PyPI,
  and `repowise serve` downloads its web UI from the project's GitHub release.
- **repowise.dev**, only when you run an account command yourself
  (`repowise login`, `repowise publish`, or sending feedback).

## License

AGPL-3.0, same as repowise. See the [repository](https://github.com/repowise-dev/repowise).
