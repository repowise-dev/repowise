# Repowise Claude Code Plugin — Developer Guide

Internal reference for maintaining, updating, and releasing the plugin.

## Layout

The plugin and its marketplace both live in the main `repowise` repo — there is
no separate standalone plugin repo:

- **Marketplace manifest:** `.claude-plugin/marketplace.json` at the **repo root**.
  It lists the plugin and points at it via `source: "./plugins/claude-code"`.
- **Plugin:** `plugins/claude-code/`.

This means one install command for users (`/plugin marketplace add
repowise-dev/repowise`) and a single source of truth — nothing to sync.

## File structure

```
repowise/                              # main repo root
├── .claude-plugin/
│   └── marketplace.json               # marketplace manifest → source: ./plugins/claude-code
└── plugins/claude-code/               # the plugin
    ├── .claude-plugin/
    │   └── plugin.json                # plugin identity (name, version, author)
    ├── .mcp.json                      # auto-registers the repowise MCP server (9 tools)
    ├── hooks/
    │   └── hooks.json                 # PostToolUse → repowise-augment (proactive context)
    ├── commands/                      # user-invoked slash commands (/repowise:<name>)
    │   ├── init.md  status.md  update.md  search.md  reindex.md
    │   └── health.md  risk.md  dead-code.md  decision.md  doctor.md
    ├── skills/                        # model-invoked skills (Claude auto-activates)
    │   ├── codebase-exploration/SKILL.md
    │   ├── pre-modification/SKILL.md
    │   ├── change-review/SKILL.md
    │   ├── code-health/SKILL.md
    │   ├── architectural-decisions/SKILL.md
    │   └── dead-code-cleanup/SKILL.md
    ├── CHANGELOG.md
    ├── DEVELOPER.md                   # this file
    └── README.md                      # user-facing docs
```

The repo-root `LICENSE` (AGPL-3.0) covers the plugin; no separate copy needed.

### What each file does

**`.claude-plugin/marketplace.json` (repo root)** — Makes the repo a
self-hosted marketplace. `plugins[].source: "./plugins/claude-code"` points at
the plugin directory. Users `/plugin marketplace add repowise-dev/repowise`,
then `/plugin install repowise@repowise`.

**`plugins/claude-code/.claude-plugin/plugin.json`** — The plugin's required
manifest. Defines the name (the `repowise:` slash-command namespace), version,
and metadata. Do **not** put a `marketplace.json` next to it — the marketplace
lives at the repo root.

**`.mcp.json`** — When the plugin is enabled, Claude Code auto-starts
`repowise mcp` as an MCP server, giving Claude the **9 tools** (`get_overview`,
`get_answer`, `get_context`, `get_symbol`, `search_codebase`, `get_risk`,
`get_why`, `get_dead_code`, `get_health`). Uses the `mcpServers` wrapper key.
The `repowise` binary must be on PATH (`/repowise:init` installs it).

**`hooks/hooks.json`** — Registers a `PostToolUse` hook
(`Bash|Grep|Glob|Read|Edit|Write` → `repowise-augment`) so context enrichment works as soon as
the plugin is installed. This mirrors the hook `repowise init` writes to
`~/.claude/settings.json`; both firing is safe — see "Hook de-duplication".

**`commands/*.md`** — Become `/repowise:<filename>`. Frontmatter sets
`description` + `allowed-tools`; `$ARGUMENTS` captures user input.

**`skills/*/SKILL.md`** — Model-invoked. The `description` frontmatter is the
activation trigger — front-load the trigger words. Keep prescriptive and short.

## Commands vs skills

| | Commands | Skills |
|---|---|---|
| **Trigger** | User types `/repowise:init` | Claude decides from context |
| **Location** | `commands/<name>.md` | `skills/<name>/SKILL.md` |
| **Namespace** | `/repowise:<filename>` | `repowise:<dirname>` |
| **Content** | Step-by-step instructions to follow | Behavioral guidance for when/how to use tools |

## Hook de-duplication

The plugin bundles the same `repowise-augment` `PostToolUse` hook that
`repowise init` writes to `~/.claude/settings.json`. A user with both installed
would otherwise get duplicate enrichment on a single tool event. `repowise-augment`
guards against this: `_emit_response` claims a short-lived, content-keyed lock
(`_claim_emission` in `packages/cli/src/repowise/cli/commands/augment_cmd.py`)
so exactly one of the two firings emits. The guard is fail-open — if anything
goes wrong it emits rather than risk swallowing a real message.

If you change the bundled hook's command or matcher, keep it in step with the
installer in
`packages/cli/src/repowise/cli/editor_integrations/claude_config.py`.

## Local development

```bash
claude --plugin-dir ./plugins/claude-code
```

Then exercise it:
- `/repowise:init` — setup wizard
- `/repowise:status`, `/repowise:health`, `/repowise:risk main..HEAD`
- Ask "how does the auth module work?" → should trigger `codebase-exploration`
- "review this PR" / a diff → should trigger `change-review`
- Start editing a shared file → should trigger `pre-modification`

After edits, run `/reload-plugins` inside Claude Code to pick them up.

## Releasing a new version

The plugin version tracks the repowise release it ships alongside (e.g. `0.16.0`).

1. **Bump the version** in two places, kept identical:
   - `plugins/claude-code/.claude-plugin/plugin.json` → `"version"`
   - `.claude-plugin/marketplace.json` (repo root) → `plugins[0].version`
   Bumping the version is what invalidates the `/plugin update` cache — without
   it, users may get a stale cached copy.
2. **Update `CHANGELOG.md`** with an entry for the new version.
3. **Commit** with the rest of the release. Users update via
   `/plugin update repowise@repowise`.

## Adding components

- **Command:** create `commands/<name>.md` with `description` + `allowed-tools`
  frontmatter. It becomes `/repowise:<name>`. Verify any CLI flags against the
  real CLI first (see gotchas).
- **Skill:** create `skills/<name>/SKILL.md` with `name` + `description` +
  `user-invocable: false`. The description is the activation trigger.

## Key gotchas

1. **Bump the version on every release** or `/plugin update` may serve a cached copy.
2. **Keep skills short and prescriptive.** Bloated skills get ignored; the
   description is truncated (~250 chars) — front-load trigger words.
3. **`.mcp.json` needs the `mcpServers` wrapper key.**
4. **Only `plugin.json` goes in `plugins/claude-code/.claude-plugin/`.** Everything
   else (`commands/`, `skills/`, `hooks/`) sits at the plugin root. The
   **marketplace** manifest lives at the **repo** root, not the plugin's.
5. **There are 13 exposed MCP tools** (10 single-repo, plus 3 workspace-only:
   `get_blast_radius`, `get_conformance`, `get_architecture`). `get_dependency_path`
   and `get_execution_flows` exist in the server but are **not** exposed — never
   reference them in commands/skills.
6. **Verify CLI flags against the source.** Easy ones to get wrong:
   `--index-only` (not `--no-llm`); prefer the self-describing
   `--docs llm|deterministic` spelling when writing agent-facing commands.
   `--concurrency` (not `--concurrent`),
   `--commit-limit` (not `--git-depth`), `--embedder` (not `--embedding-provider`).
   `dead-code` has no `--group-by` (that's a `get_dead_code` MCP param only).
7. **`repowise risk` scores a whole change** (commit or `base..head`), not a
   file, and is **CLI/REST only** — it is *not* an MCP tool. The MCP `get_risk`
   is per-file blast radius + the PR `directive` block.
8. **Graceful degradation.** Skills/commands must handle repowise-not-installed
   and repo-not-indexed, pointing back to `/repowise:init`.
