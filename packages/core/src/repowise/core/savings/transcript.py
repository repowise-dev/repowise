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
- **Never repriced.** A past saving is never revalued at today's model. That
  is not the same as leaving it unvalued: the transcript names the model that
  was in the chair, so each event is priced from *its own* evidence, and one
  whose model the rate table does not know stays unpriced rather than guessed.
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

#: Seconds of transcript reading per run, split across harnesses. Stopping
#: between files is safe: cursors are per file and saved after the loop, so
#: the next run resumes where this one stopped. Stopping *within* a file is
#: what ``_rewind`` covers.
SYNC_BUDGET_S = 20.0

#: The host truncation the live path applies, in tokens. ``estimate_tokens``
#: is ``len // 4``, so capping characters and capping tokens agree exactly.
HOST_OUTPUT_CAP_TOKENS = HOST_OUTPUT_CAP_CHARS // 4

#: The largest shell result Codex was observed to actually deliver, across
#: 95,814 of them in this repository. A host cannot have truncated below what
#: it demonstrably handed the model, so this is a *measured* lower bound on
#: Codex's cap rather than an estimate of one.
#:
#: Two observations sit behind this number and they disagree, so both are
#: recorded:
#:
#: - Most large Codex results stop at a plateau: above 5,000 characters the
#:   commonest lengths are 24,133 (164 texts) and a tight cluster at
#:   40,100-40,104, over 200 texts within four characters of one another.
#:   That is a cap with a variable-length truncation notice after it. Claude
#:   Code's plateau is the same shape and sharper -- 24 texts at exactly
#:   30,000, nothing above it.
#: - But Codex does not always stop there. Results run to 159,585, 434,762,
#:   1,510,994 and 2,552,250 characters, none of them truncated.
#:
#: So the plateau is a cap Codex applies *sometimes*, and picking it would
#: undercount every result that escaped it. Set at the maximum instead, on
#: the standing preference that a figure this ledger reports should err high
#: rather than low. That is a real trade and not a free one: the five largest
#: events become 29% of the recorded total, each crediting a single command
#: with more tokens than a model's context can hold. The plateau figure
#: (40,000 characters) is the conservative alternative and costs about 4x.
#:
#: Claude Code needs no equivalent choice -- its plateau *is* its maximum.
_CODEX_OUTPUT_CAP_CHARS = 2_552_250

#: Per-harness output cap, in tokens. ``estimate_tokens`` is ``len // 4``, so
#: capping characters and capping tokens agree exactly.
#:
#: The defect this replaces: ``HOST_OUTPUT_CAP_CHARS`` is exactly right for
#: Claude Code, and its own comment said it was "applied to every source".
#: Charging it to Codex clipped 306 of this ledger's events to 7,500 tokens
#: and left 111 of them delivering *more* than their own baseline, which is
#: incoherent for a measured pair and banks zero.
#:
#: An unlisted harness gets ``None``, meaning no cap: a cap is a claim that a
#: host truncated, and asserting one we never observed is precisely how this
#: happened. ``_accounting`` never clips below what was actually delivered,
#: so the invariant holds whatever this table says.
_HARNESS_OUTPUT_CAP_TOKENS: dict[str, int | None] = {
    "claude_code": HOST_OUTPUT_CAP_TOKENS,
    "codex": _CODEX_OUTPUT_CAP_CHARS // 4,
}

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
    #: Events the ledger refused, usually a contended sidecar. Non-zero holds
    #: the cursors back so the next run re-reads rather than losing them.
    write_failures: int = 0
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
    #: The model in the chair when this output was read back, or ``None``.
    #: Not read off the event carrying the marker: a tool *result* is a user
    #: line in every harness here and names no model. It is the most recent
    #: assistant line before it in the same transcript -- the turn that ran
    #: the command and then read the answer. Evidence that existed when the
    #: saving happened, which is the whole basis for pricing this population.
    model: str | None = None


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
    if result.write_failures:
        # The recorder never raises -- a contended sidecar comes back as False,
        # not as an exception -- so an advanced cursor here would step past a
        # marker nothing recorded and nothing can find again. Re-reading costs
        # one pass; the ref anti-join makes it free of double counting.
        return result
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
        resume = cursors.get(path)
        try:
            events = iter_new_events(adapter, path, cursors, prefilter=_gate(adapter))
            unpaired = _collect(events, harness, repo_root, shell_tools, candidates)
            result.transcripts_read += 1
        except OSError:
            continue
        if unpaired:
            # A shell call whose result has not been written yet. Its marker is
            # past the cursor, and the pairing that identifies it as a shell
            # result is behind it, so advancing now would make the marker
            # unattributable forever. Re-read this file next time instead.
            _rewind(cursors, path, resume)


