"""Bind pending edit events to the commits that added those lines.

Runs from ``repowise update`` (which the post-commit hook already starts), over
the commits since the last cursor that are newer than the oldest pending event,
capped at :data:`MAX_COMMITS`. Never a full-history walk.

The match is by content hash within a path, and every rule leans toward a miss:

* An added line matches an event only in the same file (or the file git reports
  it was renamed from), and only for events recorded before the commit.
* A line whose hash two different agents claim is ambiguous and matches neither.
* A line repeated :data:`BOILERPLATE_REPEAT` or more times among one file's
  added lines in a commit is boilerplate and cannot anchor a match.
* Matched lines bind only as a run of consecutive added lines from one agent
  holding at least :data:`MIN_RUN_SUBSTANTIVE` substantive lines
  (:func:`~repowise.core.edit_capture.hashing.is_substantive`). A one-line
  agent edit is therefore a miss: measured precision first, recall later.
* A bound hash is consumed, so the same text typed again later cannot bind twice.

A commit with no bound line stays unknown; nothing here ever says human.

Transcript backfill (:mod:`.backfill`) feeds the same matcher through
:func:`bind_commits`: ``adapter.file_edits(use, result)`` yields the edits a
recorded tool call made, and :func:`event_rows` turns them into the rows this
module matches.
"""

from __future__ import annotations

import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from ..analysis.git_cli import run_git
from .edits import CapturedCall, FileEdit
from .hashing import is_substantive, line_hash, opaque_id
from .store import (
    BINDINGS,
    EVENT_TTL_SECONDS,
    EVENTS,
    append_jsonl,
    bound_commits,
    edit_event,
    load_state,
    read_jsonl,
    save_state,
    sidecar_dir,
)

MAX_COMMITS = 50
MIN_RUN_SUBSTANTIVE = 2
BOILERPLATE_REPEAT = 3
#: An event may be stamped a little after the commit that contains it when the
#: hook and ``git commit`` race; allow for that, no more.
COMMIT_GRACE_SECONDS = 120

#: git-ai's ``agent_id.tool`` names for our agent slugs; others pass through.
_GIT_AI_TOOLS = {"claude_code": "claude"}

_WORK = "events.binding.jsonl"
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_DIFF_RE = re.compile(r"^diff --git ", re.MULTILINE)


def git_ai_tool(agent: str) -> str:
    return _GIT_AI_TOOLS.get(agent, agent)


def event_rows(
    agent: str, call: CapturedCall, *, ts: float, repo_relative: list[str] | None = None
) -> list[dict[str, Any]]:
    """Event rows for *call*'s edits, the shape :func:`bind_pending` reads.

    The live hook writes these through :func:`~.store.record_call`; a backfill
    builds them in memory from transcript edits wrapped in a
    :class:`~.edits.CapturedCall`. *repo_relative* gives each edit's
    repo-relative path when the caller already resolved it.
    """
    paths = repo_relative or [edit.path for edit in call.edits]
    return [
        edit_event(agent, call, FileEdit(path, edit.added), ts)
        for path, edit in zip(paths, call.edits, strict=True)
    ]


def _git(repo: Path, *args: str) -> str:
    # Ceiling: no timeout; a wedged git blocks this call.
    result = run_git(["-c", "core.quotepath=false", *args], repo, timeout=None)
    return result.stdout if result.returncode == 0 else ""


def commit_diff(repo: Path, sha: str) -> tuple[int, list[tuple[str, str | None, list[tuple[int, list[str]]]]]]:
    """``(commit time, [(path, renamed_from, [(first_line, added_lines)])])``."""
    out = _git(
        repo, "diff-tree", "-p", "-M", "--unified=0", "--no-color", "--no-ext-diff",
        "--root", "--format=%ct", sha,
    )
    head, _, body = out.partition("\n")
    try:
        ct = int(head.strip())
    except ValueError:
        return 0, []
    parsed = (_file_diff(chunk) for chunk in _DIFF_RE.split(body)[1:])
    return ct, [entry for entry in parsed if entry is not None]


