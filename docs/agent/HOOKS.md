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
| **Wrong-path rescue** | Claude Code | `repowise init` | a `Read` / `Edit` / `Write` / `Grep` / `Glob` / `NotebookEdit` that failed on a path this tree does not have | Names the file when exactly one indexed file carries that basename; silent otherwise |
| **Command-rewrite (distill)** | Claude Code | `repowise hook rewrite install` (opt-in) | `Bash` / `PowerShell` | Rewrites noisy commands to `repowise distill <cmd>`; auto-allowed by default, set `permission: ask` to approve each one |
| **Codex context + staleness** | Codex | `repowise init --codex` | SessionStart / UserPromptSubmit / edit / Bash | Reminds Codex to use the MCP tools and flags stale context after edits |

Every agent hook records what it said and whether the agent acted on it — see
[`repowise hook stats`](#is-any-of-this-actually-helping--repowise-hook-stats).

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

`repowise init --no-editor-setup` skips this whole group, along with the MCP
server registration that shares the same file. Reach for it when the repo is
temporary (a scratch clone, a worktree, a benchmark loop) and you do not want
that machine-wide config to move. `REPOWISE_SKIP_EDITOR_SETUP=1` is the same
switch for CI. The index itself is identical either way. To register the repo
afterwards, re-run `repowise init` in it without the flag.

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

**Search-flood digests.** A grep that returns 50+ matches also gets a compact
per-file digest: every matched file with its match count and two anchor line
numbers, ranked by graph centrality when the index can rank them, and an explicit
`(N more files, M matches)` tail for anything past the top ten.

With `hooks.search_digest: true` in `.repowise/config.yaml`, written by the same
yes/no as the rewrite hook, and toggled afterwards with `repowise hook
search-digest install | uninstall | status`, that digest *replaces* the raw
match list rather than riding alongside it. Re-run the search scoped to a file it
names, or read those lines directly, to see any match in full. Savings appear in
`repowise saved` under the `search_digest` filter, and a repo with it off still
gets the counterfactual number.

Two cases are deliberately left alone. A **single-file context grep** (`-C`,
`-A`, `-B`) is never digested: that context is exactly what the agent asked for,
and Claude Code renders those results without a path prefix, so they are not
parsed as a multi-file flood in the first place. And `files_with_matches` results
carry no match text to replace: the file list is already a digest.

**Git/edit freshness.** After a successful `git commit`, `merge`, `rebase`,
`cherry-pick`, or `pull`, repowise compares `HEAD` against the last indexed commit
in `.repowise/state.json` and, if the wiki is behind, reminds the agent to run
`repowise update` so it never silently works from outdated docs.

**Read-intelligence.** On `Read` of an indexed file, repowise emits a per-file
stale-read notice when the file changed after the session's previous read of it,
and points at the cheaper `get_context(..., include=["skeleton"])` for
structure-level questions.

With `hooks.read_skeleton: true` in `.repowise/config.yaml` — which `repowise
init` writes from the same yes/no as the rewrite hook, and which `repowise hook
read-skeleton install | uninstall | status` toggles afterwards — that pointer
becomes an action: an
unbounded `Read` of a large indexed file returns the file's *skeleton* instead of
the file, once per file per session. Signatures stay, keeping their real line
numbers; bodies collapse to `... N lines (a-b)` markers carrying 1-indexed ranges,
so the agent can range-read any elided span back — the same reversibility contract
`repowise distill` makes for shell output. Reading the file again with no range
returns it whole. Savings appear in `repowise saved` under the `read_skeleton`
filter. In a repo that has it off, the same Reads are still *measured*, and
`repowise saved` reports what they would have saved — a number about size only,
never about whether the agent could work from a skeleton.

One consequence is worth knowing: a Read the agent saw only as a skeleton still
satisfies Claude Code's read-before-edit precondition, so an `Edit` (especially
with `replace_all`) or a `Write` could touch bodies it never saw. Editing such a
file raises a one-line warning, once per file, until the file is read in full.

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

## PostToolUseFailure, the wrong-path rescue

An agent that knows a file exists but guesses the wrong directory for it gets
back "Path does not exist" and burns a turn hunting. The index already knows
where that filename lives, so the failure is answerable at the moment it
happens:

```
[repowise] core/git_indexer/fix_events.py is not in this tree.
The only indexed fix_events.py is core/ingestion/git_indexer/fix_events.py
```

