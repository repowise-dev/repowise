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

Backfill seam: transcript backfill feeds the same matcher. Inside the existing
transcript pass, ``adapter.file_edits(use, result)`` yields the edits a recorded
tool call made, and :func:`event_rows` turns them into the rows this module
matches, so one read of a transcript serves both decisions and attribution.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .edits import FileEdit
from .hashing import is_substantive, line_hash, opaque_id
from .store import (
    BINDINGS,
    EVENT_TTL_SECONDS,
    EVENTS,
    append_jsonl,
    bound_commits,
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


def git_ai_tool(agent: str) -> str:
    return _GIT_AI_TOOLS.get(agent, agent)


def event_rows(
    agent: str,
    edits: list[FileEdit],
    *,
    ts: float,
    session: str = "",
    model: str | None = None,
    tool: str = "",
    repo_relative: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Event rows for *edits*, the shape :func:`bind_pending` reads.

    The live hook writes these through :func:`~.store.record_call`; a backfill
    builds them in memory from transcript edits. *repo_relative* gives each
    edit's repo-relative path when the caller already resolved it.
    """
    rows = []
    for i, edit in enumerate(edits):
        rows.append(
            {
                "v": 1,
                "ts": ts,
                "agent": agent,
                "session": opaque_id(session) if session else "",
                "model": model,
                "tool": tool,
                "call": "",
                "path": repo_relative[i] if repo_relative else edit.path,
                "hashes": [line_hash(line) for line in edit.added],
            }
        )
    return rows


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def commit_diff(repo: Path, sha: str) -> tuple[int, list[tuple[str, str | None, list[tuple[int, list[str]]]]]]:
    """``(commit time, [(path, renamed_from, [(first_line, added_lines)])])``."""
    out = _git(
        repo, "diff-tree", "-p", "-M", "--unified=0", "--no-color", "--no-ext-diff",
        "--root", "--format=%ct", sha,
    )
    lines = out.split("\n")
    try:
        ct = int(lines[0].strip())
    except (IndexError, ValueError):
        return 0, []
    files: list[tuple[str, str | None, list[tuple[int, list[str]]]]] = []
    path: str | None = None
    old: str | None = None
    hunks: list[tuple[int, list[str]]] = []
    in_hunk = False

    def flush() -> None:
        if path is not None and hunks:
            files.append((path, old, [h for h in hunks if h[1]]))

    for line in lines[1:]:
        if line.startswith("diff --git "):
            flush()
            path, old, hunks, in_hunk = None, None, [], False
        elif not in_hunk and line.startswith("rename from "):
            old = line[len("rename from ") :]
        elif not in_hunk and line.startswith("+++ "):
            target = line[4:].rstrip("\t")
            path = target[2:] if target.startswith("b/") else None
        elif line.startswith("@@"):
            match = _HUNK_RE.match(line)
            in_hunk = match is not None
            if match:
                hunks.append((int(match.group(1)), []))
        elif in_hunk and line.startswith("+") and hunks:
            hunks[-1][1].append(line[1:])
    flush()
    return ct, files


def _match_file(
    rows: list[dict[str, Any]],
    candidates: list[int],
    hunks: list[tuple[int, list[str]]],
) -> list[tuple[int, int, str]]:
    """``(row index, line number, hash)`` for every line this file binds."""
    by_hash: dict[str, list[int]] = {}
    for idx in candidates:
        for h in rows[idx]["hashes"]:
            by_hash.setdefault(h, []).append(idx)
    hashed = [[(n + i, text, line_hash(text)) for i, text in enumerate(added)] for n, added in hunks]
    repeats = Counter(h for hunk in hashed for _, _, h in hunk)
    # Runs of consecutive lines one agent claims; a None owner breaks a run.
    runs: list[list[tuple[int, int, str, bool]]] = []
    for hunk in hashed:
        run_agent: str | None = None
        for number, text, h in hunk:
            owners = by_hash.get(h, [])
            agents = {rows[i]["agent"] for i in owners}
            agent = next(iter(agents)) if len(agents) == 1 else None
            if agent is None:
                run_agent = None
                continue
            if agent != run_agent:
                runs.append([])
                run_agent = agent
            latest = max(owners, key=lambda i: rows[i]["ts"])
            substantive = is_substantive(text) and repeats[h] < BOILERPLATE_REPEAT
            runs[-1].append((latest, number, h, substantive))
    return [
        (idx, n, h)
        for run in runs
        if sum(1 for *_, sub in run if sub) >= MIN_RUN_SUBSTANTIVE
        for idx, n, h, _ in run
    ]


def _bind_commit(
    repo: Path, sha: str, rows: list[dict[str, Any]]
) -> dict[str, Any] | None:
    ct, files = commit_diff(repo, sha)
    if not files:
        return None
    sessions: dict[tuple[str, str, str | None], dict[str, list[int]]] = {}
    for path, old, hunks in files:
        names = {path, old} if old else {path}
        candidates = [
            i
            for i, row in enumerate(rows)
            if row["path"] in names and row["ts"] <= ct + COMMIT_GRACE_SECONDS and row["hashes"]
        ]
        if not candidates:
            continue
        for idx, number, h in _match_file(rows, candidates, hunks):
            row = rows[idx]
            if h in row["hashes"]:
                row["hashes"].remove(h)
            key = (row["agent"], row.get("session") or "", row.get("model"))
            sessions.setdefault(key, {}).setdefault(path, []).append(number)
    if not sessions:
        return None
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
        ts, hashes, path = row.get("ts"), row.get("hashes"), row.get("path")
        if not isinstance(ts, (int, float)) or now - ts > EVENT_TTL_SECONDS:
            continue
        if not isinstance(hashes, list) or not hashes or not isinstance(path, str):
            continue
        if not isinstance(row.get("agent"), str):
            continue
        if row.get("call"):
            key = (row["agent"], row.get("session"), row["call"], path)
            if key in seen:
                continue
            seen.add(key)
        kept.append(row)
    return kept


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
    bindings: list[dict[str, Any]] = []
    try:
        if rows:
            state = load_state(repo)
            done = bound_commits(repo)
            since = min(row["ts"] for row in rows)
            pending = _commits_to_bind(repo, since, state.get("cursor"))
            batch = pending[:MAX_COMMITS]
            for sha in batch:
                if sha in done:
                    continue
                binding = _bind_commit(repo, sha, rows)
                if binding is not None:
                    bindings.append(binding)
            # A capped run stops the cursor at its last commit; the next run
            # continues from there instead of skipping the rest.
            cursor = batch[-1] if len(pending) > MAX_COMMITS else _git(repo, "rev-parse", "HEAD").strip()
            if cursor:
                state = load_state(repo)
                state["cursor"] = cursor
                save_state(repo, state)
        append_jsonl(directory / BINDINGS, bindings)
        if capture == "notes" and bindings:
            _write_notes(repo, bindings)
    finally:
        _return_events(directory, [row for row in rows if row["hashes"]])
    return bindings


def _write_notes(repo: Path, bindings: list[dict[str, Any]]) -> None:
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