def _file_diff(chunk: str) -> tuple[str, str | None, list[tuple[int, list[str]]]] | None:
    """``(path, renamed_from, hunks)`` of one file's diff; None without hunks."""
    header, sep, rest = chunk.partition("\n@@")
    path, old = _file_header(header)
    hunks = _added_hunks(f"@@{rest}".split("\n")) if sep else []
    if path is None or not hunks:
        return None
    return path, old, [h for h in hunks if h[1]]


def _file_header(header: str) -> tuple[str | None, str | None]:
    """``(new path, renamed_from)``; the path is None for a deleted file."""
    path: str | None = None
    old: str | None = None
    for line in header.split("\n"):
        if line.startswith("rename from "):
            old = line[len("rename from ") :]
        elif line.startswith("+++ "):
            target = line[4:].rstrip("\t")
            path = target[2:] if target.startswith("b/") else None
    return path, old


def _added_hunks(lines: list[str]) -> list[tuple[int, list[str]]]:
    """``(first new line, added lines)`` per ``@@`` hunk header of *lines*."""
    hunks: list[tuple[int, list[str]]] = []
    in_hunk = False
    for line in lines:
        if line.startswith("@@"):
            match = _HUNK_RE.match(line)
            in_hunk = match is not None
            if match:
                hunks.append((int(match.group(1)), []))
        elif in_hunk and line.startswith("+"):
            hunks[-1][1].append(line[1:])
    return hunks


def _sole_owner(rows: list[dict[str, Any]], owners: list[int]) -> tuple[str, int] | None:
    """``(agent, latest row index)`` when one agent claims a hash; None if none or several."""
    agents = {rows[i]["agent"] for i in owners}
    if len(agents) != 1:
        return None
    return next(iter(agents)), max(owners, key=lambda i: rows[i]["ts"])


def _hash_owners(
    rows: list[dict[str, Any]], candidates: list[int], ct: int, window: float | None = None
) -> dict[str, list[int]]:
    """Hash to the indexes among *candidates* of rows recorded before the commit at *ct*.

    With *window*, only rows recorded at most that many seconds before it.
    """
    earliest = ct - window if window is not None else float("-inf")
    by_hash: dict[str, list[int]] = {}
    for idx in candidates:
        row = rows[idx]
        if earliest <= row["ts"] <= ct + COMMIT_GRACE_SECONDS:
            for h in row["hashes"]:
                by_hash.setdefault(h, []).append(idx)
    return by_hash


def _match_file(
    rows: list[dict[str, Any]],
    by_hash: dict[str, list[int]],
    hunks: list[tuple[int, list[str]]],
    min_run: int = MIN_RUN_SUBSTANTIVE,
) -> list[tuple[int, int, str]]:
    """``(row index, line number, hash)`` for every line this file binds."""
    hashed = [[(n + i, text, line_hash(text)) for i, text in enumerate(added)] for n, added in hunks]
    repeats = Counter(h for hunk in hashed for _, _, h in hunk)
    # Runs of consecutive lines one agent claims; a None owner breaks a run.
    runs: list[list[tuple[int, int, str, bool]]] = []
    for hunk in hashed:
        run_agent: str | None = None
        for number, text, h in hunk:
            owner = _sole_owner(rows, by_hash.get(h, []))
            if owner is None:
                run_agent = None
                continue
            agent, latest = owner
            if agent != run_agent:
                runs.append([])
                run_agent = agent
            substantive = is_substantive(text) and repeats[h] < BOILERPLATE_REPEAT
            runs[-1].append((latest, number, h, substantive))
    return [
        (idx, n, h)
        for run in runs
        if sum(1 for *_, sub in run if sub) >= min_run
        for idx, n, h, _ in run
    ]


