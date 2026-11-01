# Quickstart

Index your repository, open the dashboard, and have your coding agent answer
from that index. The default run needs no API key, makes no model calls and
sends nothing off your machine.

> Looking for one command's flags? See the
> [CLI Reference](../reference/CLI_REFERENCE.md). For everyday workflows, see
> the [User Guide](USER_GUIDE.md).

## Choose your path

| You are | Go to |
|---|---|
| A person at a terminal | [Human path](#human-path) |
| A coding agent setting repowise up, or a person who wants one to | [Agent path](#agent-path) |

Both paths end on the same index. [Modes and upgrades](#modes-and-upgrades)
covers what to add later.

## Human path

### 1. Install

Python 3.11+ and Git are required. Use the first one you have:

```bash
uv tool install repowise          # uv brings its own Python
pipx install repowise
python3 -m pip install repowise   # Windows: python -m pip install repowise
```

Check it with `repowise --version`. If the shell says `command not found`, run
`uv tool update-shell` or `pipx ensurepath` and open a new shell.

### 2. Index

From the repository root:

```bash
repowise init
```

`init` opens on one plan screen: what it found, what it will build, an ETA,
and an opt-in model line with what a model adds and costs. Enter starts the
free run, which builds the documentation from code: no key, no model calls.
`m` picks a model, `e` edits the plan (advanced options, agents, the
post-commit hook), `s` connects only this repo, and `q` quits. A detected API
key or logged-in agent CLI is named on the model line and used only after `m`.

`repowise init --yes --no-prose` does the free run with no questions. Without
`--no-prose`, `--yes` uses a model when an API key is configured, with the
cost pre-approved. A logged-in Claude Code, Codex or OpenCode is used only
with `--provider`.

### 3. Open the dashboard

```bash
repowise serve
```

This is the first thing to run after `init`. The API starts on port 7337 and
the dashboard on port 3000, or the next free port. The dashboard needs
Node.js 20+, and its first run downloads the UI (about 50 MB, cached in
`~/.repowise/web/`). The dashboard opens in your browser once it is up;
`--no-open` skips that. `--no-ui` starts the API alone, and the
[Docker image](../../docker/README.md) avoids installing Node. View by view:
[Dashboard](DASHBOARD.md).

After a first index, the dashboard opens on a three-step welcome: what stood
out in your repo, connecting your agent, and what to add next. Setup
(Settings > Setup) keeps the same list for later: agents, sync, coverage and a
model, each with a prompt you can copy to your agent. The free index stays the
default; add a model whenever you like from the dashboard or with
`repowise generate`.

### 4. Connect your agent

`init` wires your editors unless you pass `--no-editor-setup` (or set
`REPOWISE_SKIP_EDITOR_SETUP=1`). What it writes:

| What | Where | When |
|---|---|---|
| MCP server entry | `.mcp.json`, `.vscode/mcp.json` (plus `.vscode/extensions.json`) | by default |
| Agent context files | `.claude/CLAUDE.md`, `AGENTS.md`, Copilot instructions under `.github/` | by default |
| Global repowise MCP entry for Claude Code and Claude Desktop | `~/.claude.json` and the Claude Desktop config | by default; when it points at another repo the plan screen shows the move and `s` keeps it; `--yes` points it here and prints the old and new repo |
| Claude Code hooks (session briefing, post-edit context) | `~/.claude/settings.json` | with the global entry |
| Post-commit hook that runs `repowise update` | the repo's git `post-commit` hook | by default; `e` turns it off, `--no-hook` skips it |

The global entry points at one repo at a time. Pressing `s` keeps it where it
is, and agents opened in this repo still connect through `.mcp.json`. Point it
here later with `repowise agents add --target=claude-code --scope=user`.
When machine-specific files such as `.claude/CLAUDE.md` would show up in
`git status`, `e` offers to add them to `.git/info/exclude` (off by default).

Other hosts need one more command, run after `init`:

| Host | Command |
|---|---|
| Claude Code, VS Code (Copilot) | nothing more; the [VS Code extension](../agent/VSCODE.md) adds editor features |
| Codex | `repowise agents add --target=codex --yes`, or `--codex` on `init` |
| Cursor | `repowise agents add --target=cursor --yes` |
| OpenCode | `repowise agents add --target=opencode --yes` |
| Hermes | `repowise agents add --target=hermes --yes` |
| GitHub Copilot CLI | `repowise agents add --target=copilot --yes` |
| Kiro | `repowise agents add --target=kiro --yes` |
| Any other MCP client | `repowise agents print-config claude-code`, then paste the entry into its MCP config |

`repowise agents` lists every supported host and whether it is wired.
Per-host details: [Agent integrations](../agent/INTEGRATIONS.md). On Claude
Code, the plugin adds slash commands and skills:

```text
/plugin marketplace add repowise-dev/repowise
/plugin install repowise@repowise
```

Then restart the host, or run `/mcp` in Claude Code. Most agents read MCP
config only at startup.

### 5. First questions

Ask in plain language. The agent picks the tool.

| Ask | Tool it reaches for |
|---|---|
| "Give me a tour of this codebase." | `get_overview` |
| "What breaks if I change `src/auth.py`?" | `get_context` with callers, `get_risk` |
| "What code is dead here?" | `get_dead_code` |
| "What should we fix first?" | `get_health` |
| "Which tests should I run for my branch against `main`?" | `get_change_risk`, `get_risk` |

Every one of these works without an API key. What each tool returns:
[MCP Tools](../agent/MCP_TOOLS.md). The same answers on the command line:

```bash
repowise health            # lowest-scoring files, and why
repowise dead-code         # what nothing references
repowise risk main..HEAD   # size and bug-fix record for your branch
```

## Agent path

A person can hand setup to their agent by pasting this line into Claude Code,
Codex, Cursor, VS Code Copilot, OpenCode, Hermes or any MCP client:

```text
Read https://docs.repowise.dev/setup.md and set up Repowise in this repository.
```

That runbook follows the steps below. An agent can also follow them directly.

1. **Install** with the first installer that exists: `uv tool install
   repowise`, then `pipx install repowise`, then `python -m pip install
   repowise`.
2. **Index without spending.** This command never prompts and never calls a
   model, whatever keys are set:

   ```bash
   repowise init --yes --no-prose
   ```

   It adds the global repowise MCP entry when there is none and never moves
   one that serves another repo. Add `--no-editor-setup` in CI, in a sandbox, or on a repo the
   person only wants to try: then only `.repowise/` is written.
3. **Run it in the background.** A small repo takes under a minute; a few
   thousand files can take several minutes, longer than many tool timeouts.
   Wait for the process to exit instead of relying on a fixed timeout. While
   a first run is still going, `repowise status` reports the index as
   incomplete, so read it after the process exits.
4. **Read the exit code.**

   | Code | Meaning | Next |
   |---|---|---|
   | `0` | Ready | continue |
   | `2` | Finished with gaps; everything is saved | the last line names what is missing and the command that adds it |
   | `130` | Interrupted | `repowise init --resume` |
   | other | Failed | `repowise doctor` |

   `repowise status --format json` then gives the mode, the counts and the
   next command. With `--progress json`, init writes one JSON event per line
   to stdout and ends on a summary line with the status, mode, spend and next
   commands.
5. **Reconnect.** MCP tools appear only after the host restarts, or after
   `/mcp` in Claude Code. Do not assume they are active in the current session.
6. **First tools.** Call `get_overview`. An architecture summary back means
   the setup works. Then `get_context`, `search_codebase` and `get_answer`.
7. **Offer a model, never spend unasked.** If the person wants written
   documentation, show the estimate with `repowise generate --dry-run`, and run
   `repowise generate` only after they approve it. The person puts the key in
   `.repowise/.env` (gitignored). Never ask for a key in the chat.

## Modes and upgrades

`repowise status` shows which mode an index is in.

| Mode | Reached by | You get | Not yet | Add it with |
|---|---|---|---|---|
| Index only | `init --mode fast` | graph, essential git history, health, dead code | documentation, full git history (blame, co-change) | `repowise update --full` (no key needed) |
| Built from code | `init --no-prose`, or no key | graph, full git history, health, dead code, documentation built from code, decisions from markers and ADRs, full-text search | written documentation, decisions from PRs, history and comments | `repowise generate` (needs a model) |
| Written by \<model\> | a model run, or an upgrade above | all of the above, written documentation, decisions from PRs, history and comments | nothing | |
| Semantic search, on top of either documentation mode | an embedder (see [the matrix](#providers-and-embedders)) | search by meaning as well as by text | | `repowise reindex --embedder <name>` |

`repowise update --full` with a model configured goes straight from Index only
to Written by \<model\>. Every upgrade reuses the index you already have and
shows a cost estimate before any model call.

### What a model adds

- Written documentation: subsystem and architecture pages that explain how the
  code fits together, in place of pages built from code.
- Decisions read from pull requests, git history and code comments.
  `repowise generate` reads the ones a keyless run skipped.
- Synthesized answers from `get_answer` and the dashboard chat.

Semantic search needs an embedder, which is separate from the model. Without
one, search is full-text, and every run names the command that turns it on.

### Providers and embedders

| Provider | Credential | Embedder of its own |
|---|---|---|
| `gemini` | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | yes |
| `openai` | `OPENAI_API_KEY` | yes |
| `openrouter` | `OPENROUTER_API_KEY` | yes |
| `edenai` | `EDENAI_API_KEY` | yes |
| `ollama` | none (local) | yes, with `OLLAMA_EMBEDDING_MODEL` |
| `anthropic`, `deepseek`, `kimi`, `litellm` | `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `KIMI_API_KEY`, `LITELLM_API_KEY` | no |
| `claude_cli`, `codex_cli`, `opencode` | your logged-in subscription | no |

When nothing names an embedder, repowise takes the first one with a key set,
in the order gemini, openai, openrouter, ollama, edenai, and every model run
prints which embedder it uses and why. init reads each logged-in Claude Code,
Codex or OpenCode's auth status and offers it with its call count, time and
billing (plan quota, or per token for an API key), Claude Code first. A Codex
signed in through ChatGPT stays in the picker but is not offered. An agent
CLI is used only when you pick it or pass `--provider`. Keys, models and base URLs:
[Config](../reference/CONFIG.md).

## Keeping it fresh

The post-commit hook, when installed, runs `repowise update` after every commit.
Other options:

```bash
repowise update     # incremental catch-up, by hand
repowise watch      # update continuously while you edit
repowise status     # mode, counts, and how far behind HEAD
```

`repowise hook uninstall` removes the post-commit hook. Webhooks and polling:
[Auto-Sync](../scale/AUTO_SYNC.md). Several repositories as one:
[Workspaces](../scale/WORKSPACES.md); `init` in a folder of repos opens the
same plan screen, where `r` chooses the repos, and `--repos a,b` picks them in
a script. A new `git worktree` seeds its index from
the main checkout: [Worktrees](../scale/WORKTREES.md).

## Where to go next

| Goal | Read |
|---|---|
| Everyday workflows: before a PR, in CI, changing provider | [User Guide](USER_GUIDE.md) |
| What each MCP tool answers | [MCP Tools](../agent/MCP_TOOLS.md) |
| Context that arrives without the agent asking | [Hooks](../agent/HOOKS.md) |
| Fewer tokens on test and build output | [Distill](../agent/DISTILL.md) |
| What the health score measures | [Code Health](../layers/CODE_HEALTH.md) |
| Gates and annotations on pull requests | [CI](CI.md) |
| Every setting and environment variable | [Config](../reference/CONFIG.md) |

## Troubleshooting

Start with `repowise doctor`. It checks the install, API keys, index drift,
store health and each wired agent's MCP entry.

- **`repowise: command not found`.** Run `uv tool update-shell` or `pipx
  ensurepath`, open a new shell, and restart the agent host.
- **The agent does not see the tools.** Restart the host or run `/mcp`. Then
  `repowise doctor --repair`, or `repowise agents add --target=<id> --yes`.
- **The dashboard does not open.** Check that `node --version` is 20 or newer,
  then run `repowise serve --refresh-ui`.
- **The agent answers from old code.** `repowise status` shows drift and
  `repowise update` catches up.
- **A run stopped part way.** `repowise init --resume` continues it.

More: [Troubleshooting](TROUBLESHOOTING.md).
