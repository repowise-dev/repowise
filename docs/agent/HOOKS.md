# Hooks

repowise installs a set of lightweight hooks so context reaches your agent, and
your index stays fresh, with zero effort on your part. They fall into two
families:

- **Git hooks** keep the wiki and graph in sync with your code.
- **Agent hooks** feed graph, git, health, and decision context into Claude Code
  and Codex at the exact moments the agent needs it.

Every agent hook shares the same guarantees: **no LLM calls, no network**, only
local SQLite (`wiki.db`) and `git` reads. They are import-isolated (cold start
under ~500ms), and any failure exits `0` silently, so a broken environment never
crashes or blocks your agent.

---

## At a glance

| Hook | Family | Installed by | Fires on | What it does |
|------|--------|--------------|----------|--------------|
| **Post-commit auto-sync** | git | `repowise hook install` (or the `repowise init` prompt) | every `git commit` | Runs `repowise update` in the background so the wiki tracks your code |
| **SessionStart context** | Claude Code | `repowise init` | session `startup` / `resume` / `clear` | Live index-freshness line, core-tool trust rule, and the standing decisions relevant to this session |
| **PostToolUse enrichment** | Claude Code | `repowise init` | `Grep` / `Glob` / `Read` / `Edit` / `Write` / `Bash` / `PowerShell` / repowise MCP calls | Graph context on searches, git/edit freshness, read-intelligence notices, and edit-time "governed by" decision notices |
| **Command-rewrite (distill)** | Claude Code | `repowise hook rewrite install` (opt-in) | `Bash` / `PowerShell` | Rewrites noisy commands to `repowise distill <cmd>`, pending your approval |
| **Codex context + staleness** | Codex | `repowise init --codex` | SessionStart / UserPromptSubmit / edit / Bash | Reminds Codex to use the MCP tools and flags stale context after edits |

---

## Git hook: post-commit auto-sync

The wiki, graph, and health scores are only as current as your last index. The
post-commit hook closes that gap: after every commit it runs `repowise update`
in the background, so documentation, dependency edges, and code-health follow
your code without you thinking about it. Your terminal is never blocked.

```bash
repowise hook install              # install for the current repo
repowise hook install --workspace  # install for all repos in the workspace
repowise hook status               # check whether the hook is installed
repowise hook uninstall            # remove it
```

The hook is **marker-delimited**, so it coexists safely with other tools' hooks
(linters, formatters, commit-msg checks) in the same `post-commit` file: repowise
only ever touches the block between its own markers. See
[AUTO_SYNC.md](../scale/AUTO_SYNC.md) for the full sync model, including how git worktrees
seed from the base checkout.

> Prefer to keep updates manual? Skip this hook and run `repowise update`
> yourself. The agent hooks below will remind you when the index falls behind.

---

## Claude Code agent hooks

Installed automatically during `repowise init` into your global
`~/.claude/settings.json`. Existing user hooks are always preserved, and legacy
repowise entries are migrated in place on the next `init` / `update`. All of them
route through the `repowise-augment` console script (a standalone entry point
that does not load the full CLI).

### SessionStart, live freshness + relevant decisions

The generated `CLAUDE.md` is static between reindexes, so it can't say whether
the index is current *right now*. This hook adds a short per-session block so the
agent starts with calibrated trust instead of discovering staleness mid-task:

- **Index current** → one line saying so, plus the core-tool pointer.
- **Update running** → a positive "catching up" notice (never a stale scare).
- **Index behind** → indexed vs `HEAD` with a changed-file count, and the
  target-scoped trust rule (a `stale_warning` fires only when a file a response
  actually served has changed).

