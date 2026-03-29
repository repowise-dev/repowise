# Repowise Claude Code Plugin — Developer Guide

Internal reference for maintaining, updating, and releasing the plugin.

## Repository Layout

The plugin lives in two places:

1. **Monorepo**: `plugins/claude-code/` inside the main `repowise` repo
2. **Standalone**: `repowise-dev/repowise-plugin` on GitHub (what users install from)

Changes should be made in the monorepo first, tested locally, then synced to the standalone repo for release.

## File Structure

```
repowise-plugin/                    # Standalone repo root
├── .claude-plugin/
│   ├── plugin.json                 # Plugin identity (name, version, author)
│   └── marketplace.json            # Marketplace manifest (enables /plugin install)
├── .mcp.json                       # Auto-registers repowise MCP server
├── commands/                       # User-invoked slash commands
│   ├── init.md                     # /repowise:init — full setup wizard
│   ├── status.md                   # /repowise:status — health check
│   ├── update.md                   # /repowise:update — incremental sync
│   ├── search.md                   # /repowise:search — wiki search
│   └── reindex.md                  # /repowise:reindex — rebuild embeddings
├── skills/                         # Model-invoked skills (Claude auto-activates)
│   ├── codebase-exploration/
│   │   └── SKILL.md                # Teaches Claude to use get_overview, search_codebase
│   ├── pre-modification/
│   │   └── SKILL.md                # Teaches Claude to check get_risk before edits
│   ├── architectural-decisions/
│   │   └── SKILL.md                # Teaches Claude to query get_why
│   └── dead-code-cleanup/
│       └── SKILL.md                # Teaches Claude to use get_dead_code
├── .gitignore
├── CHANGELOG.md
├── LICENSE                         # AGPL-3.0 (same as main repo)
└── README.md                       # User-facing docs
```

### What each file does

**`.claude-plugin/plugin.json`** — The only required file. Defines plugin name (used as slash command namespace `repowise:`), version, and metadata. Claude Code reads this to register the plugin.

**`.claude-plugin/marketplace.json`** — Makes the repo a self-hosted marketplace. The `plugins[].source: "."` tells Claude Code the plugin root is the repo root itself. Without this file, users can't `/plugin install` from this repo.

**`.mcp.json`** — When the plugin is enabled, Claude Code auto-starts `repowise mcp` as an MCP server. This is what gives Claude access to the 8 tools. Uses `mcpServers` wrapper key. The `repowise` binary must be on PATH (the init command handles installation).

**`commands/*.md`** — Markdown files that become `/repowise:<filename>` slash commands. Frontmatter defines `description`, `allowed-tools`, etc. The `$ARGUMENTS` placeholder captures user input after the command name.

**`skills/*/SKILL.md`** — Model-invoked skills. Claude reads the `description` in frontmatter to decide when to activate them. Not shown in the `/` menu (`user-invocable: false`). Keep under 80 lines — bloated skills get ignored.

## How Commands vs Skills Work

| | Commands | Skills |
|---|---|---|
| **Trigger** | User types `/repowise:init` | Claude decides based on context |
| **Location** | `commands/<name>.md` | `skills/<name>/SKILL.md` |
| **Namespace** | `/repowise:<filename>` | `repowise:<dirname>` |
| **Frontmatter** | `allowed-tools`, `description` | `name`, `description`, `user-invocable` |
| **Content** | Step-by-step instructions for Claude to follow | Behavioral guidance for when/how to use tools |

## Local Development

### Testing the plugin

From the monorepo root:

```bash
claude --plugin-dir ./plugins/claude-code
```

Then test:
- `/repowise:init` — walks through setup
- `/repowise:status` — shows sync state
- Ask "how does the auth module work?" — should trigger codebase-exploration skill
- Start editing a file — should trigger pre-modification skill

After making changes, run `/reload-plugins` inside Claude Code to pick them up without restarting.

### Testing with multiple plugins

```bash
claude --plugin-dir ./plugins/claude-code --plugin-dir ./other-plugin
```

## Syncing to the Standalone Repo

The standalone repo is a flat copy of `plugins/claude-code/` plus standalone-only files (LICENSE, CHANGELOG.md, .gitignore).

### First-time setup (already done)

```bash
# Clone the standalone repo somewhere
git clone https://github.com/repowise-dev/repowise-plugin.git /tmp/repowise-plugin
```

### Syncing changes

