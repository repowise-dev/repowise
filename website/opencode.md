---
layout: default
title: OpenCode Integration
nav_order: 5.6
---

# OpenCode Integration
{: .no_toc }

Use Repowise with OpenCode via the `opencode` LLM provider. No API keys — OpenCode manages auth and model selection.
{: .fs-6 .fw-300 }

---

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Quick setup

```bash
curl -fsSL https://opencode.ai/install | bash
opencode                      # first-run setup: configure your provider

cd /path/to/your-repo
repowise init --provider opencode --yes
```

OpenCode is detected automatically when it's on `PATH`. No API keys to store.

## Provider

`opencode` uses your local OpenCode CLI:

```bash
repowise init --provider opencode --yes
```

It runs:

```bash
opencode run --format json --dangerously-skip-permissions --dir <repo>
```

Repowise sends the prompt on **stdin**, parses OpenCode's **JSONL** output, and treats `opencode/*` cost as `$0.00`.

### Model selection

`opencode/default` uses OpenCode's configured default model. To pick a specific model:

```bash
repowise init --provider opencode --model opencode/deepseek-v4-pro
```

List available models:

```bash
opencode models
```

### Interactive selection

When you run `repowise init` interactively, the provider table includes `opencode` with a status indicator. If OpenCode is installed, it shows as available and you can select it directly.

If OpenCode is not installed, the interactive prompt shows:

```
Install:   curl -fsSL https://opencode.ai/install | bash
Setup:     opencode  (first run sets up your provider)
Models:    opencode models  (list available models)
```

## Reasoning

The opencode provider does not pass reasoning effort flags. OpenCode handles reasoning through its own model and agent configuration.

## Comparison with Codex CLI

| Aspect | `opencode` | `codex_cli` |
|--------|-----------|-------------|
| CLI | `opencode run` | `codex exec` |
| Auth | OpenCode providers | `codex login` |
| Format | `--format json` JSONL | `--json` JSONL |
| Reasoning | N/A (OpenCode manages) | `model_reasoning_effort` |
| Editor integration | None | MCP, hooks, plugin |
| Key storage | No | No |

## Official docs

- [OpenCode](https://opencode.ai)
- [OpenCode GitHub](https://github.com/anomalyco/opencode)
- [OpenCode Docs](https://opencode.ai/docs)