It speaks only when the basename resolves to **exactly one** indexed file that
is still on disk. Everything else is silence, and each case is a distinct way
to be confidently wrong:

- **An ambiguous basename.** Naming one of a dozen `registry.py` is worse than
  saying nothing, because the agent has no cheap way to tell a rescue from a
  fact.
- **A directory target.** "Which file did you mean" is not the question a
  missing directory asks.
- **A path in another checkout.** A sibling worktree has its own index; this
  one has no standing to answer for it.
- **A failure Claude Code already answered.** It prints its own "Did you mean"
  for some of these, and repeating it is worse than silence.
- **The path that just failed.** The index can hold a row for a file that is
  not on disk right now, and pointing back at the failed path is the worst
  thing this surface could say.

Measured over 435 sessions in this repo: 86 path-not-found failures on the file
tools, of which the rescue speaks to 18. The gap is the point.

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

- Defaults to **`allow`**, so a rewrite runs without a prompt. That is not a
  permission escalation: a rewrite is always `repowise distill <one recognized
  command>` from a closed family set, never an arbitrary command smuggled
  behind the wrapper. Set `permission: ask` under `distill.commands` in
  `.repowise/config.yaml` to approve each one instead.
- Never rewrites compound commands, redirections, or watch modes. The one pipe
  shape it handles (macOS/Linux) is a single stage into `head`, `tail`, `grep`
  or `rg`, quoted whole so it runs unchanged inside distill's own shell.
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

## Hook efficacy: `repowise hook stats`

The agent hooks keep a local ledger in `.repowise/sessions/sessions.db`: what
each hook said, and whether the agent went on to do what it pointed at.

```sh
repowise hook stats                        # per-surface firing counts and action rates
repowise hook backfill --all-projects      # seed it from your existing transcripts
```

The verdict comes from your own Claude Code transcripts — a firing is paired
with the tool calls that followed it — so the numbers are yours, not a
benchmark. `repowise update` classifies recent sessions; `hook backfill` covers
history. Nothing leaves the machine.

Notices that ask for nothing (the stale-read warning, the silent
read-after-served measurement) report `n/a` rather than a rate. `hook stats`
also reports hook invocation counts and wall time, including the calls that
returned silence.

> Upgrading from a release before firings were keyed by their text: run
> `repowise hook backfill --reset` once, or older rows are counted separately
> from the replayed ones. It never touches decisions.

---

## What gets written where

`repowise init` writes these entries into `~/.claude/settings.json` (Claude Code)
and `.codex/hooks.json` (Codex when `--codex` is passed):

| Client | Hook type | Matcher | Command |
|--------|-----------|---------|---------|
| Claude Code | `SessionStart` | `startup\|resume\|clear` | `repowise-augment` [^guard] |
| Claude Code | `PostToolUse` | `Bash\|PowerShell\|Grep\|Glob\|Read\|Edit\|Write\|mcp__.*[Rr]epowise.*__.*` | `repowise-augment` [^guard] |
| Claude Code | `PreToolUse` (opt-in) | `Bash\|PowerShell` | `repowise-rewrite` |
| Codex | `SessionStart` / `UserPromptSubmit` | lifecycle | context reminder |
| Codex | `PostToolUse` | `Bash`, `apply_patch\|Edit\|Write` | staleness check |

[^guard]: The command is written wrapped in a presence check rather than as the
    bare name:

    ```sh
    if command -v repowise-augment >/dev/null 2>&1; then exec repowise-augment; fi
    ```

    The Claude Code plugin ships these hooks independently of the CLI, so
    "plugin installed, `repowise` not installed" is a supported state — and a
    partially written install reaches it too (on Windows an MCP server holds
    `repowise.exe` open, so an installer can abort after writing only some
    console scripts). Unguarded, either state prints `command not found` on
    every matched tool call: non-blocking, unactionable, and endless. The guard
    is POSIX (`command -v` + `exec`, verified under `sh`, `bash` and `dash`) and
    forwards stdin unchanged, so the hook behaves identically when the script is
    present. An older install carrying the bare name is rewritten on the next
    `repowise init`. Codex hooks keep the bare name: their execution model is
    not documented as shell-based, and a directly `exec`'d guard would try to
    run a binary named `if`.

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
