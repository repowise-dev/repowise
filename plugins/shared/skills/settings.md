---
claude-code:
  frontmatter: |
    name: settings
    description: >
      Use when the user asks what Repowise can be configured to do, how to turn something on or off,
      what a feature costs in tokens or model calls, why Repowise behaves a certain way in this
      repository, or asks you to change a Repowise option. Covers every setting in
      .repowise/config.yaml, the user-level files and the REPOWISE_* environment variables.
    user-invocable: false
codex:
  frontmatter: |
    name: settings
    description: Use when the user asks what Repowise can be configured to do, how to turn a feature on or off, what it costs in tokens or model calls, why Repowise behaves a certain way in this repository, or asks you to change a Repowise option.
---

# Repowise Settings

Every Repowise setting (the `.repowise/config.yaml` keys, the two user-level
switches and the `REPOWISE_*` environment variables) is listed, explained and
changed through `repowise config`. Use it instead of reading or editing the
config files: it shows the value in effect and where it came from (default,
repository file, user file or environment), and `set` validates the value and
writes it where that setting lives.

## When to use it

- The user asks what can be changed, or how to turn a feature on or off.
- The user asks about cost: tokens added to each session, model calls during
  an update, hooks that run on every tool call.
- Something behaves unexpectedly ("why does this hook fire", "why are these
  pages missing") and a setting may explain it.
- The user asks you to change a Repowise option.

## Inspect

1. `repowise config list --changed`: what this repository changed from the
   defaults, with the source of each value. Start here when something
   behaves unexpectedly.
2. `repowise config list --area <area>`: one area's settings and values.
   Areas: account, attribution, decisions, distill, editor, events,
   generation, health, hooks, indexing, mcp, providers, server. Plain
   `repowise config list` shows them all by area.
3. `repowise config explain <key> --format json`: what one setting does, what
   it accepts, its default, when a change takes effect (`applies`) and the
   exact commands to change and reset it (`set_with`, `reset_with`).

Add `--format json` to any of them to parse the result; `list` returns
`{"repo", "settings": [...]}` with `key`, `description`, `value`, `source`,
`default` and `changed` per setting. Prefer `--area` or `--changed` with JSON:
the full list is large.

## Recommend

Read the descriptions of the settings in the relevant area against what you
know about this repository (its size, languages, whether it has a model key,
how the user works), and recommend a change only when it fits. Say what it
does, what it costs or saves, and when it takes effect.

## Change

1. Ask the user first, naming the key, the new value and the effect. Never
   change a setting on your own initiative.
2. `repowise config set <key> <value>`. A switch takes `true`/`false`, a
   list takes `a,b` or `[a, b]`. The text output is short; `--format json`
   repeats the before and after state of every setting the change moved.
3. Relay the effect and the undo commands it prints. Some switches move
   others with them (`decisions.mode` resets the finer decision switches):
   name the settings that moved, not only the one you set. If it says an
   environment variable wins over the file (`env_override` in JSON), tell the
   user that variable decides until it is unset.

`repowise config reset <key>` puts a setting back to its default. A refusal
exits 1 with `{"error", "message", ...}`: `unknown_setting` (the message
suggests near names), `invalid_value` (`accepts` says what fits), `env_only`
(the setting is an environment variable; tell the user where to set it) or
`config_unreadable` (the file is broken; nothing was written).

Never edit `.repowise/config.yaml` by hand: `set` validates the value and
keeps the rest of the file intact.
