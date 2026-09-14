# Claude Code as an LLM Provider

Repowise and Claude Code connect in **two independent directions**. This page
covers one of them; enabling it does not enable the other.

| Direction | What it is | Where to read |
|---|---|---|
| Repowise calls Claude Code | The `claude_cli` **LLM provider**. Repowise runs your local Claude Code CLI to write wiki pages, so a Claude subscription works instead of an API key. | This page |
| Claude Code calls Repowise | The `claude-code` **agent target**. Claude Code gets the repowise MCP tools, so you can ask it about your codebase. | [Integrations](INTEGRATIONS.md) |

## `claude_cli` Provider

Use `claude_cli` when you want page generation to run through your local Claude
Code CLI instead of an `ANTHROPIC_API_KEY`:

```bash
repowise init --provider claude_cli --yes
```

Or for an existing index:

```bash
REPOWISE_PROVIDER=claude_cli repowise generate --unwritten
```

To persist the choice, put it in `.repowise/config.yaml`:

```yaml
provider: claude_cli
model: claude_cli/claude-haiku-4-5
```

### Prerequisites

```bash
# Install Claude Code, then authenticate once:
claude login
```

Any plan that can run `claude -p` works — Pro, Max, Team or Enterprise. Repowise
never sees your credentials: the CLI holds them, and authentication happens out
of band exactly as it does for `codex_cli` and `opencode`.

### What the provider runs

```bash
claude -p --output-format json --model <model> --max-turns 1 \
  --strict-mcp-config --tools "" --system-prompt <system> [--effort <level>]
```

Repowise sends the **user** prompt on stdin (prompts carry file context and can
be large) and passes the **system** prompt via `--system-prompt`, which
*replaces* Claude Code's agent preamble rather than appending to it — repowise's
prompt is the whole instruction set, and the coding-agent framing only competes
with it. Repowise removes the tool catalog and adds a final system instruction
to answer directly in one response. The instruction is necessary even with a
single-turn budget: denying individual tools still lets the model attempt one,
consume that turn, and return `error_max_turns` without any page prose.

It parses the single JSON object from stdout, taking `result` as the page
content and mapping `usage`:

| Claude Code field | Repowise field |
|---|---|
| `usage.input_tokens` | `input_tokens` |
| `usage.output_tokens` | `output_tokens` |
| `usage.cache_read_input_tokens` | `cached_tokens` |
| `usage.cache_creation_input_tokens` | recorded in `usage`, not counted as cached |
| `stop_reason` | normalised `stop_reason` |

`claude_cli/*` models are priced at **$0.00**, because billing is handled by the
subscription rather than per-token API spend. The CLI's own `total_cost_usd` is
still recorded under `usage.reported_cost_usd` for auditing.

### Default model

`claude_cli/claude-haiku-4-5` is the default, matching the `anthropic` provider,
whose docstring calls haiku "ample for doc pages". To choose another:

```bash
repowise init --provider claude_cli --model claude_cli/claude-sonnet-4-6
```

The `claude_cli/` prefix is optional on input — a bare slug works too, and both
round-trip through `.repowise/config.yaml`:

```bash
repowise init --provider claude_cli --model claude-opus-4-6
```

### Concurrency

Each page is a full CLI process, and subscription limits are per account.
Serializing makes a 68-page wiki take about an hour; too much concurrency trips
the account limit and fails the run. The provider bounds itself to **4**
concurrent processes, and `init` caps `--concurrency` at 4 for CLI-backed
providers. Override with:

```bash
REPOWISE_CLAUDE_CLI_CONCURRENCY=2 repowise generate --unwritten
```

That variable raises as well as lowers the limit, so 4 is a default rather than
an enforced ceiling. Going higher is reasonable on a plan with a larger
allowance; it is also the fastest way to trip the account limit mid-run.

Budget roughly **9k output tokens and a few minutes per page**, drawn from the
same limits as your interactive Claude Code sessions. On a large repo, prefer
`--path` to scope a run rather than regenerating everything.

### Reasoning

Claude Code's `--effort` flag maps directly to `low`, `medium`, `high`, `xhigh`,
and `max`; `auto` omits the flag and preserves the CLI default. Repowise's
`off`, `none`, and `minimal` modes have no Claude CLI equivalent, so those are
**warned about and ignored** rather than killing an entire docs run.

## Two things worth knowing

- **`--bare` is never passed.** It looks like the right isolation flag, but it
  documents that "Anthropic auth is strictly `ANTHROPIC_API_KEY` or
  `apiKeyHelper` (OAuth and keychain are never read)" — which would defeat the
  entire point of this provider. Isolation comes from a neutral working
  directory plus `--strict-mcp-config`.
- **The subprocess does not run in your repo.** Claude Code auto-discovers
  `CLAUDE.md` from its working directory, so running inside the repo would inject
  your project's agent instructions into every page's prompt — spending tokens
  and letting repo-specific rules bias documentation prose. The provider runs in
  a fresh empty scratch directory for each call and removes it afterward;
  everything the generator needs is already in the prompt.

## Security

- Uses `asyncio.create_subprocess_exec` — never `shell=True`.
- Model names are validated against `^[a-zA-Z0-9][a-zA-Z0-9._/\-]*$` before they
  reach argv.
- All tools are disabled (`--tools ""`) and `--max-turns 1` is set, so
  the call is a pure completion with no filesystem or network access of its own.
- `--strict-mcp-config` stops the subprocess loading MCP servers from your user
  or project config.
