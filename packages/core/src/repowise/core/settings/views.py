"""The JSON every settings surface gives: one setting's view, and one change.

``repowise config ... --format json`` and the settings API return these same
shapes, so an agent reading one and a page reading the other see the same
fields.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from repowise.core.settings.model import Resolved, Setting, Source

__all__ = [
    "APPLIES",
    "change_report",
    "changed",
    "cli_value",
    "path_flag",
    "requirement",
    "resolve_many",
    "state",
    "undo_command",
    "view",
]

#: When a change takes effect, in plain words.
APPLIES = {
    "immediate": "Takes effect the next time it is read.",
    "update": "Takes effect on the next `repowise update`.",
    "reindex": "Takes effect after `repowise reindex`.",
    "restart": "Takes effect when the server or MCP process next starts.",
}


def resolve_many(root: Path | str, settings: tuple[Setting, ...]) -> list[Resolved]:
    """Each setting's value in *root*, reading each file once.

    Raises :class:`~repowise.core.repo_config.RepoConfigError` when a file
    does not parse.
    """
    from repowise.core.settings.writers import current, read_config

    files: dict[str | None, dict[str, Any]] = {}
    out = []
    for s in settings:
        if s.path not in files:
            files[s.path] = read_config(root, s)
        out.append(current(root, s, config=files[s.path]))
    return out


def changed(s: Setting, r: Resolved) -> bool:
    """Set somewhere, to something other than the default."""
    return r.source is not Source.DEFAULT and r.value != s.default


def state(s: Setting, r: Resolved) -> dict[str, Any]:
    """Where *s* stands: value, source, default, changed, problem, locked."""
    out = r.to_dict()
    out["default"] = s.to_dict()["default"]
    out["changed"] = changed(s, r)
    return out


def view(s: Setting, r: Resolved) -> dict[str, Any]:
    """One setting as ``config list``, ``get`` and the settings API give it."""
    return {**s.to_dict(), **state(s, r)}


def _bounds(s: Setting) -> str:
    low = None
    if s.minimum is not None:
        low = f"more than {s.minimum:g}" if s.exclusive_minimum else f"at least {s.minimum:g}"
    high = None if s.maximum is None else f"at most {s.maximum:g}"
    return " and ".join(b for b in (low, high) if b)


def requirement(s: Setting) -> str:
    """What a value of *s* must be, in plain words."""
    if s.choices:
        lead = "one of" if s.type == "str" else "any of"
        return f"{lead} {', '.join(s.choices)}"
    if s.type in ("int", "float"):
        kind = "a whole number" if s.type == "int" else "a number"
        bounds = _bounds(s)
        return f"{kind}, {bounds}" if bounds else kind
    return {
        "bool": "true or false",
        "list": "a list: a,b or [a, b]",
        "mapping": "a mapping: {key: value}",
    }.get(s.type, "text")


def path_flag(path: str | None) -> str:
    return f" --path {shlex.quote(path)}" if path else ""


def cli_value(value: Any) -> str:
    """*value* as ``repowise config set`` reads it back."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        simple = bool(value) and all(
            isinstance(v, str) and v and "," not in v and v.strip() == v for v in value
        )
        return shlex.quote(",".join(value) if simple else json.dumps(list(value)))
    if isinstance(value, dict):
        return shlex.quote(json.dumps(value))
    return shlex.quote(str(value))


def undo_command(m: Any, path: str | None = None) -> str:
    """The command that puts the file back where it was before the write.

    Read from the file's own value, not the effective one: an environment
    variable that decides the value says nothing about what the file held.
    """
    s, saved = m.setting, m.saved_before
    if saved.source in (Source.REPO_FILE, Source.USER_FILE):
        return f"repowise config set {s.key} {cli_value(saved.value)}{path_flag(path)}"
    return f"repowise config reset {s.key}{path_flag(path)}"


def change_report(action: str, change: Any, path: str | None = None) -> dict[str, Any]:
    """What one ``set`` or ``reset`` did: before, after, what moved with it, the undo."""
    s = change.setting
    moved = [m for m in (change.main, *change.also) if m.moved]
    override = s.env.name if change.after.source is Source.ENV and s.env else None
    return {
        "action": action,
        "key": s.key,
        "changed": change.changed,
        "before": state(s, change.before),
        "after": state(s, change.after),
        "also": [
            {
                "key": m.setting.key,
                "before": state(m.setting, m.before),
                "after": state(m.setting, m.after),
            }
            for m in change.also
        ],
        "effect": f"{s.description} {APPLIES[s.reload]}",
        "undo": [undo_command(m, path) for m in moved],
        "env_override": override,
    }
