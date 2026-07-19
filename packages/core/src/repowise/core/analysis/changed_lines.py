"""Map a git change to the source lines it touches, per file.

``repowise risk`` reads a change as aggregate counts (``--numstat``); the
test-impact query needs the actual changed *line numbers* so it can intersect
them with per-test coverage. This module parses a ``--unified=0`` diff into
per-file records covering **both** sides of the change:

* the *new* side (``new_lines``) - the lines that exist in the head/index/
  working tree, which is the space coverage is keyed in;
* the *old* side (``old_ranges``) plus the removed/added line text, which is
  what the fix-shape classifier and (later) SZZ blame read at ``fix^``.

One parser, two consumers: :func:`changed_lines` keeps its new-side-only
contract, and the git indexer's prior-defect pass reuses
:func:`parse_unified_diff` directly rather than growing a second implementation.

Pure ``git`` subprocess walking, reusing the wrapper from the change-risk
feature extractor (no new dependency, deterministic).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .change_risk.features import _git

# ``@@ -a,b +c,d @@`` - both sides. ``b``/``d`` default to 1 when omitted; a
# count of 0 means "nothing on that side" (pure insertion / pure deletion).
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class FileDiff:
    """One file's slice of a ``--unified=0`` diff.

    *path* is the new-side path, falling back to the old-side one for a
    deletion. *old_ranges* are inclusive ``(start, end)`` line spans on the
    pre-change file - the space ``git blame <sha>^`` is keyed in. A hunk with
    an old count of 0 is a pure insertion and contributes no range (there is
    nothing to blame).
    """

    path: str
    new_lines: set[int] = field(default_factory=set)
    old_ranges: list[tuple[int, int]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)


def _header_path(raw: str) -> str | None:
    """Normalize a ``--- a/x`` / ``+++ b/x`` header path. ``None`` for /dev/null."""
    path = raw.strip()
    # git quotes paths with special chars ("b/pa\tth"); strip the quotes so the
    # common (unquoted) key still resolves. Rare enough to accept the imperfect
    # unescaping.
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1]
    if path == "/dev/null":
        return None
    return path[2:] if path[:2] in ("a/", "b/") else path


def parse_unified_diff(diff: str) -> dict[str, FileDiff]:
    """Parse a ``--unified=0`` diff into per-file, two-sided records.

    A ``--- x`` line only counts as a file header when the next line is its
    ``+++ y`` partner: inside a hunk, a *removed* line whose own text starts
    with ``--`` renders as ``--- ...`` and would otherwise be misread as the
    start of a new file (same hazard for ``+++`` on the added side).
    """
    result: dict[str, FileDiff] = {}
    current: FileDiff | None = None
    lines = diff.splitlines()

    def _record(path: str) -> FileDiff:
        entry = result.get(path)
        if entry is None:
            entry = result[path] = FileDiff(path=path)
        return entry

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            # A deletion has no new-side path; key it by the old one so the
            # file still shows up for shape classification.
            path = _header_path(lines[i + 1][4:]) or _header_path(line[4:])
            current = _record(path) if path else None
            i += 2
            continue
        if line.startswith("@@") and current is not None:
            if (m := _HUNK_RE.match(line)) is not None:
                old_start = int(m.group(1))
                old_count = int(m.group(2)) if m.group(2) is not None else 1
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) is not None else 1
                if old_count > 0:
                    current.old_ranges.append((old_start, old_start + old_count - 1))
                if new_count > 0:
                    current.new_lines.update(range(new_start, new_start + new_count))
        elif current is not None:
            if line.startswith("-"):
                current.removed.append(line[1:])
            elif line.startswith("+"):
                current.added.append(line[1:])
        i += 1
    return result


def _parse_unified_diff(diff: str) -> dict[str, set[int]]:
    """New-side changed lines per file - the coverage-intersection view.

    Files whose only change was a deletion (no new-side lines) are dropped:
    they cannot intersect coverage, and would otherwise read as "touched".
    """
    return {path: f.new_lines for path, f in parse_unified_diff(diff).items() if f.new_lines}


def _verify_ref(repo_path: str, ref: str) -> None:
    # check=False: `rev-parse --verify --quiet` deliberately exits 1 with empty
    # stdout for a missing ref, which is the signal we test for here. Without the
    # opt-out, _git's returncode check (see change_risk.features._git) would raise
    # CalledProcessError first and mask the friendly ValueError this raises.
    if not _git(["rev-parse", "--verify", "--quiet", ref], repo_path, check=False).strip():
        raise ValueError(f"unknown revision {ref!r}")


def changed_lines(
    repo_path: str,
    revspec: str | None = None,
    *,
    staged: bool = False,
) -> tuple[dict[str, set[int]], str]:
    """Return ``({file: changed_lines}, label)`` for a change.

    *revspec* mirrors ``repowise risk``: ``base..head`` is a range, a bare ref
    is a single commit. With no *revspec* (or *staged*), the staged diff
    (``git diff --cached``) is used - the "what will I commit" case. *label*
    is a human string naming what was diffed. Raises ``ValueError`` on an
    unknown revision so the caller can fail loudly rather than silently
    reporting "no changes".
    """
    if staged or not revspec:
        diff = _git(["diff", "--cached", "--unified=0"], repo_path)
        return _parse_unified_diff(diff), "staged changes"

    if ".." in revspec:
        base, _, head = revspec.partition("..")
        head = head or "HEAD"
        _verify_ref(repo_path, base)
        _verify_ref(repo_path, head)
        diff = _git(["diff", "--unified=0", f"{base}..{head}"], repo_path)
        return _parse_unified_diff(diff), f"{base}..{head}"

    _verify_ref(repo_path, revspec)
    # --format= drops the commit message so only the diff body is parsed.
    diff = _git(["show", "--unified=0", "--format=", revspec], repo_path)
    return _parse_unified_diff(diff), revspec
