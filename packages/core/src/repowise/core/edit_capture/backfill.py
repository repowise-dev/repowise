"""Transcript backfill: attribute past commits from agent transcripts on this machine.

Runs only from ``repowise attribution backfill``, never from ``init`` or
``update``, and only for agents the repo and the user both opted into
(``attribution.capture`` at ``local`` or ``notes``, the agent listed in
``attribution.agents``; ``DO_NOT_TRACK`` keeps it off).

One read loop, one parser, one hash: transcripts go through the cursored
:func:`~repowise.core.sessions.cursor.iter_new_events` loop and the adapter's
``normalize`` that every transcript miner uses; :class:`TranscriptEditRecorder`
observes that stream like the episode recorder does, pairs each tool call with
its result, asks ``adapter.file_edits`` (the extractor the live hook runs) what
it wrote, and keeps only line hashes through :func:`.binder.event_rows`. The
cursor lives in the attribution sidecar, so a second run reads only what the
agents appended since.

Precision first, as in the live binder, plus two backfill rules:

* An edit binds only to a commit made within :data:`WINDOW_SECONDS` after it.
* A run needs :data:`MIN_RUN_BACKFILL` substantive lines, not the live two:
  a transcript holds months of edits, so a short common run meets a commit
  by chance far more often than one hook event does.

Paths: a transcript records the absolute path the agent wrote, often in a
worktree. An edit maps to this repo when the longest suffix of its path that
some candidate commit touched sits under the main checkout or a live worktree,
or under a directory that no longer exists. A vanished directory beside a live
worktree, or inside a ``worktrees/`` directory, is taken for a removed worktree
and needs a two-segment suffix; any other vanished directory may have been any
project, so it needs three. A suffix under an existing directory that is
neither is another checkout or a sibling repo, and the edit is dropped. A path
no commit touched yet maps only under a live root with no nested checkout on
the way, so work still in progress waits for its commit.
"""

from __future__ import annotations

import os
import posixpath
import re
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .binder import (
    COMMIT_GRACE_SECONDS,
    _commits_to_bind,
    _git,
    bind_commits,
    event_rows,
    write_notes,
)
from .edits import CapturedCall
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

#: Edit-to-commit window. Measured on this repo's transcripts: 3 days binds 2%
#: fewer commits than 14; 30 days binds 1% more and 8 contributor commits (no
#: local transcripts behind them) instead of 3.
WINDOW_SECONDS = 14 * 24 * 3600

#: Substantive lines a backfilled run needs (live capture needs 2). Measured on
#: this repo (1,861 commits, 13,399 mapped edits) against a control that moves
#: every edit 16 days later, so any bind it makes is spurious: control binds
#: per real bind 11% at 2, 6% at 3, 2.6% at 4, 1.8% at 5, 1.0% at 6, 0.7% at 7.
#: At 6 it binds 583 commits (665 at 2), one of them a contributor's.
MIN_RUN_BACKFILL = 6

#: The backfill's own transcript cursors, apart from the decision miner's: that
#: pass may have read these transcripts already, and must keep its place.
CURSORS = "transcripts.json"

_DRIVE_RE = re.compile(r"^/([A-Za-z]):?(?=/)")


@dataclass
class BackfillResult:
    """What one backfill run read and bound."""

    transcripts: dict[str, int] = field(default_factory=dict)
    edits: int = 0
    edits_in_repo: int = 0
    commits: list[str] = field(default_factory=list)
    bound_before: set[str] = field(default_factory=set)
    bindings: list[dict[str, Any]] = field(default_factory=list)
    handed_over: int = 0
    #: Scanned commits with a known source before and after this run.
    covered_before: int = 0
    covered_after: int = 0


