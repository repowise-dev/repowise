"""The paths repowise writes into a repository, and how to tell its edits from a user's.

One list for every reader that must not take repowise's own output for the
user's change; change risk reads it through ``riskignore_patterns`` and the
traverser skips a file repowise wrote whole, so init never indexes its own
output. Two kinds:

* :data:`OWNED_PATTERNS` are written whole by repowise, so any change there is
  repowise's.
* :data:`MANAGED_FILES` are files users write in too, where repowise owns a
  fenced block and the placeholder written with a new file (markdown), or a
  ``repowise`` server entry, its hooks and its extension recommendation (JSON,
  TOML). A change to one is repowise's only when removing exactly what the
  writers emit leaves both sides equal (:func:`only_repowise_differs`), so a
  user's own edit still counts.

The writers live in the CLI, which imports core and not the other way round, so
the values they emit into a project's shared files are defined here and the
writers import them. A test over every agent target's ``describe_paths`` and
the generators keeps the path list in step with them.
"""

from __future__ import annotations

import json
import re
import tomllib

from .workspace.config import WORKSPACE_CONFIG_FILENAME, WORKSPACE_DATA_DIR

#: Gitignore-style. Writers: ``init`` / ``update`` (the index, config and
#: decisions), ``workspace init`` (the two workspace paths), the Kiro target
#: (its steering file, written whole).
OWNED_PATTERNS: tuple[str, ...] = (
    ".repowise/",
    f"{WORKSPACE_DATA_DIR}/",
    WORKSPACE_CONFIG_FILENAME,
    ".kiro/steering/repowise.md",
)

#: Root-relative, each with whether it is machine-specific: worth keeping out
#: of git per clone rather than shared with the team. ``.mcp.json`` and
#: the other agents' configs stay shared. Markdown holds the writers' exactly
#: marked blocks: the CLAUDE.md and AGENTS.md generators, and the instruction
#: blocks of the codex, opencode, hermes, copilot, vscode and cursor targets.
#: The JSON and TOML files hold a ``repowise`` server entry, hook or extension
#: recommendation from the agent targets (claude code, vscode, cursor, kiro,
#: codex, opencode).
_MANAGED: dict[str, bool] = {
    ".claude/CLAUDE.md": True,
    "AGENTS.md": False,
    ".github/copilot-instructions.md": True,
    ".cursor/rules/repowise.mdc": False,
    ".mcp.json": False,
    ".claude/settings.local.json": True,
    ".vscode/mcp.json": True,
    ".vscode/extensions.json": True,
    ".cursor/mcp.json": False,
    ".kiro/settings/mcp.json": False,
    ".codex/config.toml": False,
    ".codex/hooks.json": False,
    "opencode.json": False,
    "opencode.jsonc": False,
}
MANAGED_FILES: tuple[str, ...] = tuple(_MANAGED)
#: The managed files ``init`` offers to add to ``.git/info/exclude``.
MACHINE_SPECIFIC_FILES: tuple[str, ...] = tuple(p for p, machine in _MANAGED.items() if machine)

#: The VS Code extension the vscode target recommends in ``extensions.json``.
EXTENSION_ID = "repowise-dev.repowise"
#: The Claude Code coverage re-ingest hook, in ``.claude/settings.local.json``.
COVERAGE_HOOK_COMMAND = (
    "if command -v repowise-augment >/dev/null 2>&1; "
    "then exec repowise-augment --coverage-only; fi"
)
#: The Codex context and freshness hooks, in ``.codex/hooks.json``.
CODEX_HOOK_COMMAND = "repowise-augment --client codex"
#: What the cursor target writes above its block in a new rules file.
CURSOR_RULES_PREFIX = (
    "---\n"
    "alwaysApply: true\n"
    "---\n"
    "\n"
    "<!-- Add your own Cursor rules above or below the Repowise section. "
    "Repowise only updates the managed section between markers. -->\n"
)

