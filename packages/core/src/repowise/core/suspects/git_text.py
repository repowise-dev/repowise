"""What git says about a change beyond its stored record: message body and
the symbol names in its diff's hunk headers.

Hunk headers are git's function-context lines (``@@ -1,2 +1,3 @@ def foo(``),
so the names are the enclosing definitions of the touched code as git's
funcname rules see them. Ceiling: a change touching more than
``MAX_DIFF_FILES`` files reads no symbols, since its diff (a vendored
dependency update) can run to hundreds of megabytes.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

MAX_DIFF_FILES = 100
_BATCH = 100
_HUNK_RE = re.compile(r"^@@ [^@]+ @@ ?(.*)$", re.MULTILINE)
_CALLABLE_RE = re.compile(r"([A-Za-z_]\w*)\s*\(")
_DEF_RE = re.compile(r"\b(?:class|struct|interface|enum|impl|trait|type|module)\s+([A-Za-z_]\w*)")


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return ""


def hunk_symbols(diff: str) -> tuple[str, ...]:
    names: dict[str, None] = {}
    for context in _HUNK_RE.findall(diff):
        m = _CALLABLE_RE.search(context) or _DEF_RE.search(context)
        if m:
            names[m.group(1)] = None
    return tuple(names)


def read_change_texts(
    repo_path: Path | str, heads: Iterable[tuple[str, int]]
) -> dict[str, tuple[str, tuple[str, ...]]]:
    """``head_sha -> (body, symbols)`` for ``(head_sha, files_changed)`` pairs."""
    repo = Path(repo_path)
    heads = list(heads)
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    shas = [s for s, _ in heads]
    for i in range(0, len(shas), _BATCH):
        raw = _git(repo, "log", "--no-walk=unsorted", "--format=%x00%H%n%b", *shas[i : i + _BATCH])
        for rec in raw.split("\x00"):
            sha, _, body = rec.partition("\n")
            if sha.strip():
                out[sha.strip()] = (body.strip(), ())
    small = [s for s, n in heads if n <= MAX_DIFF_FILES]
    for i in range(0, len(small), _BATCH):
        raw = _git(
            repo, "log", "--no-walk=unsorted", "-m", "--first-parent", "-U0",
            "--format=%x00%H", *small[i : i + _BATCH],
        )
        for rec in raw.split("\x00"):
            sha, _, diff = rec.partition("\n")
            sha = sha.strip()
            if sha in out:
                out[sha] = (out[sha][0], hunk_symbols(diff))
    return out


__all__ = ["MAX_DIFF_FILES", "hunk_symbols", "read_change_texts"]
