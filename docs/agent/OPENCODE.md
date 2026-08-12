# OpenCode Integration

Repowise and OpenCode connect in **two independent directions**, and it is worth
knowing which one you want before reading further. They share nothing but a name.

| Direction | What it is | Where to read |
|---|---|---|
| Repowise calls OpenCode | The `opencode` **LLM provider**. Repowise runs your local OpenCode CLI to generate wiki pages, instead of using an API key. | [`opencode` Provider](#opencode-provider) below |
| OpenCode calls Repowise | The `opencode` **agent target**. OpenCode gets the repowise MCP tools, so you can ask it about your codebase. | [OpenCode as an MCP host](#opencode-as-an-mcp-host) below |

You can use either, both, or neither. Enabling one does not enable the other.

## OpenCode as an MCP host

```bash
repowise agents add --target=opencode
```

This registers the repowise MCP server under the `mcp` key of your
`opencode.jsonc` (or an existing `opencode.json`) and adds a managed section to
`AGENTS.md`. By default it writes both the repo-local pair at the repo root and
the per-machine pair under `$XDG_CONFIG_HOME/opencode`, falling back to
`~/.config/opencode`; pass `--scope=project` or `--scope=user` for one of them.
That config path is the same on Windows, where OpenCode does not read
`%APPDATA%`.

OpenCode is at the **Good** tier: it gets the MCP tools and the config to reach
them, but no hook-level interception and no transcript mining. See the
[support matrix](INTEGRATIONS.md) for what that means in full.

Two things worth knowing:

- **Comments in the config are safe.** OpenCode accepts JSONC, and repowise does
  not rewrite a config it cannot parse as strict JSON. If yours has comments,
  `repowise agents add` leaves it alone and tells you to run
  `repowise agents print-config opencode` and paste the entry yourself.
- **`AGENTS.md` is shared with Codex and Hermes.** All three agents read the same
  file, and all three manage the same marker-delimited section of it. Removing one
  of them leaves the section in place while another is still wired, and says so.

Remove it with `repowise agents remove --target=opencode`, and check it with
`repowise agents` or `repowise doctor`.

## Prerequisites

Install OpenCode:

```bash
curl -fsSL https://opencode.ai/install | bash
```

Then run `opencode` once to set up your model provider and authentication:

```bash
opencode
```

Verify the CLI is available:

```bash
opencode --version
```

Repowise detects OpenCode automatically: when `opencode` is in `PATH`,
the interactive provider selection shows it as "available" and it can
be used immediately.

## `opencode` Provider

Use `opencode` when you want Repowise page generation to run through
your local OpenCode CLI instead of an API key:

```bash
repowise init --provider opencode --yes
```

You can also persist it:

```bash
REPOWISE_PROVIDER=opencode repowise update
```

The provider runs:

```bash
opencode run --format json --dangerously-skip-permissions --dir /absolute/path/to/repo
```

Repowise sends the combined system + user prompt on **stdin**, parses
OpenCode's **JSONL** output (extracting text from `text` events and
token usage from `step_finish` events), and treats `opencode/*` cost
as `$0.00` because billing is handled by OpenCode's own subscription/auth.

### Default model

`opencode/default` uses OpenCode's configured default model — no
`--model` flag is passed. To use a specific model:

```bash
repowise init --provider opencode --model opencode/deepseek-v4-pro
```

Or use a bare model slug (the `opencode/` prefix is optional):

```bash
repowise init --provider opencode --model deepseek-v4-pro
```

### Listing available models

```bash
opencode models           # all available models
opencode models opencode  # models from the opencode provider
```

### Reasoning

The opencode provider does not pass reasoning effort flags. OpenCode
handles reasoning internally through its own model/agent configuration.

## Security

The provider enforces several safety measures:

- Uses `asyncio.create_subprocess_exec` (no shell), so every argument
  is passed as a distinct list element — shell injection is impossible.
- Model names are validated against a safe character set
  (`[a-zA-Z0-9][a-zA-Z0-9._/\-]*`), rejecting shell metacharacters
  before anything reaches the subprocess.
- All paths are resolved with `Path.resolve()` before being passed to
  `--dir`.
- Subprocess execution is serialized via `asyncio.Semaphore(1)`.
- A 600-second hard timeout with process kill prevents runaway calls.

## Comparison with Codex CLI

| Aspect | `opencode` | `codex_cli` |
|--------|-----------|-------------|
| CLI command | `opencode run` | `codex exec` |
| Auth | OpenCode providers | `codex login` |
| Output format | JSONL via `--format json` | JSONL via `--json` |
| Reasoning modes | Not passed (OpenCode manages it) | `model_reasoning_effort` mapping |
| Sandbox | OpenCode manages its own | `--sandbox read-only` |
| Model discovery | `opencode models` | `codex debug models --bundled` |
| MCP host integration | `opencode.jsonc`, `AGENTS.md` (Good tier) | `.codex/config.toml`, hooks, plugin, prompts (Full tier) |
| API keys stored | No | No |

## Official OpenCode Docs

- [OpenCode](https://opencode.ai)
- [OpenCode GitHub](https://github.com/anomalyco/opencode)
- [OpenCode Docs](https://opencode.ai/docs)
- [OpenCode Download](https://opencode.ai/download)

## Example walkthrough

Short smoke path: [examples/opencode/](../../examples/opencode/).