def _gate(adapter: HarnessAdapter) -> RawPrefilter | None:
    """The adapter's tool-call gate, widened to keep marker and model lines.

    The tool call and the result carrying its marker are separate lines, and
    only the pair says a marker came from a shell command, so a marker-only
    gate sees results it can no longer attribute. Widening rather than
    narrowing matters because the cursor advances per line read: a line this
    gate drops is consumed, not revisited.

    The model is the same shape of problem one line further out, and it cost
    93% of the ledger's price. Codex states its model on a ``turn_context``
    line that carries no tool call, so the unwidened gate consumed it and
    every Codex event was written unpriced -- measured at 0 of 1,219
    candidates here, against 25 of 27 for Claude Code, which happens to state
    its model on the same assistant line as the tool call.

    A substring rather than an adapter method: both harnesses spell it
    ``"model"`` in the JSON, one extra line normalized is free, and a method
    with two implementations and one caller is a worse answer than the test
    it would wrap. Costs ~4.8% more lines on Codex and ~1.1% on Claude Code,
    on a surface that runs at ``init`` and on demand under a 20-second
    budget -- never inside an agent's tool call.
    """
    tool_gate = adapter.prefilter(INTENT_TOOL_CALLS)
    if tool_gate is None:
        return None

    def gate(raw_line: str) -> bool:
        return tool_gate(raw_line) or "repowise#" in raw_line or '"model"' in raw_line

    return gate


def _collect(
    events: Iterable[Event],
    harness: str,
    repo_root: Path,
    shell_tools: frozenset[str],
    candidates: dict[str, _Candidate],
) -> set[str]:
    """Collect markers from shell results; return shell calls left unanswered."""
    shell_calls: set[str] = set()
    # Carried forward rather than read per event: see ``_Candidate.model``.
    # Scoped to this call, which is one transcript, so a model never leaks
    # across sessions. A read resuming mid-file starts with None and leaves
    # its first markers unpriced, which is the right way to be wrong here.
    model: str | None = None
    for event in events:
        model = _carried_model(event, model)
        for use in event.tool_uses:
            if use.name in shell_tools:
                shell_calls.add(use.id)
        for block in event.tool_results:
            if block.tool_use_id not in shell_calls:
                continue
            shell_calls.discard(block.tool_use_id)
            if not _in_repo(event, repo_root):
                continue
            _harvest(block, event, harness, candidates, model)
    return shell_calls


def _harvest(
    block: ToolResult,
    event: Event,
    harness: str,
    candidates: dict[str, _Candidate],
    model: str | None = None,
) -> None:
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
                    model=model,
                ),
            )


def _rewind(cursors: CursorStore, path: Path, resume: dict[str, Any] | None) -> None:
    """Put *path*'s cursor back where this pass found it."""
    if resume is None:
        cursors.advance(path, offset=0, mtime=0.0)
    else:
        cursors.advance(path, offset=resume["offset"], mtime=resume["mtime"])


def _in_repo(event: Event, repo_root: Path) -> bool:
    """True when the event states a ``cwd`` inside *repo_root*.

    A stated ``cwd`` is required rather than assumed, which is also what the
    sibling scan in ``distill.missed`` does. One harness files transcripts by
    date and returns every rollout on the machine from ``discover``, so for it
    ``cwd`` is the only thing separating this repository from another, and
    reading an absent one as "no opinion" would bank a second repository's
    savings here -- in a ledger whose ref anti-join is per repository, so both
    would count it.
    """
    if not event.cwd:
        return False
    try:
        cwd = Path(event.cwd).resolve()
    except OSError:
        return False
    return cwd == repo_root or repo_root in cwd.parents


def _occurred_at(event: Event) -> datetime:
    return datetime.fromtimestamp(event.ts if event.ts is not None else time.time(), UTC)


def _result_texts(block: ToolResult) -> Iterable[str]:
    """Every string a tool result carries, across the shapes harnesses use.

    A result is a bare string, a list of content blocks, or a record of named
    streams, depending on the harness and the tool.
    """
    for blob in (block.content, block.payload):
        yield from _strings_in(blob)


def _strings_in(blob: Any) -> Iterable[str]:
    if isinstance(blob, str):
        yield blob
    elif isinstance(blob, list):
        for item in blob:
            yield from _strings_in(item)
    elif isinstance(blob, dict):
        # A content block names its own text; a stream record does not, so
        # every string it holds is output.
        text = blob.get("text")
        if isinstance(text, str):
            yield text
        else:
            yield from (value for value in blob.values() if isinstance(value, str))


