"""Leaf helpers shared across the augment submodules.

These import nothing from sibling submodules so the package import graph
stays acyclic: ``search``/``read_state``/``bash_staleness``/``codex`` all
depend on ``_shared``, never the other way around.
"""

from __future__ import annotations

import tempfile
from pathlib import Path


def _extract_output_text(tool_output: object) -> str:
    """Pull the textual portion of a Claude Code tool_output, defensively.

    Claude Code's hook payload shape varies by tool: Bash/PowerShell
    surface ``stdout``/``stderr``, Grep surfaces a structured dict
    (``mode``/``content``/``filenames``), Glob a bare ``filenames`` list.
    We only need a string we can count newlines in, so we accept any of
    the shapes captured from real hook payloads.
    """
    if isinstance(tool_output, str):
        return tool_output
    if not isinstance(tool_output, dict):
        return ""
    for key in ("output", "result", "content", "stdout", "text"):
        val = tool_output.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, list):
            # Some shapes wrap content as [{"type": "text", "text": "..."}].
            parts = []
            for item in val:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    t = item.get("text") or item.get("content")
                    if isinstance(t, str):
                        parts.append(t)
            if parts:
                return "\n".join(parts)
    # Grep files_with_matches / Glob: a list of paths, one per line.
    filenames = tool_output.get("filenames")
    if isinstance(filenames, list):
        parts = [f for f in filenames if isinstance(f, str)]
        if parts:
            return "\n".join(parts)
    return ""


def _relativize(file_path: str, repo_path: Path) -> str | None:
    """Repo-relative POSIX path for *file_path*, or None when outside it."""
    try:
        rel = Path(file_path).resolve().relative_to(Path(repo_path).resolve())
    except (ValueError, OSError):
        return None
    return rel.as_posix()


def _find_repo_root(cwd: Path) -> Path | None:
    """Walk up from cwd to find a directory with .repowise/.

    ``~/.repowise`` is the user-level config dir and a ``.repowise`` at the
    system temp ROOT is always a stray artifact (a tool that indexed with
    cwd=$TMP), so neither counts as a repo opt-in; repos legitimately created
    UNDER either directory still match.
    """
    current = Path(cwd).resolve()
    try:
        skip = {Path.home().resolve(), Path(tempfile.gettempdir()).resolve()}
    except (OSError, RuntimeError):
        skip = set()
    for _ in range(20):
        if current not in skip and (current / ".repowise").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None
