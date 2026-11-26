# Modes, costs and providers

An index starts in one of three modes and moves up in place, reusing what it
already has. This page covers what each mode gives you, what a model adds and
costs, and which providers and embedders repowise can use. Getting to a first
index: [Quickstart](QUICKSTART.md).

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

## Cost and what your agent gets

| Setup | Cost | Command | `get_answer` gives your agent |
|---|---|---|---|
| Keyless | free; nothing leaves the machine | `repowise init --yes --no-prose` | a ranked, cited shortlist of evidence from the index (`degraded: no-llm-provider`) |
| Keyless plus semantic search | free with Ollama or the `local-embed` extra; a few cents of embedding calls with a key | `ollama pull embeddinggemma`, then `repowise reindex --embedder ollama`; or `pip install "repowise[local-embed]"`, then `repowise reindex --embedder fastembed` | the same shortlist, matched by meaning as well as words |
| Full docs | `repowise generate --dry-run` prices it first; a signed-in Claude Code, Codex or Copilot uses plan quota | `repowise generate --provider <id>` | one written, cited answer |

Every other tool returns the same result in all three. Running keyless in CI,
offline or in a benchmark: [Offline, CI and benchmarks](OFFLINE.md).

## What a model adds

- Written documentation: subsystem and architecture pages that explain how the
  code fits together, in place of pages built from code.
- Decisions read from pull requests, git history and code comments.
  `repowise generate` reads the ones a keyless run skipped.
- Synthesized answers from `get_answer` and the dashboard chat.

Semantic search needs an embedder, which is separate from the model. Without
one, search is full-text, and every run names the command that turns it on.
For local semantic search with no service to run and no key, install the
`local-embed` extra and pick `fastembed`:

```bash
pip install "repowise[local-embed]"
repowise reindex --embedder fastembed
```

The extra covers Python 3.11 and 3.12; on 3.13 and newer, run
`pip install fastembed` instead. Details: [Offline](OFFLINE.md#local-semantic-search-without-a-service).

## Providers and embedders

| Provider | Credential | Embedder of its own |
|---|---|---|
| `gemini` | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | yes |
| `openai` | `OPENAI_API_KEY` | yes |
| `openrouter` | `OPENROUTER_API_KEY` | yes |
| `edenai` | `EDENAI_API_KEY` | yes |
| `ollama` | none (local) | yes: `ollama pull embeddinggemma`, then `repowise reindex --embedder ollama` |
| `anthropic`, `deepseek`, `kimi`, `litellm` | `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `KIMI_API_KEY`, `LITELLM_API_KEY` | no |
| `claude_cli`, `codex_cli`, `copilot_cli`, `opencode` | your logged-in subscription | no |

When nothing names an embedder, repowise takes the first one with a key set,
in the order gemini, openai, openrouter, ollama, edenai, and every model run
prints which embedder it uses and why. init reads each logged-in Claude Code,
Codex or OpenCode's auth status and offers it with its call count, time and
billing (plan quota, or per token for an API key), Claude Code first. A Codex
signed in through ChatGPT stays in the picker but is not offered. An agent
CLI is used only when you pick it or pass `--provider`. Keys, models and base URLs:
[Config](../reference/CONFIG.md).
