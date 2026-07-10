---
layout: default
title: Getting Started
nav_order: 2
---

# Getting Started
{: .no_toc }

The 60-second path to codebase intelligence.
{: .fs-6 .fw-300 }

---

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Quick start (under 5 minutes, no API key)

*Index once, and give your agent the dependency graph + git history + code-health — not 40 greps.*

**1. Install**

```bash
pip install repowise          # Windows: python -m pip install repowise
repowise --version            # -> repowise, version 0.27.x
```

**2. Index your repo — no LLM, no key**

```bash
cd /path/to/your/repo
repowise init --index-only -y
```

Builds the dependency graph, git history, code-health score, and dead-code findings in seconds. (Want the generated wiki + semantic search? Use `repowise init --provider gemini|anthropic|openai` with the matching key.)

**3. Connect your agent** — the MCP server is `repowise mcp`, served from the repo dir.

<details markdown="block"><summary><b>Claude Code</b></summary>

```bash
# Plugin (adds 9 tools + slash commands + skills):
/plugin marketplace add repowise-dev/repowise
/plugin install repowise@repowise

# …or wire the MCP server directly:
claude mcp add repowise -- repowise mcp
```
Or commit a project `.mcp.json`:
```json
{ "mcpServers": { "repowise": { "command": "repowise", "args": ["mcp"] } } }
```
</details>

<details markdown="block"><summary><b>Codex CLI</b></summary>

Add to `~/.codex/config.toml`:
```toml
[mcp_servers.repowise]
command = "repowise"
args = ["mcp"]
```
Or: `codex mcp add repowise -- repowise mcp`
</details>

**4. First real call.** Ask your agent: *"Use repowise `get_overview` to summarize this repo,"* or *"`get_context` for `src/auth.py`."* You get graph-grounded architecture and per-file triage instead of a flurry of greps. ✅

> `get_overview` / `get_context` work in **index-only mode** (no key) — they synthesize from the graph/git/health layers. `search_codebase` / `get_answer` / `get_why` need full mode (the generated wiki).

