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
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .change_risk.features import _git, split_revspec

# ``@@ -a,b +c,d @@`` - both sides. ``b``/``d`` default to 1 when omitted; a
# count of 0 means "nothing on that side" (pure insertion / pure deletion).
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

#: Pinned header prefixes: ``diff.noprefix`` or a custom prefix in the user's
#: config would otherwise make :func:`_header_path` strip a real ``a/`` or
#: ``b/`` directory, or keep a prefix that names no file.
DIFF_PREFIXES = ("--src-prefix=a/", "--dst-prefix=b/")


@dataclass
class FileDiff:
    """One file's slice of a ``--unified=0`` diff.

    *path* is the new-side path, falling back to the old-side one for a
    deletion. *old_ranges* are inclusive ``(start, end)`` line spans on the
    pre-change file - the space ``git blame <sha>^`` is keyed in. A hunk with
    an old count of 0 is a pure insertion and contributes no range (there is
    nothing it replaced); it records its *insert_anchors* instead - the old-side
    line the new lines went in after, which is the only handle SZZ has on code
    that was added rather than rewritten. 0 means "inserted at the top".
    """

    path: str
    new_lines: set[int] = field(default_factory=set)
    old_ranges: list[tuple[int, int]] = field(default_factory=list)
    insert_anchors: list[int] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)


_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13}


def _c_unquote(text: str) -> str:
    """Undo git's C-style path quoting: ``caf\\303\\251.py`` -> ``café.py``."""
    out = bytearray()
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in "01234567":
                out.append(int(text[i + 1 : i + 4], 8) & 0xFF)
                i += 4
                continue
            out += bytes([_C_ESCAPES[nxt]]) if nxt in _C_ESCAPES else nxt.encode("utf-8")
            i += 2
            continue
        out += text[i].encode("utf-8")
        i += 1
    return out.decode("utf-8", errors="replace")


def _header_path(raw: str) -> str | None:
    """Normalize a ``--- a/x`` / ``+++ b/x`` header path. ``None`` for /dev/null."""
    path = raw.strip()
    # git quotes a path holding a non-ASCII byte, a tab, a quote or a backslash
    # ("b/caf\303\251.py"); left escaped it names no file, and a reader that
    # looks the file up would skip it.
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = _c_unquote(path[1:-1])
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
                elif new_count > 0:
                    # ``@@ -N,0 +M,k @@``: git names the old-side line the block
                    # was inserted *after*, which is 0 for an insertion at the
                    # top of the file.
                    current.insert_anchors.append(old_start)
                if new_count > 0:
                    current.new_lines.update(range(new_start, new_start + new_count))
        elif current is not None:
            if line.startswith("-"):
                current.removed.append(line[1:])
            elif line.startswith("+"):
                current.added.append(line[1:])
        i += 1
    return result


def line_ranges(lines: Iterable[int]) -> tuple[tuple[int, int], ...]:
    """Adjacent line numbers as inclusive spans: ``{1,2,3,7}`` -> ``((1,3),(7,7))``."""
    spans: list[tuple[int, int]] = []
    for line in sorted(lines):
        if spans and line == spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], line)
        else:
            spans.append((line, line))
    return tuple(spans)


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
    working_tree: bool = False,
) -> tuple[dict[str, set[int]], str]:
    """Return ``({file: changed_lines}, label)`` for a change.

    *revspec* mirrors ``repowise risk``: ``base..head`` is a range,
    ``base...head`` the change since the two forked, a bare ref a single commit. With no *revspec* (or *staged*), the staged diff
    (``git diff --cached``) is used - the "what will I commit" case.
    *working_tree* widens that to everything ``HEAD`` does not have, staged or
    not, matching what change risk counts for an uncommitted change. *label*
    is a human string naming what was diffed. Raises ``ValueError`` on an
    unknown revision so the caller can fail loudly rather than silently
    reporting "no changes".
    """
    if working_tree:
        # Untracked files are absent by design: they are new, so neither caller
        # (prior fixes, per-test coverage) has a row to find for them anyway.
        diff = _git(["diff", "--unified=0", *DIFF_PREFIXES, "HEAD"], repo_path)
        return _parse_unified_diff(diff), "working tree"

    if staged or not revspec:
        diff = _git(["diff", "--cached", "--unified=0", *DIFF_PREFIXES], repo_path)
        return _parse_unified_diff(diff), "staged changes"

    if (parts := split_revspec(revspec)) is not None:
        # ``base...head`` is what a pull request changed: git diffs from the
        # merge-base, so commits that landed on base meanwhile stay out.
        base, sep, head = parts
        _verify_ref(repo_path, base)
        _verify_ref(repo_path, head)
        label = f"{base}{sep}{head}"
        diff = _diff(repo_path, ["diff", "--unified=0", *DIFF_PREFIXES, label])
        return _parse_unified_diff(diff), label

    _verify_ref(repo_path, revspec)
    if is_shallow_root(repo_path, revspec):
        # A shallow clone's oldest commit has its parents cut off, so git would
        # diff it against the empty tree and every line would read as changed.
        raise ValueError(f"{revspec!r} has no parent in this shallow clone; fetch more history")
    # --format= drops the commit message so only the diff body is parsed.
    # -m --first-parent matches what change risk counts on a merge; without it
    # git's combined diff emits nothing at all and a merged PR reads as empty.
    args = ["show", "--unified=0", *DIFF_PREFIXES, "--format=", "-m", "--first-parent", revspec]
    return _parse_unified_diff(_diff(repo_path, args)), revspec


def _diff(repo_path: str, args: list[str]) -> str:
    """Run a diff, turning git's refusal (e.g. no merge-base) into ``ValueError``."""
    try:
        return _git(args, repo_path)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        raise ValueError(detail[-1] if detail else f"git {args[0]} failed") from exc


def is_shallow_root(repo_path: str, rev: str) -> bool:
    """Whether *rev* is a boundary a shallow clone cut its parents from.

    Read from git's own list of grafted commits, so a genuine root commit is
    never mistaken for a cut one.
    """
    shallow = _git(["rev-parse", "--git-path", "shallow"], repo_path, check=False).strip()
    path = Path(repo_path, shallow)
    if not shallow or not path.is_file():
        return False
    sha = _git(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], repo_path, check=False)
    return bool(sha.strip()) and sha.strip() in path.read_text(encoding="utf-8").split()
