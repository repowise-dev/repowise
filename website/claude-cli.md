---
layout: default
title: Claude Code Provider
nav_order: 5.7
---

# Claude Code Provider
{: .no_toc }

Use Repowise with your Claude subscription via the `claude_cli` LLM provider. No API key — the Claude Code CLI holds the auth.
{: .fs-6 .fw-300 }

---

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Quick setup

```bash
# 1. Install Claude Code, then authenticate once
claude login

# 2. Point Repowise at it
repowise init --provider claude_cli --yes
```

Any plan that can run `claude -p` works — Pro, Max, Team or Enterprise.

To use it on an index you already have:

```bash
REPOWISE_PROVIDER=claude_cli repowise generate --unwritten
```

Or persist it in `.repowise/config.yaml`:

```yaml
provider: claude_cli
model: claude_cli/claude-haiku-4-5
```

## Two directions, one name

This page is about Repowise **calling** Claude Code to write your wiki. The
opposite direction — Claude Code calling Repowise's MCP tools to answer questions
about your codebase — is the `claude-code` agent target, set up with
`repowise agents add --target=claude-code`. They are independent; enabling one
does not enable the other.

## Choosing a model

`claude_cli/claude-haiku-4-5` is the default, matching the `anthropic` provider.

```bash
repowise init --provider claude_cli --model claude_cli/claude-sonnet-4-6
```

The `claude_cli/` prefix is optional on input:

```bash
repowise init --provider claude_cli --model claude-opus-4-6
```

| Model | Notes |
|---|---|
| `claude-haiku-4-5` | Fastest, and ample for doc pages. Default. |
| `claude-sonnet-4-6` | Better prose, slower. |
| `claude-opus-4-6` | Highest quality, heaviest on subscription limits. |

## Cost

`claude_cli/*` is priced at **$0.00** in cost estimates and the cost history,
because a subscription is not per-token API spend. The CLI's own reported cost is
kept under `usage.reported_cost_usd` for auditing.

What it does consume is your **subscription rate limits**, the same ones your
interactive Claude Code sessions use. Budget roughly 9k output tokens and a few
minutes per page. A full wiki on a large repo is a real chunk of usage, so scope
runs with `--path` rather than reaching for `--all`:

```bash
repowise generate --path src/api
```

Concurrency defaults to 4 processes. The variable below overrides it in either
direction -- lower it if you are hitting limits, and note that raising it past 4
is allowed but is the quickest way to hit them:

```bash
REPOWISE_CLAUDE_CLI_CONCURRENCY=2 repowise generate --unwritten
```

## Reasoning

Claude Code's `--effort` flag maps directly to Repowise's `low`, `medium`,
`high`, `xhigh`, and `max` reasoning modes. `auto` leaves the flag unset and
uses the CLI default. The unsupported `off`, `none`, and `minimal` modes log a
warning and proceed with the default rather than failing the run.

## Embeddings are separate

Anthropic has no embeddings API, so `claude_cli` cannot serve as an embedder. If
you want semantic search rather than keyword-only, pick an embedder separately:

```bash
repowise init --provider claude_cli --embedder ollama   # local, no key
```

Otherwise the index keeps `embedder: mock` and search stays lexical.

## Troubleshooting

**`Claude Code CLI not found`** — `claude` is not on `PATH`. Install it from
[claude.com/claude-code](https://claude.com/claude-code).

**Authentication errors on the first page** — run `claude login`. Readiness
detection stops at "is it installed", because the CLI keeps credentials in a
keychain or OAuth store with no cheap way to probe login state.

**Rate-limit failures partway through a run** — lower
`REPOWISE_CLAUDE_CLI_CONCURRENCY`, or scope the run with `--path`. Completed
pages are kept, so re-running picks up where it stopped with `--unwritten`.
