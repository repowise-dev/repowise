"""Map a git change to the source lines it touches, per file.

``repowise risk`` reads a change as aggregate counts (``--numstat``); the
test-impact query needs the actual changed *line numbers* so it can intersect
them with per-test coverage. This module parses a ``--unified=0`` diff into
``{file: {line, ...}}`` over the *new* side of the change - the lines that
exist in the head/index/working tree, which is the space coverage is keyed in.

Pure ``git`` subprocess walking, reusing the wrapper from the change-risk
feature extractor (no new dependency, deterministic).
"""

from __future__ import annotations

import re

from .change_risk.features import _git

# New-side hunk header: ``@@ -a,b +c,d @@`` -> we only care about ``+c,d``
# (the lines present after the change). ``d`` defaults to 1 when omitted.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _parse_unified_diff(diff: str) -> dict[str, set[int]]:
    """Parse ``git diff --unified=0`` into new-side changed lines per file."""
    result: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            # git quotes paths with special chars ("b/pa\tth"); strip the quotes
            # so the common (unquoted) key still resolves. Rare enough to accept
            # the imperfect unescaping.
            if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
                path = path[1:-1]
            if path == "/dev/null":  # file deleted - nothing on the new side
                current = None
                continue
            if path.startswith("b/"):
                path = path[2:]
            current = path
            result.setdefault(current, set())
        elif current is not None and line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if not m:
                continue
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            result[current].update(range(start, start + count))
    # Drop files whose only change was a deletion (no new-side lines): they
    # cannot intersect coverage, and would otherwise read as "touched".
    return {path: lines for path, lines in result.items() if lines}


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
