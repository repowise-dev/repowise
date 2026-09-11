# Oh My Pi as an LLM Provider

The `omp` **LLM provider** runs Repowise page generation through a local
**Oh My Pi** install, driven headlessly in one-shot print mode
(`omp -p --mode json`) instead of typed at a prompt. Oh My Pi already holds
credentials for whatever it is configured against — an OAuth subscription, a
provider account, or a plain API key living entirely in its own config — so
Repowise needs none of its own: no `REPOWISE_*` API key, no provider key,
nothing for `omp` to write to `.repowise/.env`.

## `omp` Provider

Use `omp` when you want page generation to run through your local Oh My Pi
install instead of any provider key:

```bash
repowise init --provider omp --yes
```

Or for an existing index:

```bash
REPOWISE_PROVIDER=omp repowise update
```

To persist the choice, put it in `.repowise/config.yaml`:

```yaml
provider: omp
model: omp/default
```

### Prerequisites

```bash
# Install Oh My Pi, then sign in once:
# https://github.com/can1357/oh-my-pi
omp
```

Running `omp` on its own walks you through sign-in, the same first-run step as
`claude login` or a bare `opencode`. Authentication happens out of band
exactly as it does for `claude_cli`, `codex_cli` and `opencode` — the CLI
holds the credentials, and Repowise only ever shells out to it.

### Model selection

`omp/default` is the default, and it is the zero-config path: no `--model`
flag is passed at all, and Oh My Pi's own configuration picks. A specific
model is an Oh My Pi *selector*, not a name Repowise invents:

```bash
repowise init --provider omp --model omp/anthropic/claude-sonnet-4-5
```

Roles work too:

```bash
repowise init --provider omp --model omp/@slow
```

The `omp/` prefix is optional on input — a bare selector round-trips through
`.repowise/config.yaml` the same way. List what your local install can
actually reach:

```bash
omp models --json
```

Repowise's interactive `repowise init` model picker is populated from this
same catalog, so what it offers always matches what Oh My Pi would actually
accept.

### What the provider runs

```bash
omp -p --mode json --no-session --no-extensions --no-skills --no-rules --no-tools \
    --config <scratch>/omp-config.yml --system-prompt <scratch>/system-prompt.md
```

Repowise sends the page prompt on stdin — print mode (`-p`) reads non-TTY
stdin as the initial message, so there is no argv length limit to work
around — then parses the JSONL event stream Oh My Pi writes to stdout. The
only event that matters is the assistant `message_end`: it carries the
finished answer text, the token usage, the stop reason, and the model Oh My
Pi actually routed to. A non-zero exit is a failure, with Oh My Pi's own
reason on stderr.

Oh My Pi also exposes a richer bidirectional JSON-RPC transport
([`docs/rpc.md`](https://github.com/can1357/oh-my-pi/blob/main/docs/rpc.md),
`--mode rpc`) with request/response correlation, mid-turn steering, and
host-owned tools — worth knowing about, but none of it applies to a
one-shot completion. Print mode answers one prompt and exits, which keeps
`omp` the same shape as `claude_cli`, `codex_cli` and `opencode`.

### Isolation

Each call runs in a fresh temporary scratch directory, resolved with
`Path.resolve()` and removed when the call finishes, so:

- No `AGENTS.md`-style project context, skills, rules or extensions are
  discovered — everything the generator needs is already in the prompt it
  sends.
- Built-in tools are off (`--no-tools`), so the call is a pure completion with
  no filesystem or network access of its own.
- `--no-session` keeps every generated page out of your Oh My Pi session
  history. A 68-page wiki run would otherwise leave 68 resumable sessions
  behind.
- A generated config overlay, passed with `--config`, overrides four global
  Oh My Pi settings a documentation run has no business inheriting:
  - `memory` and `autolearn` off — they add write-capable tools (`learn`,
    `manage_skill`), so a docs run never writes to your memory store or
    creates a skill on your behalf.
  - `advisor` off — otherwise every finished page triggers a second model call
    to review it, silently doubling the spend on a 68-page run.
  - `tools.approvalMode: always-ask` — MCP servers from your own config still
    load (see [Known considerations](#known-considerations)), and a global
    `yolo` would let the model run a mutating MCP tool against your machine
    with no prompt. The enum is a permissiveness ladder rather than a gate
    selector — `write` *auto-approves* writes — so `always-ask` is the only
    value that gates anything. A headless run has no UI to answer the prompt,
    which is exactly what makes it safe: Oh My Pi fails the call with `Tool
    "x" requires approval but no interactive UI available`, the model gets a
    tool error, and the turn still finishes with prose.

### Reasoning

Oh My Pi accepts every reasoning level Repowise names, which makes this the
fullest reasoning coverage of any CLI-backed provider:

| Repowise `--reasoning` | Oh My Pi flag |
|---|---|
| `auto` | no `--thinking` flag — your configured thinking level is left alone |
| `off`, `none` | `--thinking off` |
| `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | `--thinking <level>`, passed through unchanged |

### Cost

`omp/*` is priced at **$0.00** in Repowise's cost estimates and cost history,
because Oh My Pi bills against its own account rather than a Repowise API key
— the same reasoning that prices `claude_cli/*`, `codex_cli/*` and
`opencode/*` at zero. The cost Oh My Pi itself reports for the call is still
recorded in the usage record for auditing.

### Concurrency

Each page is a full Oh My Pi process against your own account, so the
provider bounds itself to **4** concurrent processes by default:

```bash
REPOWISE_OMP_CONCURRENCY=2 repowise generate --unwritten
```

The variable is a true override, not a clamp — it raises the limit as well as
lowers it, the same contract as `REPOWISE_CLAUDE_CLI_CONCURRENCY` and its
CLI-provider siblings.

Smoke check:

```bash
omp -p "Return exactly OK"
```

## Known considerations

MCP servers configured in your own Oh My Pi config still load for every call,
and their tool schemas still ride along on the request whether or not the
agent ever calls them — Oh My Pi has no switch to suppress them for a
headless run. They are prompt-cached, so in practice the cost is roughly one
cache write per run rather than per page, but a very large MCP surface will
still make each call slower and spend more of your account's budget than it
strictly needs to. If that matters to you, disable the MCP servers you don't
need in your Oh My Pi config; this provider does not do it on your behalf.

## Security

- Uses `asyncio.create_subprocess_exec` — never `shell=True`.
- Model names are validated against `^@?[a-zA-Z0-9][a-zA-Z0-9._/\-]*$` before
  they reach argv. The leading `@` is the only difference from the other
  CLI-backed providers' pattern, because Oh My Pi roles are spelled `@slow`.
- Every built-in tool, extension, skill and rule is disabled for the turn
  (`--no-tools --no-extensions --no-skills --no-rules`), and the scratch
  directory is resolved with `Path.resolve()` before it reaches argv.

## Comparison with Codex CLI and OpenCode

| Aspect | `omp` | `codex_cli` | `opencode` |
|--------|-------|-------------|------------|
| CLI command | `omp -p --mode json` | `codex exec` | `opencode run` |
| Auth | Oh My Pi's own login | `codex login` | OpenCode providers |
| Output format | JSONL via `-p --mode json` | JSONL via `--json` | JSONL via `--format json` |
| Reasoning modes | Full `--thinking` passthrough | `model_reasoning_effort` mapping | Not passed (OpenCode manages it) |

## Official Oh My Pi links

- [Oh My Pi](https://github.com/can1357/oh-my-pi)
- [RPC protocol reference](https://github.com/can1357/oh-my-pi/blob/main/docs/rpc.md)