def _bind_commit(
    repo: Path,
    sha: str,
    rows: list[dict[str, Any]],
    by_path: dict[str, list[int]],
    window: float | None,
    min_run: int,
) -> dict[str, Any] | None:
    ct, files = commit_diff(repo, sha)
    sessions = _bound_lines(rows, by_path, ct, files, window, min_run)
    return _binding(sha, ct, sessions) if sessions else None


def bind_commits(
    repo: Path,
    rows: list[dict[str, Any]],
    commits: list[str],
    *,
    window: float | None = None,
    min_run: int = MIN_RUN_SUBSTANTIVE,
) -> list[dict[str, Any]]:
    """Bind *rows* to *commits* (oldest first), consuming the hashes they bind.

    The matcher :func:`bind_pending` runs, for a caller that holds its own rows
    and commit list (transcript backfill). *window* bounds how long before a
    commit an edit may be; *min_run* raises the substantive lines a run needs.
    """
    by_path: dict[str, list[int]] = {}
    for idx, row in enumerate(rows):
        by_path.setdefault(row["path"], []).append(idx)
    return [
        binding
        for sha in commits
        if (binding := _bind_commit(repo, sha, rows, by_path, window, min_run)) is not None
    ]


_Sessions = dict[tuple[str, str, str | None], dict[str, list[int]]]


def _bound_lines(
    rows: list[dict[str, Any]],
    by_path: dict[str, list[int]],
    ct: int,
    files: list[tuple[str, str | None, list[tuple[int, list[str]]]]],
    window: float | None,
    min_run: int,
) -> _Sessions:
    """Bound line numbers per ``(agent, session, model)`` and path; consumes matched hashes."""
    sessions: _Sessions = {}
    for path, old, hunks in files:
        candidates = by_path.get(path, []) + (by_path.get(old, []) if old else [])
        by_hash = _hash_owners(rows, candidates, ct, window)
        for idx, number, h in _match_file(rows, by_hash, hunks, min_run) if by_hash else []:
            row = rows[idx]
            if h in row["hashes"]:
                row["hashes"].remove(h)
            key = (row["agent"], row.get("session") or "", row.get("model"))
            sessions.setdefault(key, {}).setdefault(path, []).append(number)
    return sessions


def _binding(sha: str, ct: int, sessions: _Sessions) -> dict[str, Any]:
    per_agent: Counter[str] = Counter()
    for (agent, _, _), file_lines in sessions.items():
        per_agent[agent] += sum(len(v) for v in file_lines.values())
    top = per_agent.most_common()
    # A tie between agents names none (precision first, as for git-ai notes).
    agent = top[0][0] if len(top) == 1 or top[0][1] > top[1][1] else ""
    return {
        "commit": sha,
        "agent": agent,
        "lines": sum(per_agent.values()),
        "ts": ct,
        "sessions": [
            {"agent": a, "session": s, "model": m, "files": {p: sorted(n) for p, n in f.items()}}
            for (a, s, m), f in sessions.items()
        ],
    }


def _take_events(directory: Path) -> list[dict[str, Any]]:
    """Move pending events into the work file; hooks keep appending a new one.

    A work file left by a crashed run is read first, so no event is lost.
    """
    work = directory / _WORK
    rows = read_jsonl(work)
    events = directory / EVENTS
    if events.exists():
        taken = directory / f"{_WORK}.{os.getpid()}"
        try:
            os.replace(events, taken)
        except OSError:
            return rows
        rows += read_jsonl(taken)
        append_jsonl(work, read_jsonl(taken))
        taken.unlink(missing_ok=True)
    return rows


def _return_events(directory: Path, rows: list[dict[str, Any]]) -> None:
    append_jsonl(directory / EVENTS, rows)
    (directory / _WORK).unlink(missing_ok=True)


