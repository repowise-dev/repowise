"""The files repowise writes for coding agents and editors.

Section names live behind a heavy import; a parity test holds the default
list to ``DEFAULT_SECTIONS``.
"""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import Setting

AREA = "editor"
_s = factory(AREA)
_e = factory(AREA, scope="env")

#: ``repowise.core.generation.editor_files.sections.DEFAULT_SECTIONS``, sorted.
DEFAULT_SECTIONS: tuple[str, ...] = (
    "architecture",
    "decisions",
    "entry_points",
    "health",
    "hotspots",
    "how_to_work",
    "key_modules",
)

_FILES: dict[str, str] = {
    "claude_md": ".claude/CLAUDE.md",
    "agents_md": "AGENTS.md",
    "cursor_rules": "the Cursor rules file",
    "kiro_steering": "the Kiro steering file",
    "vscode_mcp": "the VS Code MCP entry",
}

SETTINGS: tuple[Setting, ...] = (
    _s(
        "editor_setup",
        "bool",
        True,
        "Let update keep agent and editor files current. false leaves them alone.",
    ),
    *(
        _s(
            f"editor_files.{name}",
            "bool",
            True,
            f"Write {label}.",
            writer="repowise.cli.editor_files:set_editor_file_enabled",
        )
        for name, label in _FILES.items()
    ),
    _s(
        "editor_files.sections",
        "list",
        list(DEFAULT_SECTIONS),
        "Sections of the generated agent block. +name or -name adjusts the default; "
        "plain names replace it. Extra: code_health, fix_first, commands, "
        "how_to_work_full, tools.",
    ),
    _e(
        "REPOWISE_SKIP_EDITOR_SETUP",
        "bool",
        False,
        "Do not register repowise in editors' and agents' global config.",
        env_false=frozenset({"", "0", "false", "no"}),
    ),
)
