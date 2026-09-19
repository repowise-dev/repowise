"""Savings an agent was shown but the ledger never recorded.

Repowise writes its own before and after into the text the agent receives: a
distilled command output ends in an omission marker carrying the token count
that was dropped. So the delivered text is measurable, the omitted count is
readable, and their sum is what the output cost before distillation. That is a
*measured* saving recovered from the transcript, not an estimate of one.

This exists because the ledger only ever recorded what happened while it was
running. A repository indexed today has months of agent history on disk and an
empty savings page; a machine that upgraded Repowise has events only from its
next call onward. Both are catch-up, which is why this is a cursored surface
rather than a migration: it runs at ``init``, again on demand, and reads only
what has been appended since it last looked.

Three rules it does not get to relax:

- **Shell results only.** A marker in prose, a pull-request body or a test
  fixture is not a saving. A marker in an MCP response is one the MCP surface
  accounts for itself, and re-deriving it from a post-budget transcript reads
  fields the budgeter sheds first.
- **One event per omission ref.** The ref is content-addressed, so the same
  ref twice is the same distilled output. Counting it once undersells a
  genuine repeat, which is the floor this ledger is meant to be.
- **Never repriced.** These happened at rates nobody recorded, so they are
  counted in tokens and left unvalued rather than valued at today's rate.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repowise.core.distill.budget import estimate_tokens
from repowise.core.distill.engine import HOST_OUTPUT_CAP_CHARS
from repowise.core.distill.markers import ParsedMarker, parse_markers
from repowise.core.distill.store import OmissionStore, omission_sources
from repowise.core.savings.correlation import new_event_id, scoped_idempotency_key
from repowise.core.sessions import (
    INTENT_TOOL_CALLS,
    Event,
    ToolResult,
    get_adapter,
    registered_adapters,
)
from repowise.core.sessions.adapters.base import HarnessAdapter, RawPrefilter
from repowise.core.sessions.cursor import CursorStore, iter_new_events

logger = logging.getLogger(__name__)

__all__ = ["ESTIMATOR", "SYNC_BUDGET_S", "TranscriptSyncResult", "sync_transcript_savings"]

#: Names this population in ``savings_events.estimator``. Live distill writes
#: ``chars_per_token_floor_v1``, so a report can always separate the two --
#: which matters because only two of the six agents keep transcripts, and a
#: backfilled window's per-agent split is therefore skewed by method.
ESTIMATOR = "transcript_marker_v1"

#: Seconds of transcript reading per run, split across harnesses. Stopping is
#: safe rather than lossy: cursors are per file and saved after the loop, so
#: the next run resumes where this one stopped.
SYNC_BUDGET_S = 20.0

#: The host truncation the live path applies, in tokens. ``estimate_tokens``
#: is ``len // 4``, so capping characters and capping tokens agree exactly.
HOST_OUTPUT_CAP_TOKENS = HOST_OUTPUT_CAP_CHARS // 4

#: Cursors for this surface alone. Sharing the decision miner's would starve
#: whichever ran second, silently: that cursor advances as bytes are read.
_CURSOR_FILENAME = "transcript-cursors.json"

_SIDECAR_DIR = (".repowise", "omissions")
_SIDECAR_DB = "omissions.db"


@dataclass(slots=True)
class TranscriptSyncResult:
    """What one sync pass read, recorded and left for next time."""

    recorded: int = 0
    saved_input_tokens: int = 0
    #: Markers whose ref an event already claims.
    already_recorded: int = 0
    transcripts_read: int = 0
    #: Transcripts the time budget did not reach. Non-zero means run again.
    deferred: int = 0
    per_harness: dict[str, int] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.deferred == 0


@dataclass(slots=True)
class _Candidate:
    """One marker found in a shell result, before the ledger is consulted."""

    marker: ParsedMarker
    harness: str
    occurred_at: datetime
    delivered_tokens: int
    session_id: str | None


def sync_transcript_savings(
    repo_root: Path,
    *,
    harnesses: Sequence[str] | None = None,
    projects_root: Path | None = None,
    budget: float = SYNC_BUDGET_S,
    dry_run: bool = False,
    now: float | None = None,
) -> TranscriptSyncResult:
    """Record savings this repository's agents were shown but never banked.

    Creates the savings sidecar if it does not exist. A deliberate departure
    from the recorder, which never creates one because a hook or an MCP call
    is not the place to decide a repository has opted in. Being called *is*
    that decision here: this runs from ``init`` and from an explicit command.

    With *dry_run* nothing is written and no sidecar is created; the result
    reports what a real run would record, and the cursors do not advance.
    """
    repo_root = Path(repo_root).resolve()
    names = tuple(harnesses) if harnesses else tuple(registered_adapters())
    result = TranscriptSyncResult()
    if not names:
        return result

    cursors = CursorStore(repo_root.joinpath(*_SIDECAR_DIR, _CURSOR_FILENAME))
    candidates: dict[str, _Candidate] = {}
    per_harness_budget = budget / len(names)
    for name in names:
        try:
            _sweep(
                name,
                repo_root=repo_root,
                projects_root=projects_root,
                cursors=cursors,
                candidates=candidates,
                budget=per_harness_budget,
                result=result,
            )
        except Exception as exc:
            # One harness must not cost another's progress: the cursor save
            # below is shared, so an escape here would discard every advance.
            logger.warning("savings transcript sweep failed: %s: %s", name, exc)

    if dry_run:
        _apply(repo_root, candidates, result, store=None)
        return result

    with closing(_open_store(repo_root)) as store:
        _apply(repo_root, candidates, result, store=store)
    # Saved after the writes, so a crash mid-record re-reads rather than loses.
    cursors.save()
    return result


def _open_store(repo_root: Path) -> OmissionStore:
    return OmissionStore(repo_root.joinpath(*_SIDECAR_DIR, _SIDECAR_DB))


def _sweep(
    harness: str,
    *,
    repo_root: Path,
    projects_root: Path | None,
    cursors: CursorStore,
    candidates: dict[str, _Candidate],
    budget: float,
    result: TranscriptSyncResult,
) -> None:
    adapter = get_adapter(harness)
    shell_tools = adapter.shell_tool_names
    if not shell_tools:
        return
    deadline = time.monotonic() + budget
    discovered = adapter.discover(repo_root, projects_root=projects_root)
    for index, path in enumerate(discovered):
        if time.monotonic() > deadline:
            result.deferred += len(discovered) - index
            return
        try:
            events = iter_new_events(adapter, path, cursors, prefilter=_gate(adapter))
            _collect(events, harness, repo_root, shell_tools, candidates)
            result.transcripts_read += 1
        except OSError:
            continue


def _gate(adapter: HarnessAdapter) -> RawPrefilter | None:
    """The adapter's tool-call gate, widened to keep every marker line.

    The tool call and the result carrying its marker are separate lines, and
    only the pair says a marker came from a shell command, so a marker-only
    gate sees results it can no longer attribute. Skipping a line is sound
    either way: the cursor advances per line read, not per event yielded.
    """
    tool_gate = adapter.prefilter(INTENT_TOOL_CALLS)
    if tool_gate is None:
        return None

    def gate(raw_line: str) -> bool:
        return tool_gate(raw_line) or "repowise#" in raw_line

    return gate


def _collect(
    events: Iterable[Event],
    harness: str,
    repo_root: Path,
    shell_tools: frozenset[str],
    candidates: dict[str, _Candidate],
) -> None:
    shell_calls: set[str] = set()
    for event in events:
        for use in event.tool_uses:
            if use.name in shell_tools:
                shell_calls.add(use.id)
        if not event.tool_results or not _in_repo(event, repo_root):
            continue
        for block in event.tool_results:
            if block.tool_use_id not in shell_calls:
                continue
            for text in _result_texts(block):
                for marker in parse_markers(text):
                    candidates.setdefault(
                        marker.ref,
                        _Candidate(
                            marker=marker,
                            harness=harness,
                            occurred_at=_occurred_at(event),
                            delivered_tokens=estimate_tokens(text),
                            session_id=event.session_id,
                        ),
                    )


def _in_repo(event: Event, repo_root: Path) -> bool:
    """True when the event is this repository's, or says nothing either way.

    One harness files transcripts per project and states no ``cwd`` on most
    lines; another files them by date and threads the ``cwd`` through. An
    absent ``cwd`` is "no opinion", which the first harness's discovery has
    already answered.
    """
    if not event.cwd:
        return True
    try:
        cwd = Path(event.cwd).resolve()
    except OSError:
        return False
    return cwd == repo_root or repo_root in cwd.parents


def _occurred_at(event: Event) -> datetime:
    return datetime.fromtimestamp(event.ts if event.ts is not None else time.time(), UTC)


def _result_texts(block: ToolResult) -> Iterable[str]:
    """Every string a tool result carries, across the shapes harnesses use."""
    for blob in (block.content, block.payload):
        if isinstance(blob, str):
            yield blob
        elif isinstance(blob, list):
            for item in blob:
                if isinstance(item, str):
                    yield item
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    yield item["text"]
        elif isinstance(blob, dict):
            for value in blob.values():
                if isinstance(value, str):
                    yield value


def _accounting(candidate: _Candidate) -> tuple[int, int]:
    """``(baseline, delivered)`` input tokens for one recovered marker.

    ``delivered`` is measured from the text the agent actually received.
    ``baseline`` adds back what the marker says was dropped, minus the
    marker's own cost, then re-applies the host truncation the live path
    applies -- bytes past it never reached the model and cannot be claimed.
    """
    delivered = candidate.delivered_tokens
    kept = max(delivered - estimate_tokens(candidate.marker.text), 0)
    stored = kept + candidate.marker.tokens_omitted
    return min(stored, HOST_OUTPUT_CAP_TOKENS), delivered


def _payload(
    repo_root: Path, candidate: _Candidate, operation: str, surface: str
) -> dict[str, Any]:
    baseline, delivered = _accounting(candidate)
    return {
        "event_id": new_event_id(),
        # Scoped on the ref, so a re-read is a retry rather than a second
        # saving even if the ref anti-join is ever bypassed.
        "idempotency_key": scoped_idempotency_key(
            str(repo_root), surface, ESTIMATOR, candidate.marker.ref
        ),
        "occurred_at": candidate.occurred_at,
        "surface": surface,
        # The live path writes unknown here because it cannot see who invoked
        # it. A transcript names the harness it came from.
        "integration": candidate.harness,
        "agent": candidate.harness,
        "operation": operation,
        "evidence_kind": "measured",
        "estimator": ESTIMATOR,
        "token_unit": "estimated_tokens",
        "result_state": "success",
        "is_usable": True,
        "baseline_input_tokens": baseline,
        "pre_budget_input_tokens": baseline,
        "delivered_input_tokens": delivered,
        "omission_refs": (candidate.marker.ref,),
        "session_id": candidate.session_id,
    }


def _apply(
    repo_root: Path,
    candidates: dict[str, _Candidate],
    result: TranscriptSyncResult,
    *,
    store: OmissionStore | None,
) -> None:
    """Consult the ledger for every candidate, and record the ones it lacks.

    *store* is None for a dry run, which reads the sidecar if one exists and
    writes nothing either way.
    """
    if not candidates:
        return
    refs = list(candidates)
    if store is not None:
        known = store.savings().recorded_omission_refs(refs)
        origins = store.omission_sources(refs)
    else:
        known, origins = _read_only_lookup(repo_root, refs)

    from repowise.core.savings import recorder

    for ref, candidate in candidates.items():
        if ref in known:
            result.already_recorded += 1
            continue
        operation, surface = _origin(origins.get(ref))
        payload = _payload(repo_root, candidate, operation, surface)
        if store is not None and not recorder.record_event_in(store, repo_root, payload):
            continue
        saved = max(payload["baseline_input_tokens"] - payload["delivered_input_tokens"], 0)
        result.recorded += 1
        result.saved_input_tokens += saved
        result.per_harness[candidate.harness] = (
            result.per_harness.get(candidate.harness, 0) + saved
        )


def _origin(source: str | None) -> tuple[str, str]:
    """``omissions.source`` as ``(operation, surface)``.

    It reads ``"<origin>:<filter>"`` -- ``cli:git_diff``,
    ``hook-codex:test_output`` -- and is the only record of which filter ran.
    It is TTL-pruned, so most refs a transcript reaches are past it and record
    the filter as unknown rather than guessing one.
    """
    if not source:
        return "unknown", "distill"
    origin, _, filter_name = source.partition(":")
    return filter_name or "unknown", "hook" if origin.startswith("hook") else "distill"


def _read_only_lookup(repo_root: Path, refs: Sequence[str]) -> tuple[set[str], dict[str, str]]:
    database = repo_root.joinpath(*_SIDECAR_DIR, _SIDECAR_DB)
    if not database.is_file():
        return set(), {}
    from repowise.core.savings.repository import SavingsRepository

    try:
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as conn:
            return (
                SavingsRepository(conn).recorded_omission_refs(refs),
                omission_sources(conn, refs),
            )
    except sqlite3.Error:
        return set(), {}
