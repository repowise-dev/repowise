---
layout: default
title: Devin CLI Integration
nav_order: 5.7
---

# Devin CLI Integration
{: .no_toc }

Use Repowise with Devin via the `devin_cli` or `devin_acp` LLM providers. No API keys — the Devin CLI manages authentication and billing.
{: .fs-6 .fw-300 }

---

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Quick setup

```bash
curl -fsSL https://cli.devin.ai/install.sh | bash  # macOS / Linux / WSL
irm https://static.devin.ai/cli/setup.ps1 | iex     # Windows

devin auth login

cd /path/to/your-repo
repowise init --provider devin_cli --yes
```

Devin is detected automatically when `devin` is on `PATH` and logged in. No API keys to store.

## Provider

`devin_cli` uses your local Devin CLI:

```bash
repowise init --provider devin_cli --yes
```

It runs:

```bash
devin -p --prompt-file <tmp> --cd <repo> --model <model> --permission-mode auto --respect-workspace-trust false
```

Repowise writes the combined system + user prompt to a temp file, runs a non-interactive Devin session, and uses stdout as the response. Token usage is not available from the CLI, so `devin_cli/*` cost is reported as `$0.00`.

### Model selection

`devin_cli/default` uses Devin's configured default model. To pick a specific model:

```bash
repowise init --provider devin_cli --model devin_cli/opus
```

Models can be listed with:

```bash
devin models list
```

### Interactive selection

When you run `repowise init` interactively, the provider table includes `devin_cli` with a status indicator. If Devin is installed and authenticated, it shows as available and you can select it directly.

If Devin is not installed, the interactive prompt shows the install and login commands.

## `devin_acp` provider

`devin_acp` launches the Devin CLI in Agent Client Protocol mode for a full session and real token usage:

```bash
repowise init --provider devin_acp --yes
```

It runs:

```bash
devin acp
```

Repowise initializes the ACP connection, creates a session in the repository, switches Devin to `ask` mode (answer questions without editing files), optionally selects a specific model, and sends the combined prompt. It collects the streamed `agent_message_chunk` text and reads `PromptResponse.usage` for real input, output, and cached token counts.

### Model selection

`devin_acp/default` uses Devin's configured default model. To pick a specific model:

```bash
repowise init --provider devin_acp --model devin_acp/swe-1-7
```

Models can be listed with:

```bash
devin models list
```

### Requirements

`devin_acp` requires the `agent-client-protocol` package. It is installed automatically with Repowise.

## Reasoning

The `devin_cli` and `devin_acp` providers do not pass explicit reasoning effort flags. Devin selects the reasoning level through the model ID (for example, `claude-opus-5-low` vs `claude-opus-5-high` for ACP, or the same model ID for `devin_cli`). Pick the model variant that matches the desired reasoning effort.

## Safety

The provider invokes Devin with:

- `--permission-mode auto` — only auto-approves read-only tools.
- `--respect-workspace-trust false` — prevents an interactive workspace trust prompt.
- An explicit "do not edit any files" instruction in the prompt.

## Comparison with other CLI providers

| Aspect | `devin_cli` | `devin_acp` | `codex_cli` | `opencode` |
|---|---|---|---|---|
| CLI | `devin -p` | `devin acp` | `codex exec` | `opencode run` |
| Auth | `devin auth login` | `devin auth login` | `codex login` | OpenCode providers |
| Output | plain text | ACP `agent_message_chunk` | JSONL | JSONL |
| Token usage | estimated | real | real | real |
| Model list | `devin models list --format json` | `devin models list --format json` | `codex debug models` | `opencode models` |
| Key storage | No | No | No | No |

## Official docs

- [Devin CLI](https://docs.devin.ai/cli)
- [Devin CLI commands](https://docs.devin.ai/cli/reference/commands)