def consent(repo_root: Path, agents: Iterable[str]) -> tuple[list[str], dict[str, str]]:
    """``(allowed, refused)`` for *agents* (target ids such as ``claude-code``).

    *refused* maps an agent to the reason in plain words.
    """
    from repowise.core.agents.identity import get_identity
    from repowise.core.attribution import load_attribution_settings

    settings = load_attribution_settings(repo_root)
    allowed: list[str] = []
    refused: dict[str, str] = {}
    for name in agents:
        identity = get_identity(name.replace("-", "_"))
        agent = identity.cli_target_id if identity is not None else name
        if identity is None or identity.session_adapter is None or identity.capture_adapter is None:
            refused[agent] = "no transcript adapter for this agent"
        elif not settings.enabled:
            refused[agent] = "attribution is switched off"
        elif settings.do_not_track:
            refused[agent] = "DO_NOT_TRACK is set"
        elif settings.capture == "off":
            refused[agent] = "attribution.capture is off (set local or notes in both configs)"
        elif agent not in settings.agents:
            refused[agent] = "not listed in attribution.agents (repo and user config)"
        else:
            allowed.append(agent)
    return allowed, refused


class TranscriptEditRecorder:
    """Folds the edits a transcript stream records into hash rows, passing events through.

    Paths stay as the transcript wrote them (with the call's cwd) until
    :meth:`rows` maps them, because the map needs the commit window, which is
    known only once every transcript has been read.
    """

    def __init__(self, keep: Callable[[str, str], Any] | None = None) -> None:
        #: Drops at record time an edit no path of this repo could match.
        self._keep = keep
        self._rows: list[tuple[dict[str, Any], str]] = []
        self._seen: set[tuple[str, str, str]] = set()
        self._pending: dict[str, tuple[Any, Any]] = {}
        self.edits = 0

    def prefilter(self, adapter: Any) -> Callable[[str], bool]:
        """The adapter's edit-call gate, plus result lines for calls awaiting one."""
        from repowise.core.sessions.adapters.base import INTENT_EDIT_CALLS, INTENT_TOOL_CALLS

        gate = adapter.prefilter(INTENT_EDIT_CALLS)
        tool_lines = adapter.prefilter(INTENT_TOOL_CALLS)
        pending = self._pending

        def admit(raw: str) -> bool:
            if gate is None or gate(raw):
                return True
            if not pending or (tool_lines is not None and not tool_lines(raw)):
                return False
            return any(use_id in raw for use_id in pending)

        return admit

    def observe(self, adapter: Any, agent: str, events: Iterable[Any]) -> Iterator[Any]:
        """Yield *events* through untouched, recording the edits among them."""
        self._pending.clear()  # a call never pairs across transcripts
        model: str | None = None
        for event in events:
            yield event
            model = event.model or model
            for use in event.tool_uses:
                if use.name in adapter.edit_tool_names:
                    self._pending[use.id] = (use, event)
            for result in event.tool_results:
                found = self._pending.pop(result.tool_use_id, None)
                if found is not None:
                    self._record(adapter, agent, found, result, event, model)

    def _record(
        self, adapter: Any, agent: str, found: tuple[Any, Any], result: Any, event: Any, model: str | None
    ) -> None:
        use, use_event = found
        edits = adapter.file_edits(use, result)
        ts = event.ts or use_event.ts
        if not edits or ts is None:
            return
        self.edits += len(edits)
        call = CapturedCall(
            tool=use.name,
            edits=edits,
            session_id=use_event.session_id or "",
            model=use_event.model or model,
            cwd=use_event.cwd or "",
            call_id=use.id,
        )
        for row in event_rows(agent, call, ts=ts):
            key = (agent, row["call"], row["path"])
            if self._keep is not None and self._keep(row["path"], call.cwd) is None:
                continue
            if key not in self._seen:
                self._seen.add(key)
                self._rows.append((row, call.cwd))

    def rows(self, resolve: Any) -> list[dict[str, Any]]:
        """Rows whose path maps into the repo, the path made repo-relative."""
        out: list[dict[str, Any]] = []
        for row, cwd in self._rows:
            rel = resolve(row["path"], cwd)
            if rel is not None:
                out.append({**row, "path": rel})
        return out

    @property
    def oldest(self) -> float | None:
        return min((row["ts"] for row, _ in self._rows), default=None)


