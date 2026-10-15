"""What git says about a change beyond its stored record: message body and
the symbol names in its diff's hunk headers.

Hunk headers are git's function-context lines (``@@ -1,2 +1,3 @@ def foo(``),
so the names are the enclosing definitions of the touched code as git's
funcname rules see them. Ceiling: a change touching more than
``MAX_DIFF_FILES`` files reads no symbols, since its diff (a vendored
dependency update) can run to hundreds of megabytes.

Trailers that name people (``Signed-off-by``, ``Co-authored-by``,
``Reviewed-By`` and any trailer line carrying an email) are stripped from the
body, so no person's name reaches the text index or its evidence.
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
_PERSON_TRAILER_RE = re.compile(
    r"^[ \t]*(?:[A-Za-z][\w-]*-by|Cc|Author|Committer)[ \t]*:.*$"
    r"|^[ \t]*[A-Za-z][\w-]*[ \t]*:.*<[^<>\s]+@[^<>\s]+>.*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_person_trailers(body: str) -> str:
    """*body* without trailer lines that name a person."""
    return _PERSON_TRAILER_RE.sub("", body).strip()


def _git(repo: Path, *args: str) -> str | None:
    """Stdout, or ``None`` when git fails (one unknown sha fails the whole call)."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def _log(repo: Path, args: list[str], shas: list[str]) -> str:
    """``git log`` over *shas* in batches; a failed batch is retried sha by sha."""
    parts: list[str] = []
    for i in range(0, len(shas), _BATCH):
        batch = shas[i : i + _BATCH]
        raw = _git(repo, "log", "--no-walk=unsorted", *args, *batch)
        if raw is None:
            raw = "".join(r for sha in batch if (r := _git(repo, "log", "--no-walk", *args, sha)))
        parts.append(raw)
    return "".join(parts)


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
    """``head_sha -> (body, symbols)`` for ``(head_sha, files_changed)`` pairs.

    A sha git cannot read is left out; the caller counts what is missing.
    """
    repo = Path(repo_path)
    heads = [(s.strip(), n) for s, n in heads if s and s.strip()]
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    for rec in _log(repo, ["--format=%x00%H%n%b"], [s for s, _ in heads]).split("\x00"):
        sha, _, body = rec.partition("\n")
        if sha.strip():
            out[sha.strip()] = (strip_person_trailers(body), ())
    small = [s for s, n in heads if n <= MAX_DIFF_FILES and s in out]
    for rec in _log(repo, ["-m", "--first-parent", "-U0", "--format=%x00%H"], small).split("\x00"):
        sha, _, diff = rec.partition("\n")
        sha = sha.strip()
        if sha in out:
            out[sha] = (out[sha][0], hunk_symbols(diff))
    return out


__all__ = ["MAX_DIFF_FILES", "hunk_symbols", "read_change_texts", "strip_person_trailers"]