It also carries the **relevance-ranked standing decisions** for this session.
repowise scores the repo's active decisions against the session's likely working
set (dirty and staged files, files changed on the branch vs `main`, the previous
session's edited files, and branch-name tokens), expanded one hop through import
edges and co-change partners. The top few land under a hard ~400-token cap.
Relevance or silence: nothing clears the floor, nothing is injected, and
decisions are never shown just for being high-confidence. Repo-wide rules mined
from your own corrections are the one exception: a rule like "use the shared
logger, not print" applies everywhere rather than to specific files, so it
competes at a flat base relevance.

### PostToolUse, enrichment on every tool call

One hook covers several jobs, matched on
`Grep`, `Glob`, `Read`, `Edit`, `Write`, `Bash`, `PowerShell`, and repowise MCP
calls:

**Grep/Glob enrichment.** When Claude Code runs a broad or zero-result search,
repowise appends focused context pulled straight from `wiki.db`:

| Field | What it tells the agent |
|-------|------------------------|
| **Symbols** | Functions, classes, and methods defined in the file |
| **Imported by** | Which files depend on this file (reverse dependency) |
| **Depends on** | What this file imports (forward dependency) |
| **Git signals** | Hotspot status, bus factor, and owner |

So an agent that greps for `PageGenerator` immediately knows what depends on it,
what it depends on, and that it is a hotspot, without a separate MCP call:

```
[repowise] 2 related file(s) found:

  packages/core/.../page_generator.py
    Symbols: function:_now_iso, class:PageGenerator, method:__init__
    Imported by: init_cmd.py, update_cmd.py, generation/__init__.py
    Depends on: context_assembler.py, base.py, models.py
    Git: HOTSPOT, bus-factor=1, owner=RaghavChamadiya
```

**Git/edit freshness.** After a successful `git commit`, `merge`, `rebase`,
`cherry-pick`, or `pull`, repowise compares `HEAD` against the last indexed commit
in `.repowise/state.json` and, if the wiki is behind, reminds the agent to run
`repowise update` so it never silently works from outdated docs.

**Read-intelligence.** On `Read` of an indexed file, repowise can nudge the agent
toward the cheaper `get_context(..., include=["skeleton"])` for structure-level
questions, and emit a per-file stale-read notice when the file changed after
indexing.

**Edit-time "governed by" decisions.** When the agent edits a file governed by an
architectural decision (via `decision_node_links`), it gets a one-line notice
with the rationale, at most once per session per decision and only a few times
per session total. This is how a decision reaches the agent at the moment it is
about to violate (or honor) it.

Every injected decision id is recorded locally in
`.repowise/sessions/sessions.db`. On the next `repowise update`, the session miner
checks whether the guidance was followed or contradicted by your corrections in
that session, and relaxes or bumps the decision's staleness accordingly, so
guidance that stops being true stops being injected. This is the feedback loop
behind "learns from your sessions" (see the [README](../../README.md) and
[decisions layer](../layers/INTELLIGENCE_LAYERS.md)).

---

## Command-rewrite hook (distill), opt-in

Most of what an agent reads from a shell command is noise: 300 lines of passing
tests around 4 failures, full commit bodies for "what changed recently". The
rewrite hook intercepts noisy `Bash` / `PowerShell` commands and rewrites them to
[`repowise distill <cmd>`](DISTILL.md), which compresses the output errors-first
before the agent reads it, exit code preserved and every omission reversible.

```bash
repowise hook rewrite install     # or answer Yes at the `repowise init` prompt
repowise hook rewrite status
repowise hook rewrite uninstall
```

- Defaults to **`ask`**, so you approve every rewritten command before it runs.
- Never rewrites pipes, compound commands, or watch modes.
- Installing also adds `Bash(repowise distill:*)` / `PowerShell(repowise distill:*)`
  to `permissions.allow`, so an already-approved command family doesn't start
  re-prompting just because its string changed.

Per-repo behavior lives under `distill.commands` in `.repowise/config.yaml`
([CONFIG.md](../reference/CONFIG.md)). Track what it saved with `repowise saved`.

---

## Codex hooks

Written to project-local `.codex/hooks.json` by `repowise init --codex` (they do
not touch your global `~/.codex/config.toml`):

- **SessionStart / UserPromptSubmit** → a short developer note reminding Codex to
  use the repowise MCP tools for architecture, search, risk, decisions, and
  dead-code analysis.
- **PostToolUse** (`Bash`, `apply_patch` / `Edit` / `Write`) → flags that indexed
  context may be stale after edits or git operations, pointing at `repowise
  update`.

Full Codex setup: [CODEX.md](CODEX.md).

---

## What gets written where

`repowise init` writes these entries into `~/.claude/settings.json` (Claude Code)
and `.codex/hooks.json` (Codex when `--codex` is passed):

| Client | Hook type | Matcher | Command |
|--------|-----------|---------|---------|
| Claude Code | `SessionStart` | `startup\|resume\|clear` | `repowise-augment` |
| Claude Code | `PostToolUse` | `Bash\|PowerShell\|Grep\|Glob\|Read\|Edit\|Write\|mcp__.*[Rr]epowise.*__.*` | `repowise-augment` |
| Claude Code | `PreToolUse` (opt-in) | `Bash\|PowerShell` | `repowise-rewrite` |
| Codex | `SessionStart` / `UserPromptSubmit` | lifecycle | context reminder |
| Codex | `PostToolUse` | `Bash`, `apply_patch\|Edit\|Write` | staleness check |

`SessionStart` deliberately excludes `compact`: the block usually survives
compaction in the summary, and re-emitting it there would double it up. `init`
also sets `env.ENABLE_TOOL_SEARCH=true` so the MCP tool schemas load on demand
rather than sitting in every session's standing context (an existing value you
set, including a deliberate `false`, is left untouched).

For manual debugging, the underlying entry points can be run directly:

```bash
repowise-augment    # invoked by the agent hooks; prints what it would inject
repowise augment    # equivalent Click subcommand
```

---

## Hooks vs MCP tools

The two are complementary:

- **Hooks** are passive, automatic, and cost the agent nothing. They fire on
  every search, edit, or session start whether or not the agent is thinking about
  graph context.
- **[MCP tools](MCP_TOOLS.md)** are active and on-demand, with richer output.
  Reach for them when the agent needs full documentation, a risk assessment,
  decision history, or dependency tracing.

For most day-to-day coding, the hooks supply enough context on their own; the MCP
tools are there for deeper investigation.
