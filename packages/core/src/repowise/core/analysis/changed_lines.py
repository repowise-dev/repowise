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
    # File-level change shape. ``new_file`` / ``deleted`` come from the
    # ``new file mode`` / ``deleted file mode`` markers; ``old_mode`` /
    # ``mode`` from an ``old mode`` / ``new mode`` pair (a chmod with no
    # content change). ``binary`` is set when git reports ``Binary files …``,
    # which carries no hunks, so the coverage-intersection view still sees no
    # new-side lines for it — but the fix-shape classifier can now tell an
    # asset flip from a code edit instead of silently dropping it.
    new_file: bool = False
    deleted: bool = False
    binary: bool = False
    mode: str | None = None
    old_mode: str | None = None


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


def _git_diff_path(raw: str) -> tuple[str | None, str | None]:
    """Best-effort ``(old, new)`` paths from a ``diff --git a/X b/Y`` line.

    Used to open the per-file record *before* the ``---``/``+++`` header, so
    the ``new file mode`` / ``deleted file mode`` / ``old mode`` / ``new mode``
    / ``Binary files`` markers (which git prints between the ``diff --git``
    line and that header) have a record to attach to. Quoted tokens (paths
    with spaces) are handled; an unparseable line yields ``(None, None)`` and
    the header is left to open the record as before.
    """
    rest = raw[len("diff --git ") :]
    if rest.startswith('"'):
        old = new = None
        for token in rest.split('" '):
            token = token.strip().strip('"')
            if token.startswith("a/"):
                old = token[2:]
            elif token.startswith("b/"):
                new = token[2:]
        return old, new
    idx = rest.rfind(" b/")
    if idx == -1:
        return None, None
    new = rest[idx + 3 :]
    old = rest[:idx]
    old = old[2:] if old.startswith("a/") else old
    return (old or None), (new or None)


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
        if line.startswith("diff --git "):
            # Open the record up front so the file-level markers below (which
            # git prints before the ``---``/``+++`` header) have something to
            # attach to. The header re-records the same path, so this is
            # idempotent and never loses data.
            _old, _new = _git_diff_path(line)
            path = _new or _old
            current = _record(path) if path else None
            i += 1
            continue
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            # A deletion has no new-side path; key it by the old one so the
            # file still shows up for shape classification.
            path = _header_path(lines[i + 1][4:]) or _header_path(line[4:])
            current = _record(path) if path else None
            i += 2
            continue
        # File-level markers sit between the header and the hunks, in either
        # order, and never start with ``@@`` / ``-`` / ``+``, so they cannot
        # be mistaken for a hunk or a content line.
        if current is not None and line.startswith("new file mode "):
            current.new_file = True
            current.mode = line[len("new file mode ") :].strip() or None
            i += 1
            continue
        if current is not None and line.startswith("deleted file mode "):
            current.deleted = True
            current.old_mode = line[len("deleted file mode ") :].strip() or None
            i += 1
            continue
        if current is not None and line.startswith("old mode "):
            current.old_mode = line[len("old mode ") :].strip() or None
            i += 1
            continue
        if current is not None and line.startswith("new mode "):
            current.mode = line[len("new mode ") :].strip() or None
            i += 1
            continue
        if current is not None and line.startswith("Binary files "):
            current.binary = True
            i += 1
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

    *revspec* mirrors ``repowise risk``: ``base..head`` is a range, a bare ref
    is a single commit. With no *revspec* (or *staged*), the staged diff
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
        diff = _git(["diff", "--unified=0", "HEAD"], repo_path)
        return _parse_unified_diff(diff), "working tree"

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
    # -m --first-parent matches what change risk counts on a merge; without it
    # git's combined diff emits nothing at all and a merged PR reads as empty.
    diff = _git(["show", "--unified=0", "--format=", "-m", "--first-parent", revspec], repo_path)
    return _parse_unified_diff(diff), revspec