Ready for the full picture? Run `repowise init --provider …` for the generated wiki + semantic search, or skip key management entirely with the [hosted tier](https://www.repowise.dev). The detailed setup paths below walk through each editor and mode.

---

## Choose your path

There are three ways to set up repowise. Pick the one that fits how you work.

| Path | Best for | Time |
|------|----------|------|
| [Claude Code Plugin](#path-1-claude-code-plugin) | Claude Code users | ~30 seconds |
| [pip + Claude Code](#path-2-pip--claude-code) | Manual control + Claude Code | ~2 minutes |
| [pip + Codex](#path-3-pip--codex) | Codex CLI users | ~2 minutes |
| [Standalone CLI](#path-4-standalone-cli) | No AI editor, or CI use | ~2 minutes |

---

## Path 1: Claude Code Plugin

**The recommended path.** Claude handles everything — installation, API key setup, indexing, and MCP registration.

### Step 1: Install the plugin

Open Claude Code and run:

```
/plugin marketplace add repowise-dev/repowise-plugin
/plugin install repowise@repowise
```

### Step 2: Index your repo

Navigate to your project directory and run:

```
/repowise:init
```

Or just ask Claude naturally:

```
set up repowise for this repo
```

### What happens

Claude walks you through:

1. **Mode selection** — full documentation, index-only (free, no LLM), or advanced
2. **Provider setup** — choose Anthropic, OpenAI, Gemini, or local Ollama
3. **API key** — entered once, saved to `.repowise/.env` (gitignored automatically)
4. **Indexing** — parses your code, runs git analysis, generates docs

When it finishes, the MCP server is registered and Claude can query your codebase directly. You'll see a progress bar as each phase completes.

### What you see

```
 repowise
─────────────────────────────────────────────
 Indexing your codebase...

 Phase 1 of 4 — Ingestion
  Scanning & filtering files...      [████████████████████]
  Scanned 1,247 files, 247 included
    Excluded: 820 by .gitignore, 45 by extension, 12 binary, 123 unknown type
    Languages: python 892, typescript 245, go 80, yaml 18, json 12
  Parsing files...                   [████████████████████] 247/247
  Building dependency graph...       done

 Phase 2 of 4 — Analysis
  Indexing file history...           done
  Detecting dead code...             done
  Extracting decisions...            done

 Phase 3 of 4 — Generation
  Languages: python 58%, typescript 28%, go 10%, yaml 2%, json 1%
  Generating wiki pages...           [████████████████░░░░] 82%
  Estimated tokens: ~180k

 Phase 4 of 4 — Persistence
  Writing to database...             done
  Building vector index...           done
  Generating CLAUDE.md...            done

 Done. 247 pages generated.
─────────────────────────────────────────────
```

---

## Path 2: pip + Claude Code

Install manually, then connect Claude Code to the MCP server.

### Step 1: Install repowise

```bash
pip install repowise
```

### Step 2: Set your API key

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# or: export OPENAI_API_KEY="sk-..."
# or: export GEMINI_API_KEY="AI..."
```

### Step 3: Index your repo

```bash
cd /path/to/your-repo
repowise init
```

repowise detects your API key automatically and starts the interactive setup. Follow the prompts — it takes about 2 minutes for a medium-sized codebase.

### Step 4: Connect Claude Code

After `init` completes, repowise writes a `.mcp.json` file at your repo root. Claude Code picks this up automatically next time you open the project.

To verify the MCP server is connected:

```
/repowise:status
```

---

## Path 3: pip + Codex

Use the local Codex CLI auth/subscription flow and project-local MCP/hooks config.

### Step 1: Install repowise and Codex

```bash
pip install repowise
npm install -g @openai/codex
codex login
codex login status
```

### Step 2: Index your repo

```bash
cd /path/to/your-repo
repowise init --codex --provider codex_cli --yes
```

This writes `.codex/config.toml`, `.codex/hooks.json`, and managed `AGENTS.md` in the repository. It does not edit global `~/.codex/config.toml`.

### Step 3: Start Codex

```bash
codex
```

Codex reads `AGENTS.md`, starts the Repowise MCP server from project config, and receives lightweight lifecycle/freshness hooks. See [Codex Integration](codex).

---

## Path 4: Standalone CLI

Use repowise without Claude Code — with the web dashboard, REST API, or any MCP-compatible editor.

### Step 1: Install and set up

```bash
pip install repowise
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Step 2: Index your repo

```bash
cd /path/to/your-repo
repowise init
```

### Step 3: Start the web dashboard

```bash
repowise serve
```

Open [http://localhost:3000](http://localhost:3000) in your browser. You'll see the full wiki, dependency graph, search, and more.

### Step 4: Start the MCP server (optional)

To connect any MCP-compatible editor (Codex, Cursor, Cline, Windsurf):

```bash
repowise mcp
```

Then add the MCP server to your editor's configuration. See [MCP Server →](mcp-server) for editor-specific setup.

---

## Analysis-only mode (no API key needed)

If you don't have an LLM API key — or just want the git intelligence and dead code detection without generated docs — use `--index-only`:

```bash
repowise init --index-only
```

This runs the full ingestion and analysis pipeline (parsing, graph, git, dead code) but skips LLM generation. It's free, takes under a minute for most repos, and still gives you:

- Dependency graph
- Hotspot detection
- Dead code findings
- Ownership data

You can always run `repowise init` later to add docs on top of an existing index.

---

## Keeping docs in sync

After the initial index, keep your wiki current with:

```bash
repowise update          # Sync changed files since last commit
repowise watch           # Auto-update on file saves
```

Or in Claude Code, run `/repowise:update` — Claude detects changed files and regenerates only the affected pages.

---

## Next steps

- [Core concepts →](concepts) — understand the four layers
- [CLI reference →](cli-reference) — all commands and flags
- [MCP server →](mcp-server) — connecting to Claude Code, Cursor, Cline
