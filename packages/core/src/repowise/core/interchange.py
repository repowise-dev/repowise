"""The envelope shared by repowise's normalized JSON formats.

``repowise-coverage-v1`` and ``repowise-evidence-v1`` are one JSON object
each, tagged by ``format`` and keyed by repo-relative POSIX paths. Reading
that envelope lives here once: the object, its tag, and the spelling of a
path. What each format puts inside it is the format's own module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: The key that names a normalized document's format and version.
FORMAT_KEY = "format"


def load_json_object(text: str) -> dict[str, Any] | None:
    """The JSON object *text* holds, ``None`` when it is not one."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def format_tag(data: dict[str, Any]) -> str | None:
    """The document's ``format`` tag, ``None`` when it names none."""
    tag = data.get(FORMAT_KEY)
    return tag if isinstance(tag, str) and tag else None


def posix_path(raw: Any) -> str:
    """*raw* spelled with forward slashes, ``.`` segments and repeats dropped."""
    return Path(str(raw).replace("\\", "/")).as_posix()


def repo_relative_path(raw: Any) -> tuple[str | None, str | None]:
    """*raw* as a repo-relative POSIX path, or ``(None, reason)`` when it is not one.

    Reasons: ``no_path`` (missing or empty), ``bad_path`` (a control
    character), ``absolute_path`` (a root or a drive letter: the writer did
    not make it relative), ``outside_repo`` (it climbs out with ``..``).
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, "no_path"
    if any(ord(c) < 0x20 for c in raw):
        return None, "bad_path"
    path = posix_path(raw.strip())
    if path.startswith("/") or (len(path) > 1 and path[0].isalpha() and path[1] == ":"):
        return None, "absolute_path"
    if path == ".." or path.startswith("../") or "/../" in path or path.endswith("/.."):
        return None, "outside_repo"
    if path == ".":
        return None, "no_path"
    return path, None


__all__ = [
    "FORMAT_KEY",
    "format_tag",
    "load_json_object",
    "posix_path",
    "repo_relative_path",
]
