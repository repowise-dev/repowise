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
- `pip install repowise` (recommended)
- `pip install "repowise[all]"` (includes all LLM provider dependencies)
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

## Step 3: Ask about mode

Ask the user ONE question:

"How would you like to set up Repowise?

1. **Index-only mode** — no LLM needed. Builds the dependency graph, mines git history, detects dead code. Runs in under 60 seconds. You get graph intelligence, git ownership, hotspots, and dead code detection — but no generated documentation or semantic search.
2. **Full mode** — generates rich documentation using an LLM. Requires an API key (Anthropic, OpenAI, Google Gemini, or Ollama for local). Takes longer but gives you the best results — full wiki, semantic search, and RAG context.
3. **I'll configure it myself** — just show me the available flags."

### If Index-only mode:

Skip provider selection. Construct:
```
repowise init --index-only
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
  --index-only           Analysis-only mode. No docs generation, no API key needed.

Embeddings:
  --embedder NAME        Embedding provider: gemini, openai, mock (default: auto-detect)

Cost control:
  --concurrency N        Max parallel LLM calls (default: 5)
  --test-run             Limit to top 10 files by PageRank for quick validation

Exclusions:
  -x, --exclude PATTERN  Gitignore-style exclude patterns. Repeatable.
                         Example: -x 'vendor/**' -x 'generated/'
  --skip-tests           Skip test files
  --skip-infra           Skip infrastructure files (Dockerfile, Makefile, Terraform, shell)

Git:
  --commit-limit N       Max commits to analyze per file (default: 500, max: 5000)
  --follow-renames       Track files across renames (slower but more accurate history)

Output:
  --no-claude-md         Don't generate/update CLAUDE.md
  -y, --yes              Skip cost confirmation prompt

Recovery:
  --resume               Resume a previously interrupted init
  --force                Re-index from scratch, overwriting existing .repowise/

Dry run:
  --dry-run              Show generation plan and cost estimate without running
```

Then ask if they want you to construct a command or if they'll handle it.

## Step 4: Provider selection (full mode only)

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

Also check if the provider's Python package is installed. If using anthropic and it's not installed:
```
pip install "repowise[anthropic]"
```
Similarly for openai (`repowise[openai]`) and gemini (`repowise[gemini]`).

## Step 5: Exclusions

Before running the command, ask:

"Any directories or patterns you want to exclude from indexing? Common ones to skip: `vendor/`, `generated/`, large data directories, build artifacts. Your `.gitignore` is already respected automatically. Press Enter to skip."

Add any exclusions as `-x` flags.

## Step 6: Run it

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
