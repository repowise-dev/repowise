# Claude Code Slash Commands

The [Repowise Claude Code plugin](../../plugins/claude-code/README.md) ships 18
slash commands under the `/repowise:` namespace. They are installed automatically
when you run `repowise init` and Claude Code loads the plugin. Each command is a
Markdown instruction file under
[`plugins/claude-code/commands/`](../../plugins/claude-code/commands/); Claude
Code renders them as structured agent instructions, not bare shell scripts.

## How they work

Each command instructs Claude to run one or more `repowise` CLI commands and
present the result in a specific way. Claude Code's tool permissions per command
are declared in the file's YAML frontmatter (`allowed-tools:`). All commands
that perform read-only operations restrict themselves to `Bash` and `Read`;
`/repowise:init` additionally uses `Write` and `AskFollowupQuestion` because it
may create files and needs to ask about your preferences.

Most commands accept free-form `$ARGUMENTS` that are forwarded to the underlying
CLI invocation. For example:

```
/repowise:search authentication middleware
/repowise:context src/auth/token.py
/repowise:dead-code safe
/repowise:risk --base main
```

## Command reference

| Command | Description |
|---------|-------------|
| `/repowise:init` | Set up Repowise for this codebase. Installs if needed, asks about your preferences, and runs the indexing |
| `/repowise:status` | Check the health of your Repowise index — sync state, page counts, provider, and token usage |
| `/repowise:update` | Trigger an incremental Repowise update to sync documentation with recent code changes |
| `/repowise:search` | Search the Repowise wiki using natural language, full-text, or symbol search |
| `/repowise:ask` | Ask a codebase question and get a cited, synthesised answer with a confidence rating (costs an LLM call) |
| `/repowise:context` | Triage card for files, modules, or symbols — layer, hotspot, fix history, freshness (relationships, not source bytes) |
| `/repowise:symbol` | Read one symbol's body with live-verified line bounds (`path::Name`, live range, or distill omission ref) |
| `/repowise:reindex` | Rebuild the Repowise vector store by re-embedding all wiki pages. No LLM calls — only embedding API calls |
| `/repowise:health` | Show Repowise code-health — KPIs, lowest-scoring files, refactoring targets, trends, or per-file markers |
| `/repowise:coverage` | Ingest or inspect test-coverage reports — LCOV, Cobertura/Clover, or coverage.py `.coverage` (builds the per-test map when contexts are present) |
| `/repowise:impacted-tests` | Print the tests whose coverage intersects a change's changed lines — for a commit, `base..head` range, or staged diff |
| `/repowise:risk` | Rank a live change for review using a repo-relative percentile and an auditable supporting diff-shape score |
| `/repowise:security` | Scan for security signals — working-tree scanning already runs during `init`/`update`; use `--history` to walk full git history for leaked secrets |
| `/repowise:dead-code` | Report unreachable files, unused exports, and zombie packages, tiered by confidence |
| `/repowise:export` | Export the wiki (or architecture model) to Markdown, HTML, JSON, or Structurizr DSL |
| `/repowise:decision` | Work with architectural decisions — list, inspect health, add, or confirm auto-proposed decisions |
| `/repowise:why` | Why the code is shaped this way — decisions, rationale, and git archaeology (question, path, or decision-health dashboard) |
| `/repowise:doctor` | Diagnose the Repowise setup — install, API keys, index/store drift — and optionally repair it |

## Relationship to MCP tools

Slash commands and MCP tools overlap intentionally but serve different workflows:

- **Slash commands** are *user-initiated*. You call them explicitly when you
  want a specific report — dead code before a cleanup, risk before a merge, etc.
- **MCP tools** are *agent-initiated*. Claude calls them autonomously during
  problem-solving (e.g. calling `get_risk` before editing a hotspot file).

Most commands are thin wrappers over the same `repowise` CLI that the MCP tools
call internally, so the results are identical. The command layer adds
presentation logic: confidence framing for `/repowise:ask`, safe-deletion
guardrails for `/repowise:dead-code`, and formatting guidance for each output.

For the full MCP tool reference see [MCP_TOOLS.md](MCP_TOOLS.md).

## Adding or customising commands

The command files live in
[`plugins/claude-code/commands/`](../../plugins/claude-code/commands/) inside
this repository. Each file is a self-contained Markdown document with a YAML
frontmatter block. To override a command for a specific project, place a file
with the same name under `.claude/commands/repowise/` in your repo root —
Claude Code will use the local file in preference to the plugin's version.

To add a new project-level command that does not exist in the plugin, create
`.claude/commands/<name>.md`. See
[Claude Code's slash command documentation](https://docs.claude.com/en/docs/claude-code/slash-commands)
for the full authoring reference.