def _carried_model(event: Event, current: str | None) -> str | None:
    """The model in the chair after *event*, given it was *current* before.

    Two lines are refused rather than carried, and both were found by review
    rather than by the numbers, because both fail quietly:

    - **A sidechain.** Claude Code interleaves Task sub-agent lines into the
      main transcript and a sub-agent can run a different model, so carrying
      one prices the main thread's next command at the sub-agent's rate -- a
      5x error where a Haiku sub-agent lands between an Opus tool call and
      its result. Every other miner here filters them for the same reason
      (``sessions/miners/decisions.py``, ``precedent``, ``decisions``).
    - **A sentinel.** Claude Code writes ``<synthetic>`` for an API error or
      an interrupted message. It resolves to no rate, which is right, but it
      has to be refused *here* too: letting it overwrite a known-good model
      leaves every later marker in the file unpriced. Angle brackets are the
      shape harnesses use for "not a real value".
    """
    if not event.model or event.sidechain or event.model.startswith("<"):
        return current
    return event.model


def _accounting(candidate: _Candidate) -> tuple[int, int]:
    """``(baseline, delivered)`` input tokens for one recovered marker.

    ``delivered`` is the text the marker was found in, which for a shell
    result is the command output the model read back. ``baseline`` adds back
    what the marker says was dropped, minus the marker's own cost, then
    re-applies **this harness's** truncation -- bytes past it never reached
    the model and cannot be claimed.

    Whose truncation matters, and it used to be nobody's in particular:
    Claude Code's 30,000-character cap was charged to every source, Codex
    included, and Codex does not truncate. See
    ``_HARNESS_OUTPUT_CAP_TOKENS`` for the measurement.

    The cap never clips below ``delivered``. A host cannot have truncated
    below what it demonstrably handed the model, so this is not a fudge but
    the definition -- and it keeps the invariant true for a harness nobody
    has measured yet, which is the case that produced this bug.
    """
    delivered = candidate.delivered_tokens
    kept = max(delivered - estimate_tokens(candidate.marker.text), 0)
    stored = kept + candidate.marker.tokens_omitted
    cap = _HARNESS_OUTPUT_CAP_TOKENS.get(candidate.harness)
    capped = stored if cap is None else min(stored, cap)
    # The floor applies on both branches. An uncapped harness can still land
    # under ``delivered`` when the marker's own text costs more than what it
    # says was omitted, and that would store a baseline smaller than the
    # delivery it describes -- the very incoherence this function now exists
    # to rule out.
    return max(capped, delivered), delivered


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
        **_pricing_payload(candidate),
    }


def _pricing_payload(candidate: _Candidate) -> dict[str, Any]:
    """The rate fields for one recovered marker, empty when it cannot be priced.

    Not a departure from "never repriced" but the point of it. The rule exists
    so a past saving is never revalued at *today's* model; this prices each
    event at the model that was in the chair when it happened, read out of the
    same transcript line that proves the saving. The evidence was always there
    and the first version of this module simply dropped it, which left 93% of
    the ledger's tokens carrying no rate at all.

    Empty when the model is unknown to the rate table. An unpriced event is
    the honest outcome and the report already counts the two populations
    apart; a guessed rate would be worse than the silence it replaced.
    """
    from repowise.core.savings.pricing import snapshot_for_model

    snapshot = snapshot_for_model(candidate.model, f"transcript_model:{candidate.harness}")
    return snapshot.as_payload() if snapshot is not None else {}


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
            result.write_failures += 1
            continue
        saved = max(payload["baseline_input_tokens"] - payload["delivered_input_tokens"], 0)
        result.recorded += 1
        result.saved_input_tokens += saved
        result.per_harness[candidate.harness] = result.per_harness.get(candidate.harness, 0) + saved


def _origin(source: str | None) -> tuple[str, str]:
    """``omissions.source`` as ``(operation, surface)``.

    It reads ``"<origin>:<filter>"`` -- ``cli:git_diff``,
    ``hook-codex:test_output``, ``mcp:get_context`` -- and is the only record
    of which surface and filter produced a ref. It is TTL-pruned, so most refs
    a transcript reaches are past it and record the filter as unknown rather
    than guessing one.
    """
    if not source:
        return "unknown", "distill"
    origin, _, filter_name = source.partition(":")
    if origin.startswith("hook"):
        surface = "hook"
    elif origin == "mcp":
        surface = "mcp"
    else:
        surface = "distill"
    return filter_name or "unknown", surface


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
