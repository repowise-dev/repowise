---
layout: default
title: Oh My Pi Provider
nav_order: 5.8
---

# Oh My Pi Provider
{: .no_toc }

Use Repowise with your local Oh My Pi install via the `omp` LLM provider. No API key — Oh My Pi's own login holds the auth.
{: .fs-6 .fw-300 }

---

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Quick setup

```bash
# 1. Install Oh My Pi, then sign in once
# https://github.com/can1357/oh-my-pi
omp

# 2. Point Repowise at it
repowise init --provider omp --yes
```

To use it on an index you already have:

```bash
REPOWISE_PROVIDER=omp repowise update
```

Or persist it in `.repowise/config.yaml`:

```yaml
provider: omp
model: omp/default
```

## Provider

`omp` uses your local Oh My Pi install:

```bash
repowise init --provider omp --yes
```

It runs:

```bash
omp -p --mode json --no-session --no-extensions --no-skills --no-rules --no-tools \
    --config <scratch>/omp-config.yml --system-prompt <scratch>/system-prompt.md
```

Repowise sends the page prompt on stdin and parses the JSONL event stream Oh
My Pi writes to stdout, reading the assistant `message_end` event for the
answer text, token usage, stop reason and routed model. A non-zero exit is a
failure, with Oh My Pi's own reason on stderr.

Oh My Pi also exposes a richer bidirectional JSON-RPC transport (`--mode
rpc`) with request/response correlation, mid-turn steering and host-owned
tools, but none of that applies to a one-shot completion — print mode keeps
`omp` the same shape as `claude_cli`, `codex_cli` and `opencode`.

### Choosing a model

`omp/default` uses Oh My Pi's own configured default — no `--model` flag is
passed. To use a specific model, pass an Oh My Pi selector:

```bash
repowise init --provider omp --model omp/anthropic/claude-sonnet-4-5
```

Roles work too:

```bash
repowise init --provider omp --model omp/@slow
```

List what your local install can reach:

```bash
omp models --json
```

`repowise init`'s interactive model picker is populated from this same
catalog.

## Isolation

Each call runs in a fresh scratch directory: no `AGENTS.md`-style project
context, skills, rules or extensions are discovered, built-in tools are off,
and `--no-session` keeps pages out of your Oh My Pi session history.

A generated config overlay turns off four global settings a documentation run
should not inherit: `memory` and `autolearn` (so a run never writes to your
memory store or creates a skill), `advisor` (otherwise every finished page
costs a second model call to review it), and `tools.approvalMode`, pinned to
`always-ask`. That last one is the safety one: MCP servers from your own
config still load, and a global `yolo` would let the model run a mutating MCP
tool against your machine with no prompt. A headless run has no UI to answer
an approval, so such a call fails with a tool error instead of executing.

## Reasoning

Oh My Pi accepts every reasoning level Repowise names — the fullest coverage
of any CLI-backed provider. `auto` leaves your configured thinking level
alone; `off`/`none` map to `--thinking off`; `minimal`, `low`, `medium`,
`high`, `xhigh` and `max` pass straight through as `--thinking <level>`.

## Cost

`omp/*` is priced at **$0.00** in cost estimates and the cost history, because
Oh My Pi bills against its own account rather than a Repowise API key. The
cost Oh My Pi itself reports for the call is still recorded in the usage
record for auditing.

Concurrency defaults to 4 processes:

```bash
REPOWISE_OMP_CONCURRENCY=2 repowise generate --unwritten
```

## Known considerations

MCP servers configured in your own Oh My Pi config still load for every call,
and their tool schemas ride along on the request — Oh My Pi has no switch to
suppress them for a headless run. They're prompt-cached (roughly one cache
write per run, not per page), but a very large MCP surface still makes each
call slower and pricier against your account's budget. Disable the servers
you don't need in your Oh My Pi config if this matters to you.

Smoke check:

```bash
omp -p "Return exactly OK"
```

## Comparison with Codex CLI and OpenCode

| Aspect | `omp` | `codex_cli` | `opencode` |
|--------|-------|-------------|------------|
| CLI | `omp -p --mode json` | `codex exec` | `opencode run` |
| Auth | Oh My Pi's own login | `codex login` | OpenCode providers |
| Format | `-p --mode json` JSONL | `--json` JSONL | `--format json` JSONL |
| Reasoning | Full `--thinking` passthrough | `model_reasoning_effort` | Not passed (OpenCode manages) |
| Key storage | No | No | No |

## Embeddings are separate

Oh My Pi has no embeddings endpoint of its own, so `omp` cannot serve as an
embedder. Pick one separately if you want semantic search:

```bash
repowise init --provider omp --embedder ollama   # local, no key
```

Otherwise the index keeps `embedder: mock` and search stays lexical.

## Troubleshooting

**`Oh My Pi CLI not found`** — `omp` is not on `PATH`. Install it from
[github.com/can1357/oh-my-pi](https://github.com/can1357/oh-my-pi) and run
`omp` once to sign in.

**Not signed in** — an unauthenticated run fails with a non-zero exit and Oh
My Pi's own error on stderr, which Repowise surfaces verbatim. Run `omp` once
interactively, sign in, and retry.

**Rate-limit or budget pressure partway through a run** — lower
`REPOWISE_OMP_CONCURRENCY`, or scope the run with `--path`. Completed pages
are kept, so re-running picks up where it stopped with `--unwritten`.

## Official links

- [Oh My Pi](https://github.com/can1357/oh-my-pi)
- [RPC protocol reference](https://github.com/can1357/oh-my-pi/blob/main/docs/rpc.md)
