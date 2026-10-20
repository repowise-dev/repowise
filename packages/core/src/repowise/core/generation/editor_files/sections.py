"""Which sections the generated editor-file block carries.

The block is read into the agent's context on every turn, so the default is
the lean set: what an agent acts on before an edit. ``editor_files.sections``
in ``.repowise/config.yaml`` adjusts it with the grammar ``mcp.tools`` uses:
``+name`` / ``-name`` add to or remove from the default, and unprefixed names
are an explicit list that replaces it.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

DEFAULT_SECTIONS: frozenset[str] = frozenset(
    {
        "architecture",
        "key_modules",
        "entry_points",
        "hotspots",
        "health",
        "decisions",
        "how_to_work",
        "tools",
    }
)

OPT_IN_SECTIONS: frozenset[str] = frozenset(
    {"code_health", "fix_first", "commands", "how_to_work_full"}
)

ALL_SECTIONS: frozenset[str] = DEFAULT_SECTIONS | OPT_IN_SECTIONS


def resolve_sections(raw: object) -> frozenset[str]:
    """The section set a config value names; the default when it names none.

    Unknown names are dropped with a warning rather than failing the write,
    because the block is regenerated on every update.
    """
    if raw is None:
        return DEFAULT_SECTIONS
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, dict) or not isinstance(raw, Iterable):
        log.warning("editor_files.sections ignored: expected a list of names", value=raw)
        return DEFAULT_SECTIONS
    explicit: set[str] = set()
    added: set[str] = set()
    removed: set[str] = set()
    for name in raw:
        token = str(name).strip().lower()
        prefix = token[:1] if token[:1] in ("+", "-") else ""
        key = token[len(prefix) :]
        if key not in ALL_SECTIONS:
            log.warning("editor_files.sections: unknown section", section=name)
            continue
        {"+": added, "-": removed}.get(prefix, explicit).add(key)
    base = explicit if explicit else set(DEFAULT_SECTIONS)
    return frozenset((base | added) - removed)


def load_sections(repo_path: Path) -> frozenset[str]:
    """The section set ``.repowise/config.yaml`` asks for; the default on any read failure."""
    from repowise.core.repo_config import load_repo_config

    try:
        editor_files = load_repo_config(repo_path).get("editor_files")
    except Exception:  # a broken config is reported by the commands that need it
        return DEFAULT_SECTIONS
    if not isinstance(editor_files, dict):
        return DEFAULT_SECTIONS
    return resolve_sections(editor_files.get("sections"))
