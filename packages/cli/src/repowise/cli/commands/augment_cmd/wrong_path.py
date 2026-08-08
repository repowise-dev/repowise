"""Rescue a tool call that failed on a path this tree does not have.

The agent guesses a plausible directory for a file it knows exists — the
package layout moved, or it inferred ``core/git_indexer/`` from a symbol name
when the file lives under ``core/ingestion/git_indexer/`` — and gets back
"Path does not exist". The index knows where that basename actually lives, so
the failure is answerable at the moment it happens.

Measured over 435 transcripts of this repo: 86 path-not-found failures on the
file tools, of which this predicate speaks to 18, across 16 sessions. The gap
is deliberate and every step of it is a rule below. Precision is the whole
product here — a confident wrong path costs more than silence, because the
agent has no cheap way to tell a rescue from a fact.

Everything runs on stdlib ``sqlite3`` through :mod:`fast_lookup`; no retrieval,
no ORM. The one query is a basename scan of the repo's file nodes.
"""

from __future__ import annotations

import re
from pathlib import Path

from ._shared import HookResult, _find_repo_root

#: Tools whose failure can name a path we could rescue. Bash and PowerShell
#: fail on paths too ("cd: no such file"), but their input is a command line
#: rather than a path argument, so extracting the intended path is a different
#: problem with a different error surface. Out of scope on purpose.
_PATH_TOOLS = frozenset({"Read", "Edit", "Write", "Grep", "Glob", "NotebookEdit"})

#: The two verbatim shapes a missing path takes, taken from the corpus rather
#: than from the docs. Grep/Glob name the path in the error; Read does not, and
#: its path has to come from ``tool_input``.
_PATH_MISSING = re.compile(
    r"<tool_use_error>Path does not exist: (?P<path>.+?)\. Note: your current"
)
_FILE_MISSING = re.compile(r"(?:^|\n)File does not exist\.")

#: Claude Code prints its own suggestion for some of these (10 of the 86). It
#: has already been said and saying it again is worse than silence.
_ALREADY_SUGGESTED = re.compile(r"Did you mean ", re.IGNORECASE)


def _failed_path(tool_input: dict, error_text: str) -> str:
    """The path the tool tried and did not find, or "" if this is not that."""
    m = _PATH_MISSING.search(error_text)
    if m:
        return m.group("path").strip()
    if _FILE_MISSING.search(error_text):
        for field in ("file_path", "path", "notebook_path"):
            value = tool_input.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _is_several_paths(attempted: str) -> bool:
    """True when one string carries more than one path.

    A search tool can be handed several paths at once and report them as one
    string, and the basename of whichever landed last is not what was asked
    for. Tested by counting whitespace-separated tokens that end in a file
    extension rather than by rejecting whitespace outright: a checkout under
    ``C:\\Users\\Foo Bar`` or ``OneDrive - Company`` puts a space in every
    absolute path, and rejecting those takes the surface's recall to zero for
    everyone who has one.
    """
    tokens = attempted.split()
    return sum(1 for token in tokens if Path(token).suffix) > 1


