"""Answer a Glob that ripgrep gave up on, from the index.

``Ripgrep search timed out after 20 seconds`` is the whole event. The tool
scanned the working tree, ran out of time, and returned nothing — not "no
matches", *nothing*, after twenty seconds of the agent waiting. A glob is a
path query, and the index already holds every path, so this is answerable
offline and instantly at the moment the failure happens.

**The headline here is wall clock, not tokens.** A timeout costs twenty
seconds and then usually a retry, and the retry is the same scan. The tokens
saved are incidental.

**Platform-local, and stated as such.** These timeouts cluster on Windows,
where the filesystem walk is slow enough to hit the ceiling; the same globs on
macOS return in well under a second. This ships because the event is already
registered and the answer is free, not because it is a general win.

Precision rules, in the same posture as the wrong-path rescue:

* **Only a real timeout.** Every other Glob failure — a rejected pattern, a
  user interrupt — is somebody else's problem and gets silence.
* **No brace expansion.** ``{a,b}`` is a shell construct this does not
  implement, and half-matching a pattern is worse than not answering it.
* **Say what was dropped.** The list is capped and the tail is counted, so the
  agent can see the answer is partial.
* **Never claim emptiness.** Zero indexed matches means the index has nothing
  to say, which is not the same as the tree having no such file, so it stays
  silent rather than reporting "no matches".
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - hot path keeps pathlib out of the graph
    from pathlib import Path

#: The failure verbatim, as the tool emits it.
TIMEOUT_ERROR = re.compile(r"Ripgrep search timed out after \d+ seconds?")

#: Paths named in the rescue before the tail is counted instead.
_MAX_LISTED = 20

#: Brace expansion is not implemented; a pattern using it is declined whole.
_BRACES = re.compile(r"[{}]")


def _translate(pattern: str) -> re.Pattern[str] | None:
    """A glob as a regex over POSIX paths, or None if it is not one we do.

    ``**`` crosses separators and ``*`` does not, which is the one rule a
    naive :func:`fnmatch.translate` gets wrong — there, ``*`` matches ``/`` and
    ``packages/*.py`` would answer with the whole tree. Written out rather than
    imported so the two cannot drift.
    """
    if not pattern or _BRACES.search(pattern):
        return None
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            if pattern.startswith("**", i):
                i += 2
                # `**/` swallows the separator so it can also match zero
                # directories: `**/x.py` has to find a root-level `x.py`.
                if pattern.startswith("/", i):
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(char))
            else:
                body = pattern[i + 1 : end].replace("\\", "\\\\")
                out.append(f"[{'^' + body[1:] if body.startswith('!') else body}]")
                i = end + 1
                continue
        else:
            out.append(re.escape(char))
        i += 1
    try:
        return re.compile("".join(out) + r"\Z", re.IGNORECASE)
    except re.error:
        return None


def matching_paths(repo_path: Path, pattern: str, limit: int = _MAX_LISTED) -> tuple[list[str], int]:
    """``(paths, total)`` indexed under *repo_path* matching *pattern*.

    ``total`` is the full match count, so the caller can say how many it is
    not showing. Any failure answers ``([], 0)`` — silence, never a guess.
    """
    matcher = _translate(pattern.replace("\\", "/").lstrip("./"))
    if matcher is None:
        return [], 0
    from . import fast_lookup

    conn = fast_lookup.connect(repo_path)
    if conn is None:
        return [], 0
    try:
        repo_id = fast_lookup.repo_id(conn, repo_path)
        if repo_id is None:
            return [], 0
        nodes = fast_lookup.file_nodes(conn, repo_id)
    except Exception:
        return [], 0
    finally:
        conn.close()

    # An indexed path can name a resolved external package rather than a file
    # in this tree; those are not answers to a glob over the working tree.
    hits = [n for n in nodes if ":" not in n.split("/")[0] and matcher.match(n)]
    hits.sort()
    return hits[:limit], len(hits)


def rescue(repo_path: Path, tool_input: dict, error_text: str) -> str | None:
    """The line to show for a timed-out Glob, or None to stay silent."""
    if not isinstance(tool_input, dict) or not TIMEOUT_ERROR.search(error_text):
        return None
    pattern = tool_input.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return None
    paths, total = matching_paths(repo_path, pattern.strip())
    if not paths:
        return None
    listed = "\n".join(f"  {p}" for p in paths)
    tail = f"\n  ({total - len(paths)} more)" if total > len(paths) else ""
    return (
        f"[repowise] That search timed out, so the index answered it instead. "
        f"{total} indexed path(s) match `{pattern.strip()}`:\n{listed}{tail}\n"
        "These come from the last index, so a file added since will be missing; "
        "re-run the search scoped to a directory if you need the live tree."
    )
