# OpenCode Integration

Repowise supports OpenCode via the `opencode` LLM provider, which runs
documentation generation through your local OpenCode CLI installation.
No API keys are managed by repowise — OpenCode handles all authentication
and model selection through its own provider system.

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
| Editor integration | None | `.codex/config.toml`, hooks, plugin |
| API keys stored | No | No |

## Official OpenCode Docs

- [OpenCode](https://opencode.ai)
- [OpenCode GitHub](https://github.com/anomalyco/opencode)
- [OpenCode Docs](https://opencode.ai/docs)
- [OpenCode Download](https://opencode.ai/download)
