# Shared plugin content

The single source for the skills and slash commands both agent plugins ship.

Everything under `plugins/claude-code/skills/`, `plugins/claude-code/commands/`,
`plugins/codex/skills/` and `packages/cli/.../agent_targets/_data/codex_prompts/`
is **generated from here**. Edit those files and the next test run fails; edit
these and regenerate:

```
python scripts/gen_plugin_content.py          # write
python scripts/gen_plugin_content.py --check  # report drift, write nothing
```

`plugins/codex/skills/` used to be a hand-copy of the Claude Code one and had
already drifted: descriptions rewritten, headings retitled, one directory
renamed, with nothing detecting it. Two files that say roughly the same thing in
different words look fine from either side alone.

## Authoring

One file per item. Frontmatter is stored **verbatim**, not as parsed keys, so a
render reproduces byte for byte instead of reflowing every folded scalar.

```
---
frontmatter: |            # used by any host with no override of its own
  description: ...
claude-code:              # optional per-host block
  dir: pre-modification   #   output directory, where the hosts disagree
  frontmatter: |          #   replaces the shared block entirely
    name: ...
---

<body, shared by every host, and the thing the golden test pins>
```

Skills carry per-host frontmatter because a description is trigger text tuned to
one host's dispatcher. Commands share one block, and each host drops the keys it
does not define (Codex prompt frontmatter is `description` and `argument-hint`;
`allowed-tools` is Claude Code's).

Write a slash-command reference as `{{cmd:risk}}`. Claude Code renders
`/repowise:risk`, Codex renders `/prompts:repowise-risk`.

## Where the Codex commands go

Not into `plugins/codex/`. A Codex plugin manifest has no slot for commands: it
may bundle `skills/`, `hooks/`, `assets/`, `.mcp.json` and `.app.json`, and
nothing else, so the only surface that yields a Codex slash command is
`~/.codex/prompts/`, which is local-only and written by the CLI. They ship as
package data and `repowise agents add --target=codex` installs them.