def _absolute(path: str, cwd: str) -> str | None:
    """*path* as an absolute POSIX-style path, resolved against *cwd*; None if neither says."""
    path = path.replace("\\", "/")
    if path.startswith("file://"):
        path = path[len("file://") :]
    if os.name == "nt":
        path = _DRIVE_RE.sub(r"\1:", path)  # /c/x and /C:/x are C:/x
    if not (path.startswith("/") or path[1:2] == ":"):
        if not cwd:
            return None
        base = _absolute(cwd, "")
        if base is None:
            return None
        path = f"{base}/{path}"
    return posixpath.normpath(path)


class PathMap:
    """Maps a transcript path to this repo's path; see the module docstring."""

    def __init__(self, main: str, worktrees: Iterable[str], known: Iterable[str]) -> None:
        self._fold = str.lower if os.name == "nt" else str
        trees = [self._root(root) for root in worktrees]
        self._live = {self._root(main), *trees}
        # Where this repo's worktrees live: a vanished sibling was most likely one.
        self._tree_parents = {posixpath.dirname(root) for root in trees}
        self._known = {self._fold(path): path for path in known}
        self._exists: dict[str, bool] = {}

    def _root(self, root: str) -> str:
        return self._fold(root.replace("\\", "/").rstrip("/"))

    def __call__(self, path: str, cwd: str) -> str | None:
        absolute = _absolute(path, cwd)
        if absolute is None:
            return None
        segments = absolute.split("/")
        # From 2: never take a drive or the filesystem root for a checkout.
        for i in range(2, len(segments)):
            rel = self._known.get(self._fold("/".join(segments[i:])))
            if rel is None:
                continue
            root = "/".join(segments[:i])
            if self._fold(root) in self._live:
                return rel
            if not self._exists_on_disk(root) and len(segments) - i >= self._suffix_needed(root):
                return rel
            return None
        return self._uncommitted(segments)

    def _suffix_needed(self, root: str) -> int:
        parent = self._fold(posixpath.dirname(root))
        return 2 if parent in self._tree_parents or posixpath.basename(parent) == "worktrees" else 3

    def _uncommitted(self, segments: list[str]) -> str | None:
        """A path no candidate commit touched yet, under a live root, with no
        nested checkout (its own ``.git``) on the way to it."""
        for i in range(len(segments) - 1, 1, -1):
            if self._fold("/".join(segments[:i])) not in self._live:
                continue
            directory = "/".join(segments[:i])
            for part in segments[i:-1]:
                directory = f"{directory}/{part}"
                if not self._exists_on_disk(directory) or self._exists_on_disk(f"{directory}/.git"):
                    return None
            return "/".join(segments[i:])
        return None

    def _exists_on_disk(self, path: str) -> bool:
        if path not in self._exists:
            self._exists[path] = os.path.exists(path)
        return self._exists[path]


def worktree_roots(repo: Path) -> list[str]:
    """The main checkout and every live worktree of *repo*."""
    out = _git(repo, "worktree", "list", "--porcelain")
    roots = [line[len("worktree ") :] for line in out.splitlines() if line.startswith("worktree ")]
    return roots or [str(repo)]


def _paths_since(repo: Path, since: float | None) -> set[str]:
    """Paths commits since *since* touched; every path ever with None."""
    window = [] if since is None else [f"--since={int(since) - COMMIT_GRACE_SECONDS}"]
    out = _git(repo, "log", "--no-merges", *window, "--format=", "--name-only", "HEAD")
    return {line for line in out.splitlines() if line}


def read_transcripts(
    agents: list[str],
    recorder: TranscriptEditRecorder,
    cursors: Any,
    *,
    projects_roots: dict[str, Path] | None = None,
) -> dict[str, int]:
    """Feed every transcript of *agents* through *recorder*; returns files read per agent.

    *cursors* advance in memory; the caller saves them once the edits are bound.
    """
    from repowise.core.agents.identity import get_identity
    from repowise.core.sessions import get_adapter
    from repowise.core.sessions.cursor import iter_new_events

    counts: dict[str, int] = {}
    for agent in agents:
        identity = get_identity(agent.replace("-", "_"))
        if identity is None or identity.session_adapter is None:
            continue
        adapter = get_adapter(identity.session_adapter)
        root = (projects_roots or {}).get(agent)
        prefilter = recorder.prefilter(adapter)
        counts[agent] = 0
        for path in adapter.discover_all(projects_root=root):
            try:
                events = iter_new_events(adapter, path, cursors, prefilter=prefilter)
                for _ in recorder.observe(adapter, identity.slug, events):
                    pass
            except OSError:
                continue
            counts[agent] += 1
    return counts


