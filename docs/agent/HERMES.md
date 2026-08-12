# Hermes Integration

[Hermes](https://hermes-agent.nousresearch.com) is an MCP host, so it can read a
repowise index directly. This page covers wiring it up and the two host details
that decide whether it works.

## Setup

```bash
repowise agents add --target=hermes                  # this repo and this machine
repowise agents add --target=hermes --scope=user     # the MCP server only
repowise agents add --target=hermes --scope=project  # the AGENTS.md section only
```

Hermes is at the **Good** tier: it gets the MCP tools and the config to reach
them, but no hook-level interception and no transcript mining. See the
[support matrix](INTEGRATIONS.md) for what that means in full.

Remove it with `repowise agents remove --target=hermes`, and check it with
`repowise agents` or `repowise doctor`.

## What each scope writes

Hermes reads exactly one config file per machine, and there is no project-level
equivalent. So the two scopes write different things:

| Scope | File | What it does |
|---|---|---|
| `user` | `config.yaml` in the Hermes home | Registers the MCP server under `mcp_servers.repowise`. Serves every repo. |
| `project` | `AGENTS.md` at the repo root | The managed instructions section, which Hermes loads per repo. |

The Hermes home is `$HERMES_HOME` when that is set, otherwise
`%LOCALAPPDATA%\hermes` on Windows and `~/.hermes` everywhere else. **Windows
does not use `~/.hermes`**, and an entry written there is not broken so much as
invisible, which is the harder problem to notice.

Because the registration is per machine rather than per repo, the entry passes no
repo path and lets the server resolve whichever repo Hermes was launched in. It
pins the absolute path of the repowise install that wrote it, so a later install
earlier on `PATH` cannot quietly take over.

## `platform_toolsets.cli` is deliberately left alone

This is the part most likely to look like a missing feature, so it is worth
stating plainly.

Hermes decides which MCP servers a platform sees with one rule: **if that
platform's toolset list already names one or more MCP servers, the list is an
allowlist and only those servers are exposed. Otherwise every enabled MCP server
is exposed.** A config with no `platform_toolsets` at all, or with a plain
`cli: [hermes-cli]`, takes the permissive branch.

So on an ordinary config repowise is reachable the moment it is in
`mcp_servers`, and adding `repowise` to that list would flip the config onto the
allowlist branch with exactly one entry, silently cutting off every other MCP
server the user had. Writing the key would not be redundant, it would be a
regression.

Repowise therefore adds itself to `platform_toolsets.cli` **only when that list
is already an allowlist**, which is the one case where leaving it out would get
repowise filtered back out. If the list contains the `no_mcp` sentinel, repowise
writes the server entry, changes nothing else, and tells you the entry will stay
inert until you remove the sentinel.

## Editing YAML without eating your config

`config.yaml` is commonly seeded from Hermes's heavily annotated example, so
repowise edits it in place rather than parsing and rewriting it. Comments, key
order, formatting, and any anchors or merge keys outside the one entry repowise
owns are returned untouched, and the file's existing line endings are preserved.

Every edit is checked before it lands: repowise re-parses what it is about to
write and compares it against the config it intended. If the two differ for any
reason, whether an unusual layout, a flow-style mapping or a duplicate key,
nothing is written and the file is left exactly as it was. If the file cannot be parsed at
all, repowise says so and prints the entry for you to paste:

```bash
repowise agents print-config hermes
```

Re-running `repowise agents add` reports `unchanged` rather than rewriting, and
`repowise agents remove` returns the file to what it was before.

## `AGENTS.md` is shared with Codex and OpenCode

All three agents read the same file and manage the same marker-delimited section
of it. All of them writing it is safe, because the section is idempotent.
Removing one of them while another is still wired leaves the section in place and
says so, rather than silently taking the instructions away from an agent that is
still configured.

Hermes loads exactly one project context file, preferring `.hermes.md` or
`HERMES.md` over `AGENTS.md`. Repowise deliberately writes `AGENTS.md` and not
the host-named file: creating a `HERMES.md` in a repo that already has an
`AGENTS.md` would take precedence over it and suppress the repo's existing
instructions entirely.

## Official Hermes Docs

- [Hermes Agent](https://hermes-agent.nousresearch.com)
- [MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
- [MCP config reference](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference)
- [Configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration)
