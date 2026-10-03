# Hooks

repowise installs small hooks so context reaches your agent and your index
stays fresh without you asking. There are two families:

- **Git hooks** keep the wiki and graph in step with your code.
- **Agent hooks** feed graph, git, health and decision context into Claude Code
  and Codex at the moment the agent needs it.

Every agent hook makes **no LLM calls and no network calls**. It reads the local
index (`wiki.db`) and `git`. Any failure exits `0` silently, so a broken
environment never blocks your agent.

For the exact settings entries each install writes, see the
[hooks reference](../reference/HOOKS_REFERENCE.md).

---

## At a glance

| Hook | Family | Installed by | Fires on | What it does |
|------|--------|--------------|----------|--------------|
| **Post-commit auto-sync** | git | `repowise init` by default (`--no-hook` skips it), or `repowise hook install` | every `git commit` | Runs `repowise update` in the background so the wiki tracks your code |
| **Pre-commit security check** | git | `repowise hook install --security` (opt-in) | every `git commit` | Runs `repowise security check --staged` and blocks a commit that adds a finding at or above `high` ([details](../layers/SECURITY.md#before-a-commit---staged)) |
| **SessionStart context** | Claude Code | `repowise init` | session `startup` / `resume` / `clear` | Index-freshness line, core-tool pointer, and the standing decisions relevant to this session |
| **PostToolUse enrichment** | Claude Code | `repowise init` | `Grep` / `Glob` / `Read` / `Edit` / `Write` / repowise MCP calls | Graph context on searches, read notices, edit-time decision and bug-history notices |
| **Wrong-path rescue** | Claude Code | `repowise init` | a path tool call that failed on a path this tree does not have | Names the file when exactly one indexed file carries that basename; silent otherwise |
| **Coverage re-ingest** (opt-in) | Claude Code | `hooks.coverage_reingest: true`, then the next `repowise coverage add` / `init` / `update` | `Bash` / `PowerShell` | Re-ingests a fresh full-suite coverage report in the background |
| **Command rewrite (distill)** | Claude Code, Codex | `repowise hook rewrite install`, or Yes at the `repowise init` prompt | `Bash` / `PowerShell` | Rewrites noisy commands to `repowise distill <cmd>` |
| **Codex context and staleness** | Codex | `repowise init --codex` | SessionStart, edits, shell | Reminds Codex to use the MCP tools and flags stale context after git operations |

Each agent hook records what it said and whether the agent acted on it. See
[Hook efficacy](#hook-efficacy-repowise-hook-stats).

---

## Git hook: post-commit auto-sync

The wiki, graph and health scores are only as current as your last index. After
every commit this hook runs `repowise update` in the background, so docs,
dependency edges and health follow your code. Your terminal is never blocked.

```bash
repowise hook install              # current repo
repowise hook install --workspace  # every repo in the workspace
repowise hook status
repowise hook uninstall
```

The hook is marker-delimited, so it coexists with other tools' hooks in the same
`post-commit` file: repowise only touches the block between its own markers. See
[Keeping the index fresh](../scale/AUTO_SYNC.md) for every sync method, and
[WORKTREES.md](../scale/WORKTREES.md) for how worktrees seed from the base checkout.

Prefer manual updates? Skip this hook and run `repowise update` yourself. The
SessionStart hook tells the agent when the index falls behind.

---

## Claude Code agent hooks

`repowise init` installs these into your global `~/.claude/settings.json`.
Existing user hooks are preserved, and older repowise entries are updated in
place on the next `init` or `update`. They all run the `repowise-augment` entry
point, which does not load the full CLI.

`repowise init --no-editor-setup` skips this group, along with the MCP server
registration in the same file. Use it for a scratch clone, a worktree or a
benchmark loop where you do not want machine-wide config to change.
`REPOWISE_SKIP_EDITOR_SETUP=1` does the same in CI. The index is identical
either way; re-run `repowise init` without the flag to register the repo later.

### SessionStart: freshness and relevant decisions

The generated `CLAUDE.md` is static between reindexes, so it cannot say whether
the index is current right now. This hook adds a short block at session start:

- **Index current**: one line saying so, plus the core-tool pointer.
- **Update running**: a "catching up" notice.
- **Index behind**: indexed commit vs `HEAD` with a changed-file count, and the
  trust rule (a `stale_warning` fires only when a file a response served has
  changed).

It also carries the **standing decisions** most relevant to this session.
repowise scores active decisions against the likely working set (dirty and
staged files, files changed on the branch vs `main`, the previous session's
edited files, branch-name tokens), expanded one hop through imports and
co-change partners. The top few land under a hard 400-token cap. If nothing
clears the relevance floor, nothing is injected. Working agreements mined from
your own corrections (for example "use the shared logger, not print") name no
file, so they compete at a flat base relevance.

### PostToolUse: enrichment on tool calls

One hook, matched on `Grep`, `Glob`, `Read`, `Edit`, `Write` and repowise MCP
calls, does several jobs.

**Search enrichment.** On a broad or zero-result search, repowise appends
context from the index:

| Field | What it tells the agent |
|-------|------------------------|
| **Symbols** | Functions, classes and methods defined in the file |
| **Imported by** | Files that depend on this file |
| **Depends on** | What this file imports |
| **Git** | Hotspot status, bus factor and owner |

```
[repowise] 2 related file(s) found:

  src/billing/invoice_service.py
    Symbols: class:InvoiceService, method:__init__, function:_now_iso
    Imported by: api/routes.py, jobs/reconcile.py
    Depends on: models.py, tax.py
    Git: HOTSPOT, bus-factor=1, owner=alice
```

**Search-flood digests.** A grep that returns 50 or more lines also gets a
per-file digest: each matched file with its match count and anchor line
numbers, ranked by graph centrality when the index can rank them, with a
`(N more files, M matches)` tail past the top ten. With `hooks.search_digest:
true`, the digest replaces the raw match list. Single-file context greps (`-C`,
`-A`, `-B`) and `files_with_matches` results are never digested.

**Read notices.** On a `Read` of an indexed file, repowise warns when the file
changed after the session's previous read of it, and points at
`get_context(..., include=["skeleton"])` for structure-level questions.

**Skeleton reads** (opt-in, `hooks.read_skeleton: true`). An unbounded `Read`
of a large indexed file returns the file's skeleton, once per file per session.
Signatures keep their real line numbers; bodies collapse to `... N lines (a-b)`
markers, so the agent can range-read any elided span. Reading the file again
with no range returns it whole. A skeleton read still satisfies Claude Code's
read-before-edit check, so an `Edit` or `Write` on such a file raises a one-line
warning, once per file, until the file is read in full.

**Re-read collapse** (opt-in, `hooks.read_reread: true`). A `Read` of a file
the session already read comes back as a short notice naming the earlier read.
It applies only when the same range was served, no `Edit` or `Write` came
between, and the bytes hash the same. When the bytes differ, the agent gets the
file plus a line saying it changed on disk outside this session. It is never
applied twice in a row for the same file, so one more Read always returns the
content after a context compaction. Because a skeleton read records no content,
this mostly trims small files, unindexed files and third reads of a range.

**Glob timeouts.** On Windows a `Glob` can exhaust its 20-second budget and
return nothing. A glob is a path query and the index holds every path, so
repowise answers it from the index at that moment. Brace expansion (`{a,b}`) is
declined, and zero indexed matches stays silent.

**Edit-time notices.** When the agent edits a file governed by an architectural
decision, it gets a one-line notice with the rationale, once per session per
decision and under a per-session cap. A file with a repeated bug-fix history
gets a one-line heads-up too (see [bug history](../layers/BUG_HISTORY.md)).

Every injected decision id is recorded in `.repowise/sessions/sessions.db`. On
the next `repowise update`, the session miner checks whether your corrections
followed or contradicted that guidance and adjusts the decision's staleness, so
guidance that stops being true stops being injected. See
[decisions](../layers/DECISIONS.md).

The three opt-in read and search keys are written by the rewrite-hook question
in `repowise init`, and toggled per repo with `repowise hook read-skeleton`,
`hook read-reread` and `hook search-digest` (`install | uninstall | status`).
Each one's savings appear in `repowise saved` under its own filter, and a repo
with the key off still gets the counterfactual number. Key details:
[CONFIG.md](../reference/CONFIG.md#the-hooks-block).

### PostToolUseFailure: wrong-path rescue

An agent that guesses the wrong directory for a file gets "Path does not exist"
and burns a turn hunting. The index knows where that filename lives:

```
[repowise] billing/invoice.py is not in this tree.
The only indexed invoice.py is src/billing/invoice.py
```

It speaks only when the basename resolves to exactly one indexed file that is
still on disk. It stays silent for an ambiguous basename, a directory target, a
path in another checkout, a failure Claude Code already answered with its own
"Did you mean", and the path that just failed. Most path-not-found failures
therefore get silence by design.

---

## Command-rewrite hook (distill)

Most of what an agent reads from a shell command is noise. The rewrite hook
rewrites noisy `Bash` / `PowerShell` commands to
[`repowise distill <cmd>`](DISTILL.md), which compresses output errors-first
before the agent reads it, keeps the exit code, and makes every omission
reversible.

```bash
repowise hook rewrite install     # or answer Yes at the `repowise init` prompt
repowise hook rewrite status
repowise hook rewrite uninstall
```

- Rewrites run without a prompt by default (`permission: allow`). This is not a
  permission escalation: a rewrite is always `repowise distill <one recognized
  command>`. Set `permission: ask` under `distill.commands` in
  `.repowise/config.yaml` to approve each one.
- Compound commands, redirections and watch modes are never rewritten. On
  macOS/Linux, a single pipe into `head`, `tail`, `grep` or `rg` is rewritten as
  one quoted command.
- With `permission: ask`, a rewritten string no longer matches allow rules such
  as `Bash(git diff:*)`. `repowise hook rewrite install --allow-rule` adds
  `Bash(repowise distill:*)` / `PowerShell(repowise distill:*)` to cover that.

Codex support, the safety model and the per-repo config are in
[DISTILL.md](DISTILL.md#3-the-command-rewrite-hook-claude-code--codex).

---

## Codex hooks

`repowise init --codex` writes project-local `.codex/hooks.json` (your global
`~/.codex/config.toml` is untouched):

- **SessionStart**: a short note reminding Codex to use the repowise MCP tools,
  plus the same relevance-ranked standing decisions Claude Code receives.
- **PostToolUse** (the shell tool, and `apply_patch` / `Edit` / `Write`): after
  a successful `git commit`, `merge`, `rebase`, `cherry-pick` or `pull`, compares
  `HEAD` with the last indexed commit and flags that context may be stale.

Codex names its shell tool `shell_command` on current releases and `Bash` on
older ones, so the matcher covers both. Codex has no `Read` / `Grep` / `Glob`
tools for repowise to enrich, which is why it watches the shell and Claude Code
does not. Full setup: [CODEX.md](CODEX.md).

---

## Hook efficacy: `repowise hook stats`

The agent hooks keep a local ledger in `.repowise/sessions/sessions.db`: what
each hook said, and whether the agent then did what it pointed at. The verdict
comes from your own Claude Code transcripts, so the numbers are yours. Nothing
leaves the machine.

```bash
repowise hook stats                        # per-surface firing counts and action rates
repowise hook backfill --all-projects      # seed the ledger from existing transcripts
```

Flags and the `--reset` upgrade step: [CLI reference](../reference/CLI_REFERENCE.md#repowise-hook-stats).

---

## What gets written where

| Client | Hook type | Matcher | Written to |
|--------|-----------|---------|------------|
| Claude Code | `SessionStart` | `startup\|resume\|clear` | `~/.claude/settings.json` |
| Claude Code | `PostToolUse` | `Grep\|Glob\|Read\|Edit\|Write\|mcp__.*[Rr]epowise.*__.*` | `~/.claude/settings.json` |
| Claude Code | `PostToolUseFailure` | `Read\|Edit\|Write\|Grep\|Glob\|NotebookEdit` | `~/.claude/settings.json` |
| Claude Code | `PreToolUse` (opt-in rewrite) | `Bash\|PowerShell` | `~/.claude/settings.json` |
| Claude Code | `PostToolUse` (opt-in decision capture prompt) | `Bash\|PowerShell` | `~/.claude/settings.json` |
| Claude Code | `PostToolUse` and `PostToolUseFailure` (opt-in coverage re-ingest) | `Bash\|PowerShell` | the repo's `.claude/settings.local.json` |
| Codex | `SessionStart` | `startup\|resume\|clear` | the repo's `.codex/hooks.json` |
| Codex | `PostToolUse` | `Bash\|shell_command`, `apply_patch\|Edit\|Write` | the repo's `.codex/hooks.json` |

The coverage re-ingest entries are written by the next `repowise coverage add`,
`init` or `update` once `hooks.coverage_reingest: true` is set, and removed when
the key is false or gone. Command strings, the presence guard and the other
settings `init` writes: [hooks reference](../reference/HOOKS_REFERENCE.md).

---

## Hooks vs MCP tools

- **Hooks** are passive. They fire on every search, edit or session start,
  whether or not the agent is thinking about graph context.
- **[MCP tools](MCP_TOOLS.md)** are on-demand and return richer output: full
  documentation, risk, decision history, dependency paths.

For day-to-day coding the hooks supply most of the context; the MCP tools are
there for deeper questions.
