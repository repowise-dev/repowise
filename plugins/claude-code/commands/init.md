---
description: Set up Repowise for this codebase. Installs if needed, asks about your preferences, and runs the indexing.
allowed-tools: Bash(repowise --version), Bash(repowise init:*), Bash(repowise status:*), Bash(repowise update:*), Bash(git rev-parse --git-common-dir), Bash(uv tool install repowise), Bash(pipx install repowise), Bash(pip install repowise), Bash(python -m pip install repowise), Read, AskUserQuestion
---

# Repowise Init

You are helping the user set up Repowise for their codebase. Follow this sequence precisely.

## Step 1: Check if repowise is installed

Run: `repowise --version`

If the command fails or is not found, ask the user:

"Repowise isn't installed yet. I can install it for you, or you can install it yourself."

If they want you to install it, use the first installer that works:
`uv tool install repowise`, then `pipx install repowise`, then
`python -m pip install repowise`. Each one includes every LLM provider SDK.

After install, verify with `repowise --version`. If the shell still says
`command not found`, the user runs `uv tool update-shell` or `pipx ensurepath`
and opens a new shell.

If repowise IS found, print the version and move to Step 2.

## Step 2: Check if this repo is already indexed

If the project root has no `.repowise/` directory, move to Step 3.

If it has one, run `repowise status --format json` and act on what it says:

- **Incomplete** (`incomplete` is set): a previous run stopped part way. Offer
  `repowise init --resume` with the flags of the original run (`--no-prose`
  for a run built from code).
- **Index only** (`upgrade_command` is set): the repo was indexed with
  `--mode fast` and has no documentation yet. Offer `repowise update --full`.
  It needs no API key: without a model it builds the documentation from code
  and backfills the full git history, reusing the index.
- **Otherwise** the index is ready: "This repo is already indexed by Repowise.
  Run `/repowise:status` to check health, or `/repowise:update` to refresh."
  Re-index from scratch with `repowise init --force` only if the user asks.

Do not continue to Step 3 unless the user asks to re-index.

## Step 3: Offer the mode, but do not block on it

Repowise needs **no API key at all**. Never make a key a precondition for
setting it up, and never stop and wait if you cannot get an answer.

Tell the user:

"Repowise indexes your repo and builds its documentation from code with no API
key. I'll run `repowise init --yes --no-prose` unless you want to pick:

1. **Built from code** (default, no key): the full index, health, dead code,
   and documentation built from code. No model calls, no cost.
2. **Written by a model**: a model writes the documentation and reads
   decisions from PRs, git history and code comments. Needs a provider, and
   shows the estimated cost before any model call. You can start with option 1
   and add this later with `repowise generate`.
3. **Index only**: graph and essential git history, no documentation. For very
   large repos; `repowise update --full` adds the rest later.
4. **Show me the flags.**"

If the user does not answer, or you are running unattended, take option 1.

- **Built from code:** skip provider selection. The command is
  `repowise init --yes --no-prose`. Go to Step 3b.
- **Written by a model:** go to Step 3b, then Step 4.
- **Index only:** the command is `repowise init --yes --no-prose --mode fast`.
  Go to Step 3b.
- **Show me the flags:** print the reference below, then ask whether you
  should build the command or they will.

```
repowise init [PATH]

Core flags:
  --provider NAME        LLM provider: anthropic, openai, gemini, openrouter,
                         deepseek, kimi, ollama, litellm, edenai, claude_cli,
                         codex_cli, opencode
  --model NAME           Model identifier override
  --prose / --no-prose   Documentation written by a model (--prose, needs a
                         provider) or built from code (--no-prose: no model
                         calls, whatever keys are set). Default: written by a
                         model when a key is available.
  --mode fast            Index only: graph and essential git, no documentation.
                         'repowise update --full' adds the rest later.

Embeddings:
  --embedder NAME        Embedder for semantic search: gemini, openai,
                         openrouter, ollama, edenai, mock (default: detected
                         from keys)

Cost control:
  --concurrency N        Max parallel LLM calls (default: 10)
  --test-run             Write documentation with the model for the top 10
                         files only, for a quick check
  --dry-run              Show the plan and cost estimate without running

Exclusions:
  -x, --exclude PATTERN  Gitignore-style exclude patterns. Repeatable.
                         Example: -x 'vendor/**' -x 'generated/'
  --skip-tests           Skip test files
  --skip-infra           Skip infrastructure files (Dockerfile, Makefile, Terraform, shell)

Git:
  --commit-limit N       Max commits to analyze per file (default: 5000, max: 10000)
  --follow-renames       Track files across renames (slower but more accurate history)

Editor integration:
  --no-editor-setup      Write only .repowise/: no global MCP entry, no
                         Claude Code hooks, no project files (.mcp.json,
                         .claude/CLAUDE.md, .vscode/). See Step 3b.
  --hook / --no-hook     Install the post-commit hook that runs 'repowise
                         update' after each commit. Default: on (off with
                         --no-editor-setup). Undo with 'repowise hook uninstall'.

Keys:
  --no-save-key          Do not save the provider key to .repowise/.env
                         (for CI or a shared machine)

Output:
  --no-claude-md         Don't generate or update CLAUDE.md
  --progress json        One JSON event per line on stdout, ending with a
                         summary (status, mode, spend, next commands)
  -y, --yes              No questions: every answer at its default, cost
                         pre-approved. Add --no-prose to guarantee no spend.
  -v, --verbose          Show per-phase internals plus debug logs

Recovery:
  --resume               Continue a run that stopped part way
  --force                Re-index from scratch
```

## Step 3b: Is this repo a keeper?

Every path reaches this step. It is about the repo, not the provider.