```bash
# From the monorepo
PLUGIN_SRC="plugins/claude-code"
STANDALONE="/tmp/repowise-plugin"

# Copy plugin files (overwrite)
cp -r $PLUGIN_SRC/.claude-plugin $STANDALONE/
cp $PLUGIN_SRC/.mcp.json $STANDALONE/
cp -r $PLUGIN_SRC/commands $STANDALONE/
cp -r $PLUGIN_SRC/skills $STANDALONE/
cp $PLUGIN_SRC/README.md $STANDALONE/

# Review, commit, push
cd $STANDALONE
git diff
git add -A
git commit -m "Sync from monorepo: <describe changes>"
git push origin main
```

## Releasing a New Version

### 1. Bump the version

Update version in both files:
- `.claude-plugin/plugin.json` → `"version": "0.2.0"`
- `.claude-plugin/marketplace.json` → `"version": "0.2.0"` (in both `metadata` and `plugins[0]`)

Version in `plugin.json` wins if they conflict, but keep them in sync.

### 2. Update CHANGELOG.md

Add a new section at the top:

```markdown
## 0.2.0 (YYYY-MM-DD)

### Added
- New skill for X

### Changed
- Updated init command to support Y

### Fixed
- Fixed Z in search command
```

### 3. Commit and tag

```bash
git add -A
git commit -m "Release v0.2.0 — <summary>"
git tag v0.2.0
git push origin main
git push origin v0.2.0
```

### 4. Create GitHub release

```bash
gh release create v0.2.0 --title "v0.2.0 — <title>" --notes-file CHANGELOG_EXCERPT.md
```

Or create it from the web at https://github.com/repowise-dev/repowise-plugin/releases/new.

### 5. Users get the update

Users run:
```
/plugin update repowise@repowise
```

This pulls the latest from the marketplace repo. The version bump in `plugin.json` drives cache invalidation — without bumping the version, users may get a stale cached copy.

## Adding New Components

### Adding a new command

1. Create `commands/<name>.md` with frontmatter:
   ```yaml
   ---
   description: One-line description of what it does
   allowed-tools: Bash, Read
   ---
   ```
2. Write the step-by-step instructions Claude should follow.
3. It automatically becomes `/repowise:<name>`.

### Adding a new skill

1. Create `skills/<name>/SKILL.md` with frontmatter:
   ```yaml
   ---
   name: <name>
   description: >
     When to activate this skill. Be specific — Claude uses this text to decide
     whether to load the skill. Front-load keywords users would say.
   user-invocable: false
   ---
   ```
2. Write prescriptive instructions (not explanations). Keep under 80 lines.
3. The description is critical — it's the activation trigger.

### Adding supporting files to a skill

Skills can include extra files in their directory:
```
skills/my-skill/
├── SKILL.md           # Required — main instructions
├── reference.md       # Loaded on demand
└── scripts/
    └── helper.sh      # Claude can execute this
```

Reference them from SKILL.md so Claude knows they exist.

### Adding a hook

Create `hooks/hooks.json` at the plugin root:
```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [{ "type": "command", "command": "echo 'file modified'" }]
      }
    ]
  }
}
```

### Adding an agent

Create `agents/<name>.md` at the plugin root:
```yaml
---
name: agent-name
description: What this agent does
model: sonnet
---

System prompt for the agent...
```

## Key Gotchas

1. **Version bumps are required for updates.** Without bumping `plugin.json` version, `/plugin update` may serve a cached copy.

2. **Keep skills concise.** Claude ignores bloated skill files. Under 80 lines, prescriptive not verbose.

3. **Skill descriptions are truncated at 250 chars.** Front-load the key use case and trigger words.

4. **`.mcp.json` needs the `mcpServers` wrapper key.** Not just the server name at the top level.

5. **Don't put files inside `.claude-plugin/`.** Only `plugin.json` and `marketplace.json` go there. Everything else (`commands/`, `skills/`, `hooks/`) must be at the plugin root.

6. **`$ARGUMENTS` in commands.** If you use `$ARGUMENTS` in the markdown, it gets replaced with whatever the user typed after the slash command. If you don't use it, the args are appended as `ARGUMENTS: <value>`.

7. **The repowise CLI flags.** Always check the actual CLI (`repowise <cmd> --help`) before writing commands. Key flags that are easy to get wrong:
   - `--index-only` (not `--no-llm`)
   - `--concurrency` (not `--concurrent`)
   - `--commit-limit` (not `--git-depth`)
   - `--embedder` (not `--embedding-provider`)

8. **Graceful degradation.** Skills must handle the case where MCP tools fail (repowise not installed or repo not indexed). Always include fallback guidance pointing to `/repowise:init`.
