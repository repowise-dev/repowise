# Claude Code Slash Commands

The [Repowise Claude Code plugin](../../plugins/claude-code/README.md) ships 18
slash commands under the `/repowise:` namespace. Install the plugin with
`/plugin install repowise@repowise` (see the plugin README), then run
`/repowise:init` to index the repo.

## How they work

- Each command is a Markdown instruction file in
  [`plugins/claude-code/commands/`](../../plugins/claude-code/commands/). Claude
  reads it, runs the matching `repowise` CLI command, and presents the result.
- The file's `allowed-tools:` frontmatter scopes what Claude may run: each
  command allows only its own CLI subcommand (for example
  `Bash(repowise health:*)`) plus `Read`.
- `/repowise:init` also allows the install commands (`uv tool install`,
  `pipx install`, `pip install`) and `AskUserQuestion`, so it can install
  repowise and ask about your setup. `/repowise:reindex` also allows
  `AskUserQuestion`.
- Text after the command name reaches the command as `$ARGUMENTS`:

```
/repowise:search authentication middleware
/repowise:context src/auth/token.py
/repowise:dead-code safe
/repowise:risk main..HEAD
```

## Command reference

| Command | What it does |
|---------|--------------|
| `/repowise:init` | Install repowise if needed, ask about your preferences, and index the repo |
| `/repowise:status` | Index health: sync state, page counts, provider, token usage |
| `/repowise:update` | Incremental update to bring the index up to recent commits |
| `/repowise:search` | Search the wiki by natural language, full text, or symbol |
| `/repowise:ask` | Cited answer to a codebase question with a confidence rating (one LLM call) |
| `/repowise:context` | Triage card for files, modules, or symbols: layer, hotspot, fix history, freshness |
| `/repowise:symbol` | One symbol's body with live-verified line bounds (`path::Name`, a line range, or a distill omission ref) |
| `/repowise:reindex` | Re-embed every wiki page into the vector store. Embedding calls only, no LLM calls |
| `/repowise:health` | Code health: KPIs, lowest-scoring files, refactoring targets, trends, per-file markers |
| `/repowise:coverage` | Ingest or inspect coverage reports (LCOV, Cobertura, Clover, Go, JaCoCo, Apex, repowise JSON; a coverage.py `.coverage` file builds the per-test map), or gate a change on patch coverage |
| `/repowise:impacted-tests` | Tests whose coverage touches a change's lines, for a commit, a `base..head` range, or the staged diff |
| `/repowise:risk` | Rank a change against recent history: repo-relative percentile plus a supporting diff-shape score |
| `/repowise:security` | Security signals. Working-tree scanning runs during `init` and `update`; `--history` walks the full git history for leaked secrets |
| `/repowise:dead-code` | Unreachable files, unused exports, and zombie packages, tiered by confidence |
| `/repowise:export` | Export the wiki or architecture model to Markdown, HTML, JSON, or Structurizr DSL |
| `/repowise:decision` | List, inspect, add, or confirm architectural decisions |
| `/repowise:why` | Why the code is shaped this way: decisions, rationale, git archaeology |
| `/repowise:doctor` | Diagnose the setup (install, API keys, index and store drift) and optionally repair it |

## Slash commands and MCP tools

- **Slash commands** are for you: run one when you want a specific report, such
  as dead code before a cleanup or risk before a merge.
- **MCP tools** are for Claude: it calls them on its own while working, for
  example `get_risk` before editing a hotspot. See [MCP_TOOLS.md](MCP_TOOLS.md).

Both read the same `.repowise/` index. The command files add presentation
rules on top of the CLI output, such as showing the confidence with every
`/repowise:ask` answer and treating only `safe_to_delete` findings as
deletions in `/repowise:dead-code`.
