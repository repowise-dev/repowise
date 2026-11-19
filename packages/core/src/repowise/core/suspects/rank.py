"""Changes in a window ranked against a symptom, by reciprocal rank fusion.

Candidates are the Change Records merged in the window before the symptom.
Each signal on ``SeamRegistry("suspect-signal")`` ranks the candidates it has
evidence for; the fused order is ``sum(1 / (k + rank))`` over the signals that
fired, k=60, equal weights (Cormack et al. 2009), ties newest first. A row
carries every signal that fired with its own rank and evidence. The output is
an order to read changes in, never a probability and never a cause.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from repowise.core.co_change import CoChangePartner
from repowise.core.frame_resolver import ProfileFrame, resolve_frames
from repowise.core.registry.plugins import SeamRegistry
from repowise.core.stack_trace import Frame, map_to_repo_paths, parse_trace

if TYPE_CHECKING:
    from repowise.core.workspace.repo_index import IndexedSymbol

RRF_K = 60
#: Input bounds: a pasted log can be huge and only its head is a symptom.
MAX_SYMPTOM_CHARS = 20_000
MAX_SYMPTOM_FILES = 50
MAX_PATH_MENTIONS = 200

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Symptom:
    """What was seen: free text (an issue, an error), files, a stack trace."""

    text: str = ""
    files: tuple[str, ...] = ()
    trace: str = ""
    #: When it was seen; candidates are changes merged before it.
    at: datetime | None = None


@dataclass(frozen=True)
class Candidate:
    """One Change Record in the window, with what the signals read."""

    change_id: str
    title: str
    merged_at: datetime
    files: tuple[str, ...]
    body: str = ""
    #: Names git's hunk headers give for the code the change touched.
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class SuspectContext:
    """The index facts signals read; an empty mapping means no evidence."""

    repo_files: frozenset[str] = frozenset()
    #: File to the files it imports or is imported by.
    imports: Mapping[str, Iterable[str]] = field(default_factory=dict)
    co_change: Mapping[str, Sequence[CoChangePartner]] = field(default_factory=dict)
    #: The repo's own name words: matched everywhere, so never shown as evidence.
    repo_terms: frozenset[str] = frozenset()


@dataclass(frozen=True)
class FrameSymbol:
    """A trace frame joined to the indexed symbol that ran it."""

    path: str
    name: str
    symbol_id: str


@dataclass(frozen=True)
class PreparedSymptom:
    symptom: Symptom
    at: datetime
    #: Repo files the symptom names: given, in the text, or in trace frames.
    files: tuple[str, ...]
    frames: tuple[Frame, ...]
    trace_files: tuple[str, ...]
    #: Symbols the trace's frames resolved to, innermost frame first.
    frame_symbols: tuple[FrameSymbol, ...] = ()
    #: Positions in ``frames`` that resolved (to a symbol or to a module body).
    resolved_frames: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Hit:
    """A signal's score for one candidate (higher ranks first) and why."""

    score: float
    evidence: tuple[str, ...] = ()


class SuspectSignal(Protocol):
    def rank(
        self, symptom: PreparedSymptom, candidates: Sequence[Candidate], ctx: SuspectContext
    ) -> Mapping[str, Hit]:
        """Hits by ``change_id`` for the candidates this signal has evidence on."""
        ...


@dataclass(frozen=True)
class SignalRank:
    rank: int
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class Suspect:
    change_id: str
    rank: int
    merged_at: datetime
    signals: dict[str, SignalRank]


suspect_signals: SeamRegistry[SuspectSignal] = SeamRegistry("suspect-signal")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _path_mentions(text: str) -> list[Frame]:
    from .signals import path_mentions

    return [Frame(p, 0, None) for p in path_mentions(text)[:MAX_PATH_MENTIONS]]


def bounded(symptom: Symptom) -> Symptom:
    """*symptom* cut to the input bounds."""
    return Symptom(
        symptom.text[:MAX_SYMPTOM_CHARS],
        tuple(symptom.files[:MAX_SYMPTOM_FILES]),
        symptom.trace[:MAX_SYMPTOM_CHARS],
        symptom.at,
    )


def prepare(symptom: Symptom, ctx: SuspectContext) -> PreparedSymptom:
    """The symptom read against the repo's files; reads only ``ctx.repo_files``."""
    symptom = bounded(symptom)
    frames = tuple(parse_trace(symptom.trace or symptom.text))
    trace_files = tuple(map_to_repo_paths(frames, ctx.repo_files))
    given = [f.replace("\\", "/").removeprefix("./") for f in symptom.files]
    named = map_to_repo_paths(_path_mentions(symptom.text), ctx.repo_files)
    files = tuple(dict.fromkeys([*given, *named, *trace_files]))[:MAX_SYMPTOM_FILES]
    at = _aware(symptom.at) if symptom.at else datetime.now(UTC)
    return PreparedSymptom(symptom, at, files, frames, trace_files)


