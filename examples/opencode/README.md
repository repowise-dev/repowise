# OpenCode Example

Use OpenCode as the LLM provider for Repowise wiki generation. OpenCode
handles its own auth and model selection; Repowise does not store OpenCode
API keys.

Unlike Codex, there is no `repowise init --opencode` editor-integration flag
today — this example covers the **provider** path documented in
[docs/agent/OPENCODE.md](../../docs/agent/OPENCODE.md).

## Prerequisites

```bash
curl -fsSL https://opencode.ai/install | bash
opencode                 # first run: configure provider + auth
opencode --version
```

Confirm Repowise sees the provider name:

```bash
repowise init --help | grep opencode
```

## Setup

Index with OpenCode as the page-generation provider:

```bash
cd /path/to/your-repo
repowise init --provider opencode --yes
```

Fast smoke without LLM wiki generation (index + graph/git/health only):

```bash
repowise init --index-only --yes
```

Then upgrade pages later with OpenCode:

```bash
repowise generate --provider opencode
# or:
REPOWISE_PROVIDER=opencode repowise update
```

Optional model selection (bare slug or `opencode/` prefix):

```bash
repowise init --provider opencode --model opencode/default --yes
opencode models            # list models OpenCode knows about
```

## Smoke Checks

```bash
opencode --version
opencode models
repowise status
```

Expected: OpenCode CLI responds, and `repowise status` shows an indexed repo
after `init` completes.

## What This Does Not Set Up

OpenCode has no Repowise project-file / hooks / plugin integration yet
(see the comparison table in [OPENCODE.md](../../docs/agent/OPENCODE.md)).
For MCP + hooks + skills in an agent CLI, use the [Codex example](../codex/)
instead.

## Related docs

- [OpenCode integration](../../docs/agent/OPENCODE.md)
- [Codex example](../codex/) (editor + MCP + plugin path)
- [CLI reference](../../docs/reference/CLI_REFERENCE.md)
