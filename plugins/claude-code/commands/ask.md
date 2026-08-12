---
description: Ask a codebase question and get a cited, synthesised answer with a confidence rating (costs an LLM call).
allowed-tools: Bash, Read
---

# Repowise Ask

Answer a question about this codebase with citations. This is the same
synthesis the `get_answer` MCP tool performs: hybrid retrieval followed by an
LLM answer over what it found. Unlike `/repowise:search`, this **costs an LLM
call** — use it when the user wants an answer, not a hit list.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `/repowise:init` first." Stop.
2. Resolve the question from `$ARGUMENTS`. If empty, ask: "What do you want to know about this codebase?"
3. Run the command and present the answer, confidence, and retrieval quality.
   Prefer citing the paths the answer names. Do not invent citations.

## Choosing the invocation

- Question only → `repowise ask "<question>"`
- Restrict retrieval to a subtree → `repowise ask "<question>" --scope packages/cli/`
- Machine-readable → `repowise ask "<question>" --format json`
- Raw MCP payload (including dropped blocks) → `repowise ask "<question>" --full`

```
repowise ask "how does the retry backoff work?"
repowise ask "where is the session cookie set?" --format json
repowise ask "how is width resolved?" --scope packages/cli/
repowise ask "why is auth split across two modules?" --full
```

Shared targeting flags (`--path`, `--repo`, `--no-workspace`) work the same as
the other tool-adapter commands.

## How to present the result

- `confidence: high` is content-grounded — cite it directly.
- `medium` / `low` — say so, and follow any `best_guesses` / `fallback_targets`
  into `/repowise:context` or `get_context` rather than inventing detail.
- If a `note` warns that numbers may be synthesised, surface that caveat.
- Prefer this over `/repowise:search` when the user asked a *question*. Prefer
  search when they want a list of matching pages / symbols.