def backfill(
    repo: Path,
    agents: list[str],
    *,
    capture: str,
    projects_roots: dict[str, Path] | None = None,
    now: float | None = None,
    min_run: int = MIN_RUN_BACKFILL,
    window: float = WINDOW_SECONDS,
    known: set[str] | None = None,
) -> BackfillResult:
    """Read *agents*' transcripts and bind their edits to this repo's commits.

    *agents* must already have passed :func:`consent`; *capture* is the
    effective level (``notes`` also writes a git-ai note per bound commit).
    *known* is the commits the index already labels agent or human-attested,
    for the coverage figures.
    Bindings land in the sidecar beside the live ones. Unbound edits younger
    than the pending-event TTL join the pending events, so a commit made after
    this run still finds them.
    """
    from repowise.core.sessions.cursor import CursorStore

    now = time.time() if now is None else now
    roots = worktree_roots(repo)
    # Ceiling: one string per path the history ever held, so memory grows with
    # the repo's history, not with the transcripts; edits elsewhere are dropped
    # as they are read.
    recorder = TranscriptEditRecorder(keep=PathMap(roots[0], roots[1:], _paths_since(repo, None)))
    cursors = CursorStore(sidecar_dir(repo) / CURSORS)
    result = BackfillResult(
        transcripts=read_transcripts(agents, recorder, cursors, projects_roots=projects_roots)
    )
    result.edits = recorder.edits
    oldest = recorder.oldest
    if oldest is not None:
        result.commits = _commits_to_bind(repo, oldest, None)
        path_map = PathMap(roots[0], roots[1:], _paths_since(repo, oldest))
        rows = recorder.rows(path_map)
        result.edits_in_repo = len(rows)
        done = bound_commits(repo)
        result.bound_before = {sha for sha in result.commits if sha in done}
        todo = [sha for sha in result.commits if sha not in done]
        result.bindings = bind_commits(repo, rows, todo, window=window, min_run=min_run)
        covered = {sha for sha in result.commits if sha in (known or set())} | result.bound_before
        result.covered_before = len(covered)
        result.covered_after = len(covered | {b["commit"] for b in result.bindings})
        _persist(repo, result, rows, capture=capture, now=now, window=window)
    cursors.save()
    return result


def _persist(
    repo: Path,
    result: BackfillResult,
    rows: list[dict[str, Any]],
    *,
    capture: str,
    now: float,
    window: float,
) -> None:
    directory = sidecar_dir(repo)
    live_pending = bool(read_jsonl(directory / EVENTS))
    append_jsonl(directory / BINDINGS, result.bindings)
    if capture == "notes" and result.bindings:
        write_notes(repo, result.bindings)
    left = [row for row in rows if row["hashes"] and now - row["ts"] <= EVENT_TTL_SECONDS]
    append_jsonl(directory / EVENTS, left)
    result.handed_over = len(left)
    state = load_state(repo)
    # With no live events waiting, every commit up to HEAD has been tried: the
    # live binder may start after it, so an edit this run left unbound never
    # meets an older commit under the live rule. Live events waiting for those
    # commits keep the cursor where it is.
    head = _git(repo, "rev-parse", "HEAD").strip()
    if head and not state.get("cursor") and not live_pending:
        state["cursor"] = head
    state["backfill"] = {
        "at": now,
        "commits": len(result.commits),
        "bound": len(result.bindings),
        "bound_before": len(result.bound_before),
        "covered_before": result.covered_before,
        "covered_after": result.covered_after,
        "window_days": round(window / 86400),
    }
    save_state(repo, state)