def _repo_relative(repo_path: Path, attempted: str, cwd: Path) -> str | None:
    """*attempted* as a repo-relative POSIX path, or None if it is elsewhere.

    A sibling worktree or another checkout carries its own index, and this one
    has no standing to answer for it: 10 of the 86 failures name one, and the
    file they want is the *other* tree's copy.

    A relative path is joined to *cwd* and put through the same containment
    check rather than taken at face value. The agent's cwd is not always the
    repository root, so face value gets both halves wrong: ``../sibling/x.py``
    would read as an in-repo path and be answered with this tree's copy, and a
    cwd-relative path would be echoed back as though it were repo-relative.
    """
    path = Path(attempted)
    if not path.is_absolute():
        path = cwd / path
    try:
        return path.resolve().relative_to(repo_path.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _resolve(repo_path: Path, relative: str) -> str | None:
    """The one indexed file with this basename, or None when that is not one.

    None covers every reason to stay quiet, and they are not the same reason:
    no index, no match, several matches, or a match that is no longer on disk.
    The ambiguous case is silence by decision — naming one of twelve
    ``registry.py`` confidently is the failure this surface exists to avoid.
    """
    from . import fast_lookup

    basename = relative.rsplit("/", 1)[-1]
    conn = fast_lookup.connect(repo_path)
    if conn is None:
        return None
    try:
        repo_id = fast_lookup.repo_id(conn, repo_path)
        if repo_id is None:
            return None
        matches = fast_lookup.files_by_basename(conn, repo_id, basename)
    except Exception:
        return None
    finally:
        conn.close()

    # ``node_type = 'file'`` also covers resolved external packages, spelled
    # ``external:pkg/mod``. They are not paths in this tree, and the on-disk
    # test below is what rules them out along with rows whose file has since
    # been deleted. Both filters run before the uniqueness test, so a stale row
    # cannot hide the single live answer behind an apparent tie.
    live = [m for m in matches if (repo_path / m).exists()]
    if len(live) != 1:
        return None
    resolved = live[0]
    if resolved.lower() == relative.lower():
        # The answer is the path that just failed. Reachable whenever the file
        # arrived between the failure and this hook, or the tool failed for a
        # reason other than the path being absent. "Did you mean the thing you
        # just tried" is the worst thing this surface could say, and 3 of the
        # 86 measured failures would have hit it.
        return None
    return resolved


def _error_text(error: object) -> str:
    """The failure as text. ``error`` is a string today; this survives it not.

    Claude Code passes the rejection through verbatim, so the shape is the
    tool's rather than the harness's. A dict is unwrapped by the keys the
    tool-result blocks use; anything else is not something to guess at.
    """
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        for field in ("message", "text", "content", "error"):
            value = error.get(field)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _handle_tool_failure(
    tool_name: str,
    tool_input: dict,
    error: object,
    cwd: str,
    *,
    session_id: str = "",
) -> HookResult:
    """Point a path-not-found failure at the file the index says it meant."""
    if tool_name not in _PATH_TOOLS or not isinstance(tool_input, dict):
        return HookResult()
    error_text = _error_text(error)
    if not error_text or _ALREADY_SUGGESTED.search(error_text):
        return HookResult()

    attempted = _failed_path(tool_input, error_text)
    if not attempted or _is_several_paths(attempted):
        return HookResult()

    repo_path = _find_repo_root(Path(cwd)) if cwd else None
    if repo_path is None:
        return HookResult()

    relative = _repo_relative(repo_path, attempted, Path(cwd))
    # A path with no extension is a directory, and "which file did you mean" is
    # not the question it asks. 20 of the 86, left for a surface that can
    # answer them properly.
    if not relative or not Path(relative).suffix:
        return HookResult()

    resolved = _resolve(repo_path, relative)
    if resolved is None:
        return HookResult()

    text = (
        f"[repowise] {relative} is not in this tree. "
        f"The only indexed {relative.rsplit('/', 1)[-1]} is {resolved}"
    )
    _log_wrong_path_firing(repo_path, session_id, text)
    return HookResult(context=text)


def _log_wrong_path_firing(repo_path: Path, session_id: str, text: str) -> None:
    """Record the firing in the shared ledger; measurement only, never fatal.

    Same contract as the search surfaces: keyed on a hash of the emitted text
    so the transcript classifier can recompute the id from what the agent saw,
    and so one repeated rescue in a session logs once.
    """
    if not session_id:
        return
    try:
        from ._shared import _ledger_key
        from .ledger import _claim_ledger

        _claim_ledger(
            repo_path,
            session_id,
            _ledger_key("wrong_path", "rescue", text),
            node_id="",
            surface="wrong_path",
            category="rescue",
            chars=len(text),
        )
    except Exception:
        return