def _clean(rows: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    """Drop expired, malformed and duplicate events (two hooks, one call)."""
    seen: set[tuple] = set()
    kept = []
    for row in rows:
        if not _live(row, now):
            continue
        if row.get("call"):
            key = (row["agent"], row.get("session"), row["call"], row["path"])
            if key in seen:
                continue
            seen.add(key)
        kept.append(row)
    return kept


def _live(row: dict[str, Any], now: float) -> bool:
    """A well-formed event younger than :data:`EVENT_TTL_SECONDS`."""
    ts, hashes = row.get("ts"), row.get("hashes")
    if not isinstance(ts, (int, float)) or now - ts > EVENT_TTL_SECONDS:
        return False
    return (
        isinstance(hashes, list)
        and bool(hashes)
        and isinstance(row.get("path"), str)
        and isinstance(row.get("agent"), str)
    )


def _commits_to_bind(repo: Path, since: float, cursor: str | None) -> list[str]:
    """Commits after *cursor* and newer than *since*, oldest first.

    No ``--max-count`` here: git applies it before ``--reverse``, which would
    keep the newest commits and skip the oldest. Listing ids is cheap; the
    caller caps how many it diffs. ``--topo-order`` puts every parent before
    its child, so a cursor at the last processed commit covers all before it.
    """
    args = [
        "rev-list", "--no-merges", "--topo-order", "--reverse",
        f"--since={int(since) - COMMIT_GRACE_SECONDS}", "HEAD",
    ]
    if cursor and _git(repo, "cat-file", "-t", cursor).strip() == "commit":
        args.append(f"^{cursor}")
    return _git(repo, *args).split()


def bind_pending(repo: Path, *, capture: str, now: float | None = None) -> list[dict[str, Any]]:
    """Bind pending events to recent commits; returns the new bindings.

    *capture* is the effective level; ``notes`` also writes a git-ai note per
    bound commit and makes sure ``notes.rewriteRef`` carries it through rebase.
    """
    if capture == "off":
        return []
    directory = sidecar_dir(repo)
    if not (directory / EVENTS).exists() and not (directory / _WORK).exists():
        return []
    now = time.time() if now is None else now
    rows = _clean(_take_events(directory), now)
    try:
        bindings = _bind_rows(repo, rows)
        append_jsonl(directory / BINDINGS, bindings)
        if capture == "notes" and bindings:
            write_notes(repo, bindings)
    finally:
        _return_events(directory, [row for row in rows if row["hashes"]])
    return bindings


def _bind_rows(repo: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bind *rows* to the next batch of commits and advance the cursor past it."""
    if not rows:
        return []
    done = bound_commits(repo)
    since = min(row["ts"] for row in rows)
    pending = _commits_to_bind(repo, since, load_state(repo).get("cursor"))
    batch = pending[:MAX_COMMITS]
    bindings = bind_commits(repo, rows, [sha for sha in batch if sha not in done])
    # A capped run stops the cursor at its last commit; the next run
    # continues from there instead of skipping the rest.
    cursor = batch[-1] if len(pending) > MAX_COMMITS else _git(repo, "rev-parse", "HEAD").strip()
    if cursor:
        state = load_state(repo)
        state["cursor"] = cursor
        save_state(repo, state)
    return bindings


def write_notes(repo: Path, bindings: list[dict[str, Any]]) -> None:
    from .notes import ensure_rewrite_ref, render_note, write_note

    ensure_rewrite_ref(repo)
    for binding in bindings:
        sessions = [
            {
                "tool": git_ai_tool(entry["agent"]),
                "session": entry["session"] or opaque_id(f"{binding['commit']}:{entry['agent']}"),
                "model": entry["model"],
                "files": entry["files"],
            }
            for entry in binding["sessions"]
        ]
        write_note(repo, binding["commit"], render_note(binding["commit"], sessions))


def bind_repo(repo: Path) -> list[dict[str, Any]]:
    """:func:`bind_pending` at the repo's effective capture level."""
    from repowise.core.attribution import load_attribution_settings

    settings = load_attribution_settings(repo)
    return bind_pending(repo, capture=settings.capture) if settings.enabled else []