By default `init` writes project files into this repo (`.mcp.json`,
`.claude/CLAUDE.md`, `AGENTS.md`, `.vscode/mcp.json`) and adds the global
repowise MCP entry (in `~/.claude.json` and Claude Desktop's config)
when there is none. The Claude Code entry serves whichever repo Claude Code is
started in. Claude Desktop has no project scope, so its one entry names the
repo it serves. init never moves an entry that serves another repo; it only
re-points one whose repo no longer exists. That is the right default for the
repo someone actually works in, and the wrong one for a checkout that is about
to disappear.

Add `--no-editor-setup` when either of these holds:

- the path is a scratch clone, a fixture, a sample, a vendored copy, or sits
  under a temp directory
- it is a linked git worktree, not the main checkout. Check with
  `git rev-parse --git-common-dir`: a value other than `.git` means a worktree

Also add it if the user says they don't want their editor or MCP setup touched.
Do **not** infer that from "just index it" or "don't ask me questions": those
are about prompts, not config.

The index and the documentation are identical either way. With the flag only
`.repowise/` is written. Re-running `repowise init` in that repo without the
flag wires it up later.

Whichever you pick, say which in one line so the user can correct you. If
`init` prints the old and new target of the global entry, relay it: the user
may want it pointed back.

Then continue to Step 4 for documentation written by a model, or Step 5
otherwise.

## Step 4: Provider selection (written by a model only)

**Skip this entire step unless the user chose documentation written by a
model.** A missing key is not a blocker. If the user has no key, do not stop and
do not ask again: run `repowise init --yes --no-prose` and tell the user they can
add a model later with `repowise generate`.

Do not read, test or print the user's environment variables or key values
yourself. `repowise init` picks up a provider key from the environment and
`.repowise/.env` on its own, so a user who already has one set needs no
`--provider`. Ask:

"Which LLM provider do you want to use?"
- **Google Gemini** (good cost efficiency): needs `GEMINI_API_KEY` or `GOOGLE_API_KEY`
- **Anthropic** (Claude): needs `ANTHROPIC_API_KEY`
- **OpenAI**: needs `OPENAI_API_KEY`
- **OpenRouter, DeepSeek, Kimi, Eden AI**: need their own key
- **Ollama** (fully local, no API key, slower): needs Ollama running
- **LiteLLM** (100+ providers): needs LiteLLM config
- **Claude Code, Codex or OpenCode login** (`claude_cli`, `codex_cli`,
  `opencode`): uses the user's plan quota, or is billed per token when the
  CLI is signed in with an API key, and takes longer. `init` reads each CLI's
  auth status and uses one only with `--provider`. A Codex signed in through
  ChatGPT is listed in the picker but not offered.

The user sets a key in their own shell or in `.repowise/.env` (gitignored).
Never ask for a key in the chat. Show the export line for their provider:
```bash
export GEMINI_API_KEY="..."
```

Every provider SDK ships with `repowise` itself, so there is no per-provider
package to install. If a provider import fails, the install is broken:
reinstall repowise with the same installer and run `repowise doctor`.

## Step 5: Exclusions

Before running the command, ask:

"Any directories or patterns you want to exclude from indexing? Common ones to skip: `vendor/`, `generated/`, large data directories, build artifacts. Your `.gitignore` is already respected automatically. Press Enter to skip."

Add any exclusions as `-x` flags.

## Step 6: Run it

The unattended command is `repowise init --yes --no-prose`. `--yes` answers
every prompt with its default and pre-approves the cost; `--no-prose` makes sure
nothing is spent. Without `--no-prose`, `--yes` uses a model whenever a key is
set; a logged-in agent CLI only with `--provider`. In a folder that holds
several repos, `--yes` indexes all of them; `--repos a,b` picks some. A model run passes `--yes` only after the user approved the estimate
(`repowise init --dry-run` prints it).

Show the user the exact command you're about to run. Ask for confirmation.

Run it in the background. A small repo takes under a minute; a few thousand
files can take several minutes, longer than many tool timeouts. Wait for the
process to exit instead of relying on a fixed timeout, then read its exit code:

- `0`: ready.
- `2`: finished with gaps. Everything is saved; the last line names what is
  missing and the command that adds it.
- `130`: interrupted. `repowise init --resume` (same flags) continues it.
- anything else: failed. Run `repowise doctor`.

With `--progress json`, init writes one JSON event per line to stdout and ends
on a summary line with the status, mode, spend and next commands.

`repowise status` reports a first run as incomplete until it exits, so read it
after the process ends. Ctrl+C is safe at any point: running `/repowise:init` again
resumes with `--resume`.

## Step 7: Post-setup

After init exits with `0` or `2`:

1. Run `repowise status` to show the summary.
2. Tell the user:
   - "Run `repowise serve` to open the dashboard. It needs Node.js 20+, and the
     first run downloads the UI. It starts on port 3000, or the next free port."
   - "The MCP tools appear after you restart your agent, or run `/mcp` in
     Claude Code. They are not active in this session yet."
     **Only if you did not pass `--no-editor-setup`.** With that flag nothing was
     registered and no editor files were written, so say instead: "The index is
     built. I did not register it as your global MCP server and wrote no editor
     files into the repo (this looked like a temporary checkout). To wire it up,
     re-run `repowise init` without `--no-editor-setup`."
   - "After the restart, try asking me 'give me a tour of this codebase' or
     'what depends on utils.py?'"
   - "Run `/repowise:status` anytime to check the index, and `/repowise:update`
     after code changes to keep it in sync."
3. If the documentation was built from code, add one line: "`repowise generate`
   has a model write the documentation and read decisions from PRs and
   history, with a cost estimate first." For an Index only run, name
   `repowise update --full` instead.
4. If CLAUDE.md was generated (default): "I've also generated a CLAUDE.md with codebase context that I'll read on every session."