def resolvable(frame: Frame) -> bool:
    """Whether :func:`resolve_trace` can join *frame*: a named Python frame of the repo."""
    return bool(frame.function) and not frame.vendored and frame.path.endswith(".py")


def resolve_trace(
    prepared: PreparedSymptom,
    *,
    root: str,
    symbols_by_file: Mapping[str, Sequence[IndexedSymbol]],
    dirty_paths: Collection[str] = (),
    origins: Mapping[str, str] | None = None,
    read_lines: Callable[[str], Sequence[str] | None] | None = None,
) -> PreparedSymptom:
    """*prepared* with its Python frames joined to symbols of the checkout at *root*.

    Python only: a pasted Go trace carries no function start line and a
    JavaScript trace gives the executing position rather than the function's
    start, so neither has the second signal :func:`resolve_frames` requires.
    Those frames, and any that do not resolve, keep matching by file and name.
    """
    picked = [(i, f) for i, f in enumerate(prepared.frames) if resolvable(f)]
    if not picked:
        return prepared
    resolution = resolve_frames(
        [ProfileFrame(f.path, f.line, "python", function=f.function) for _i, f in picked],
        root=root,
        symbols_by_file=symbols_by_file,
        dirty_paths=dirty_paths,
        origins=origins,
        read_lines=read_lines,
    )
    found = [
        FrameSymbol(r.path, r.symbol.name, r.symbol.symbol_id)
        for r in resolution.resolved
        if r.symbol is not None
    ]
    return replace(
        prepared,
        frame_symbols=tuple(dict.fromkeys(found)),
        resolved_frames=frozenset(picked[r.index][0] for r in resolution.resolved),
    )


def _competition_ranks(hits: Mapping[str, Hit]) -> dict[str, int]:
    """1-based ranks, equal scores sharing the better rank (1, 1, 3)."""
    ordered = sorted(hits.items(), key=lambda kv: -kv[1].score)
    ranks: dict[str, int] = {}
    prev: float | None = None
    rank = 0
    for i, (cid, hit) in enumerate(ordered, start=1):
        if hit.score != prev:
            rank, prev = i, hit.score
        ranks[cid] = rank
    return ranks


def rank_suspects(
    symptom: Symptom,
    candidates: Sequence[Candidate],
    ctx: SuspectContext,
    *,
    signals: Sequence[str] | None = None,
    k: int = RRF_K,
    prepared: PreparedSymptom | None = None,
) -> list[Suspect]:
    """Every candidate in fused order; *signals* restricts which signals run.

    *prepared* is :func:`prepare`'s result for this symptom and these repo
    files, when the caller already has it. A signal that raises did not fire.
    """
    selected = [(n, s) for n, s in suspect_signals.all() if signals is None or n in signals]
    prepared = prepared or prepare(symptom, ctx)
    fused: dict[str, float] = {c.change_id: 0.0 for c in candidates}
    fired: dict[str, dict[str, SignalRank]] = {c.change_id: {} for c in candidates}
    for name, signal in selected:
        try:
            found = signal.rank(prepared, candidates, ctx)
            hits = {cid: hit for cid, hit in found.items() if cid in fused}
        except Exception:
            _log.debug("suspect signal %s failed; it did not fire", name, exc_info=True)
            continue
        for cid, rank in _competition_ranks(hits).items():
            fused[cid] += 1.0 / (k + rank)
            fired[cid][name] = SignalRank(rank, hits[cid].evidence)
    order = sorted(
        candidates, key=lambda c: (-fused[c.change_id], -_aware(c.merged_at).timestamp(), c.change_id)
    )
    return [
        Suspect(c.change_id, i, _aware(c.merged_at), fired[c.change_id])
        for i, c in enumerate(order, start=1)
    ]


__all__ = [
    "MAX_PATH_MENTIONS",
    "MAX_SYMPTOM_CHARS",
    "MAX_SYMPTOM_FILES",
    "RRF_K",
    "Candidate",
    "FrameSymbol",
    "Hit",
    "PreparedSymptom",
    "SignalRank",
    "Suspect",
    "SuspectContext",
    "SuspectSignal",
    "Symptom",
    "bounded",
    "prepare",
    "rank_suspects",
    "resolvable",
    "resolve_trace",
    "suspect_signals",
]