#: The CLI targets' marker pairs: the instruction block the codex, opencode,
#: hermes, vscode and cursor targets write in the generators' format, and the
#: bare pair the copilot target writes beside it as its claim. A test checks
#: them against the targets' own constants.
_DISTILL_TAG = "REPOWISE_DISTILL"
_COPILOT_OWNER_PAIR = ("<!-- REPOWISE_COPILOT_CLI:START -->", "<!-- REPOWISE_COPILOT_CLI:END -->")

_HOOK_COMMANDS = (COVERAGE_HOOK_COMMAND, CODEX_HOOK_COMMAND)
#: The server entry's key in every target's config (each target's ``SERVER_NAME``).
_SERVER_NAME = "repowise"


def only_repowise_differs(path: str, old: str, new: str) -> bool:
    """Whether *old* and *new* (``""`` for absent) differ only in repowise's part of *path*."""
    before = _without_repowise(path, old)
    return before is not None and before == _without_repowise(path, new)


def _without_repowise(path: str, text: str) -> object:
    """*text* minus repowise's part, in a form two versions compare by; ``None`` if unreadable."""
    if not path.endswith((".json", ".jsonc", ".toml")):
        placeholder = next((p for p in _placeholders() if text.startswith(p)), "")
        text = text[len(placeholder) :]
        for start, end in _marker_pairs():
            text = re.sub(re.escape(start) + ".*?" + re.escape(end), "", text, flags=re.DOTALL)
        # Only the ends: a block is appended after a blank line and ends the file.
        return text.strip()
    if not text.strip():
        return {}
    try:
        data = tomllib.loads(text) if path.endswith(".toml") else json.loads(text)
    except ValueError:
        # JSONC with comments, or a broken file: never repowise's alone, so it counts.
        return None
    return _prune(data)


def _prune(value: object) -> object:
    """Drop the server entry, repowise's hooks and recommendation, and what that alone empties."""
    if isinstance(value, dict):
        items = [(k, v) for k, v in value.items() if k != _SERVER_NAME]
    elif isinstance(value, list):
        items = [(None, v) for v in value if not _is_ours(v)]
    else:
        return value
    kept = []
    for key, v in items:
        pruned = _prune(v)
        # A container emptied by the removal goes; one the user left empty
        # stays. Falsy scalars (0, false, "") are never emptied: ``not v``.
        if not (pruned or not v) or _lost_its_hooks(v, pruned):
            continue
        kept.append((key, pruned))
    return dict(kept) if isinstance(value, dict) else [p for _, p in kept]


def _is_ours(item: object) -> bool:
    """A list item the writers emit: the recommendation, or a hook running our command."""
    if isinstance(item, dict):
        return item.get("type") == "command" and item.get("command") in _HOOK_COMMANDS
    return item == EXTENSION_ID


def _lost_its_hooks(group: object, pruned: object) -> bool:
    """A hook group whose every hook was ours: the writers add the group with them."""
    return isinstance(group, dict) and bool(group.get("hooks")) and "hooks" not in pruned


def _marker_pairs() -> list[tuple[str, str]]:
    """The exact ``(start, end)`` markers every writer fences its block with."""
    from .generation.editor_files.agents_md import AgentsMdGenerator
    from .generation.editor_files.base import BaseEditorFileGenerator as Gen
    from .generation.editor_files.claude_md import ClaudeMdGenerator, WorkspaceClaudeMdGenerator

    generators = (ClaudeMdGenerator, WorkspaceClaudeMdGenerator, AgentsMdGenerator)
    tags = {*(g.marker_tag for g in generators), _DISTILL_TAG}
    pairs = [(Gen.MARKER_START_FMT.format(tag=t), Gen.MARKER_END_FMT.format(tag=t)) for t in tags]
    return [*pairs, _COPILOT_OWNER_PAIR]


def _placeholders() -> tuple[str, ...]:
    """The text each writer puts above its block when it creates the file."""
    from .generation.editor_files.agents_md import AgentsMdGenerator
    from .generation.editor_files.claude_md import ClaudeMdGenerator, WorkspaceClaudeMdGenerator

    return (
        ClaudeMdGenerator.user_placeholder,
        WorkspaceClaudeMdGenerator.user_placeholder,
        AgentsMdGenerator.user_placeholder,
        CURSOR_RULES_PREFIX,
    )
