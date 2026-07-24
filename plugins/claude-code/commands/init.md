---
description: Set up Repowise for this codebase. Installs if needed, asks about your preferences, and runs the indexing.
allowed-tools: Bash, Read, Write, AskFollowupQuestion
---

# Repowise Init

You are helping the user set up Repowise for their codebase. Follow this sequence precisely.

## Step 1: Check if repowise is installed

Run: `repowise --version`

If the command fails or is not found, ask the user:

"Repowise isn't installed yet. I can install it for you. Which do you prefer?"
- `pip install repowise` (recommended, includes every LLM provider SDK)
- "I'll install it myself"

If they want you to install it, run `pip install repowise`. If that fails, try `python -m pip install repowise`.

After install, verify with `repowise --version`.

If repowise IS found, print the version and move to Step 2.

## Step 2: Check if this repo is already indexed

Check if `.repowise/` directory exists in the project root.

If it exists, tell the user:
"This repo is already indexed by Repowise. Run `/repowise:status` to check health, or `/repowise:update` to refresh. If you want to re-index from scratch, I can run `repowise init --force`."

Then stop — do not continue to Step 3 unless the user asks to re-index.

If `.repowise/` doesn't exist, move to Step 3.

## Step 3: Offer the mode, but do not block on it

Repowise needs **no API key at all**. Never make a key a precondition for
setting it up, and never stop and wait if you cannot get an answer.

Tell the user:

"Repowise indexes your repo and writes a complete wiki with no API key. I'll run
`repowise init --yes` unless you'd rather pick:

1. **Default (no key)** — full index plus a wiki rendered from your code's structure. Free, fast, nothing to configure.
2. **Model-written wiki** — everything above, but an LLM writes the wiki prose instead of rendering it from structure, which also enables decision mining. Needs a provider key (Anthropic, OpenAI, Gemini, or Ollama locally). You can start keyless and upgrade any page later with `repowise generate`.
3. **Show me the flags.**"

If the user does not answer, or you are running unattended, take option 1.

### If Default (no key):

Skip provider selection entirely. Construct:
```
repowise init --no-prose --yes
```

Jump to Step 5.

### If Full mode:

Move to Step 4.

### If "show me flags":

Print the full flag reference:
```
repowise init [PATH]

Core flags:
  --provider NAME        LLM provider: anthropic, openai, gemini, ollama, litellm
  --model NAME           Model identifier override
  --prose / --no-prose   Write the subsystem (concept) pages as model prose
                         (--prose, needs a key), or render the whole wiki from
                         structure with no model and no spend (--no-prose).
                         Every other page is structural either way.
                         Default: prose when a key is available.
  --mode fast            Quick first pass on very large repos: no wiki at all.

Embeddings:
  --embedder NAME        Embedding provider: gemini, openai, mock (default: auto-detect)

Cost control:
  --concurrency N        Max parallel LLM calls (default: 10)
  --test-run             Limit to top 10 files by PageRank for quick validation

Exclusions:
  -x, --exclude PATTERN  Gitignore-style exclude patterns. Repeatable.
                         Example: -x 'vendor/**' -x 'generated/'
  --skip-tests           Skip test files
  --skip-infra           Skip infrastructure files (Dockerfile, Makefile, Terraform, shell)

Git:
  --commit-limit N       Max commits to analyze per file (default: 500, max: 10000)
  --follow-renames       Track files across renames (slower but more accurate history)

Output:
  --no-claude-md         Don't generate/update CLAUDE.md
  -y, --yes              Skip cost confirmation prompt
  -v, --verbose          Show per-phase internals plus debug logs (quiet by default)

Recovery:
  --resume               Resume a previously interrupted init
  --force                Re-index from scratch, overwriting existing .repowise/

Dry run:
  --dry-run              Show generation plan and cost estimate without running
```

Then ask if they want you to construct a command or if they'll handle it.

## Step 4: Provider selection (model-written wiki only)

**Skip this entire step unless the user chose the model-written wiki.** A
missing key is not a blocker: `init` renders the template wiki and exits 0
without one. If you cannot find a key, do not stop and do not ask again. Run
`repowise init --yes` and tell the user their wiki is rendered from structure
and can be upgraded per-page later with `repowise generate`.

Check which API keys are already set by running:
```bash
echo "ANTHROPIC=${ANTHROPIC_API_KEY:+set}" "OPENAI=${OPENAI_API_KEY:+set}" "GEMINI=${GEMINI_API_KEY:+set}${GOOGLE_API_KEY:+set}" "OLLAMA=${OLLAMA_BASE_URL:+set}"
```

If one or more keys are detected, suggest the detected provider:
- `ANTHROPIC_API_KEY` set → suggest `--provider anthropic`
- `OPENAI_API_KEY` set → suggest `--provider openai`
- `GEMINI_API_KEY` or `GOOGLE_API_KEY` set → suggest `--provider gemini`
- `OLLAMA_BASE_URL` set → suggest `--provider ollama`

If no key is detected, ask:

"Which LLM provider do you want to use?"
- **Anthropic** (Claude) — needs `ANTHROPIC_API_KEY`
- **OpenAI** (GPT-4o or compatible) — needs `OPENAI_API_KEY`
- **Google Gemini** (recommended for cost efficiency) — needs `GEMINI_API_KEY` or `GOOGLE_API_KEY`
- **Ollama** (fully local, no API key, slower) — needs Ollama running locally
- **LiteLLM** (100+ providers) — needs LiteLLM config

Then ask them to set the required environment variable. Show the exact export command:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Every provider SDK (anthropic, openai, google-genai, litellm) ships with `repowise`
itself, so there is no per-provider package to install. If a provider import fails,
the install is broken rather than incomplete: reinstall with
`pip install --force-reinstall repowise` and run `repowise doctor`.

## Step 5: Exclusions

Before running the command, ask:

"Any directories or patterns you want to exclude from indexing? Common ones to skip: `vendor/`, `generated/`, large data directories, build artifacts. Your `.gitignore` is already respected automatically. Press Enter to skip."

Add any exclusions as `-x` flags.

## Step 6: Run it

For an unattended or non-interactive run, the canonical command is
`repowise init --yes`, plus `--no-prose` when you want to guarantee no spend.
`--yes` suppresses every prompt including the cost gate. You do not need to guard
against prompts beyond that: init treats an unanswerable question as a signal to
continue with defaults rather than a reason to fail, and the cost gate declines
by itself when stdin is not a terminal, keeping the finished index and landing on
the structural wiki.

Show the user the exact command you're about to run. Ask for confirmation.

Run it. For full mode, note: "This will take a while for the initial indexing. You can Ctrl+C safely — run `/repowise:init` again and I'll use `--resume` to continue where you left off."

## Step 7: Post-setup

After init completes successfully:

1. Confirm the `.repowise/` directory was created
2. Run `repowise status` to show the summary
3. Tell the user:
   - "Repowise has indexed your codebase. The MCP tools are now active — I can answer questions about your architecture, ownership, dependencies, and more."
   - "Try asking me something like 'how does the auth module work?' or 'what depends on utils.py?'"
   - "Run `/repowise:status` anytime to check the health of your index."
   - "Run `/repowise:update` after making code changes to keep the wiki in sync."
4. If CLAUDE.md was generated (default): "I've also generated a CLAUDE.md with codebase context that I'll read on every session."
