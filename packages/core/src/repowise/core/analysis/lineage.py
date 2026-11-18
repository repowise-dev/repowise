"""A function's identity across renames and moves: its lineage.

When a run rewrites ``function_facts``, the stored rows it replaces are the old
side and the walked rows are the new side. A row whose symbol id survives,
directly or through a file rename, keeps its lineage. The rest pair through the
move passes (``change_health.moves.build_move_map``) on the stored body digest,
each pass taking only keys unique on both sides:

1. same qualified name and same body, in any file;
2. same body under a new name or path, 20 tokens or more;
3. same qualified name with an edited body, across files, when the caller can
   read both bodies and most of the old one survived.

Precision first: a function that does not pair gets a fresh lineage. Where a
candidate existed but could not be taken, a ``broken`` event records why.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repowise.core.ingestion.symbol_identity import file_of_symbol, make_symbol_id

from .change_health.moves import (
    MIN_RENAMED_TOKENS,
    SymbolBody,
    build_move_map,
    similar_bodies,
    sized_alike,
    symbol_bodies,
)

#: ``(old symbol id, new symbol id) -> True`` when the new body kept most of the old one.
SimilarFn = Callable[[str, str], bool]


@dataclass(frozen=True, slots=True)
class FunctionBody:
    """One function as the pairing reads it: its id, body digest and size, and lineage."""

    symbol_id: str
    digest: str | None
    tokens: int | None
    lineage_id: str | None = None


@dataclass(frozen=True, slots=True)
class LineageEvent:
    """A decision about one lineage: where it went, or why it ended."""

    lineage_id: str
    from_symbol_id: str
    to_symbol_id: str | None
    #: ``file_rename``, ``verbatim_move``, ``body_rename``, ``edited_move`` or ``broken``.
    method: str
    #: Why a ``broken`` lineage did not pair; None otherwise.
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class LineagePlan:
    """The lineage of every new function, and the events that decided it."""

    lineage: dict[str, str]
    events: list[LineageEvent]


def mint_lineage(symbol_id: str, commit: str | None) -> str:
    """A fresh lineage id for *symbol_id* first seen at *commit*.

    Deterministic, so a row stored before lineage existed (no commit known)
    gets the same id on every read until a write stores it.
    """
    seed = f"{symbol_id}\0{commit or ''}".encode()
    return hashlib.blake2b(seed, digest_size=8).hexdigest()


def pair_lineage(
    old: Iterable[FunctionBody],
    new: Iterable[FunctionBody],
    *,
    renames: Mapping[str, str] | None = None,
    commit: str | None = None,
    similar: SimilarFn | None = None,
) -> LineagePlan:
    """Carry each *old* lineage to the *new* function that continues it.

    *renames* maps an old file path to its new one. *similar* reads both bodies
    of a same-named cross-file candidate; without it that pass pairs nothing.
    """
    renames = dict(renames or {})
    new_by_id = {body.symbol_id: body for body in new}
    old_by_id = {body.symbol_id: body for body in old}
    lineage: dict[str, str] = {}
    events: list[LineageEvent] = []

    # A kept id first, so a file renamed onto an existing one cannot take
    # the lineage of a function that never left.
    _carry_kept(old_by_id, new_by_id, lineage)
    removed = _carry_file_renames(old_by_id, new_by_id, renames, lineage, events)
    added = [body for sid, body in new_by_id.items() if sid not in lineage]

    # Only ``path::name`` ids round-trip through the move passes' keys.
    base = [_as_body(b) for b in removed if b.digest is not None and "::" in b.symbol_id]
    head = [_as_body(b) for b in added if b.digest is not None and "::" in b.symbol_id]

    paired_old = _carry_moves(base, head, old_by_id, renames, similar, lineage, events)
    _record_broken(base, head, paired_old, old_by_id, renames, similar is not None, lineage, events)
    for body in added:
        lineage.setdefault(body.symbol_id, mint_lineage(body.symbol_id, commit))
    return LineagePlan(lineage, events)


def _lineage_of(body: FunctionBody) -> str:
    return body.lineage_id or mint_lineage(body.symbol_id, None)


def _carry_kept(
    old_by_id: Mapping[str, FunctionBody],
    new_by_id: Mapping[str, FunctionBody],
    lineage: dict[str, str],
) -> None:
    """An id present on both sides keeps its lineage."""
    for sid, body in old_by_id.items():
        if sid in new_by_id:
            lineage[sid] = _lineage_of(body)


def _carry_file_renames(
    old_by_id: Mapping[str, FunctionBody],
    new_by_id: Mapping[str, FunctionBody],
    renames: Mapping[str, str],
    lineage: dict[str, str],
    events: list[LineageEvent],
) -> list[FunctionBody]:
    """Carry an id whose file was renamed; return the old functions left unpaired."""
    removed: list[FunctionBody] = []
    for sid, body in old_by_id.items():
        if sid in lineage:
            continue
        target = _renamed(sid, renames)
        if target != sid and target in new_by_id and target not in lineage:
            lineage[target] = _lineage_of(body)
            events.append(LineageEvent(lineage[target], sid, target, "file_rename"))
        else:
            removed.append(body)
    return removed


def _carry_moves(
    base: list[SymbolBody],
    head: list[SymbolBody],
    old_by_id: Mapping[str, FunctionBody],
    renames: Mapping[str, str],
    similar: SimilarFn | None,
    lineage: dict[str, str],
    events: list[LineageEvent],
) -> set[str]:
    """Pair the rest through the move passes; return the old ids they took."""

    def kept(old_body: SymbolBody, new_body: SymbolBody) -> bool:
        # The size gate first: it needs only the stored counts, and the
        # comparison behind *similar* reads and parses both files.
        return (
            similar is not None
            and sized_alike(old_body, new_body)
            and similar(_id_of(old_body), _id_of(new_body))
        )

    moves = build_move_map(base, head, rename_map=renames, within_file=True, similar=kept)
    paired_old: set[str] = set()
    for (new_path, new_name), (old_path, old_name) in moves.by_new.items():
        old_id, new_id = make_symbol_id(old_path, old_name), make_symbol_id(new_path, new_name)
        if (new_path, new_name) in moves.edited:
            method = "edited_move"
        elif new_name == old_name:
            method = "verbatim_move"
        else:
            method = "body_rename"
        lineage[new_id] = _lineage_of(old_by_id[old_id])
        paired_old.add(old_id)
        events.append(LineageEvent(lineage[new_id], old_id, new_id, method))
    return paired_old


def _record_broken(
    base: list[SymbolBody],
    head: list[SymbolBody],
    paired_old: set[str],
    old_by_id: Mapping[str, FunctionBody],
    renames: Mapping[str, str],
    can_compare: bool,
    lineage: Mapping[str, str],
    events: list[LineageEvent],
) -> None:
    """A ``broken`` event for each unpaired old function that had a candidate."""
    leftover_new = [b for b in head if _id_of(b) not in lineage]
    leftover_old = [b for b in base if _id_of(b) not in paired_old]
    for body, reason in _broken(leftover_old, leftover_new, renames, can_compare):
        events.append(
            LineageEvent(_lineage_of(old_by_id[_id_of(body)]), _id_of(body), None, "broken", reason)
        )


def similar_by_source(repo_path: Any, base_commit: str | None) -> SimilarFn | None:
    """Compare an old body at *base_commit* with the new one on disk, or ``None``
    when there is no base to read.

    Reached only for a same-named cross-file candidate, so each file is read
    and parsed on demand, once.
    """
    if repo_path is None or not base_commit:
        return None
    from ..ingestion.change_detector import ChangeDetector
    from .change_health.sources import read_blobs

    root = Path(repo_path)
    detector: ChangeDetector | None = None
    parsed: dict[tuple[str | None, str], Any] = {}

    def body(rev: str | None, symbol_id: str) -> SymbolBody | None:
        path = file_of_symbol(symbol_id)
        if (rev, path) not in parsed:
            if rev is None:
                file = root / path
                raw = file.read_bytes() if file.is_file() else None
            else:
                raw = (read_blobs(str(root), [(rev, path)]) or {}).get((rev, path))
            nonlocal detector
            detector = detector or ChangeDetector(root)
            parsed[(rev, path)] = (raw, detector.parse_bytes(raw, path) if raw else None)
        raw, pf = parsed[(rev, path)]
        symbols = [s for s in (pf.symbols if pf else ()) if s.id == symbol_id]
        bodies = symbol_bodies(path, raw, symbols) if symbols else []
        return bodies[0] if bodies else None

    def similar(old_id: str, new_id: str) -> bool:
        # An unreadable or unparsable side leaves the pair unmade, never the run failed.
        try:
            old, new = body(base_commit, old_id), body(None, new_id)
        except Exception:
            return False
        return old is not None and new is not None and similar_bodies(old, new)

    return similar


def _broken(
    old: list[SymbolBody], new: list[SymbolBody], renames: Mapping[str, str], can_compare: bool
) -> Iterable[tuple[SymbolBody, str]]:
    """Each unpaired old function that had a candidate, with why it was not taken."""
    old_digests = Counter(b.digest for b in old)
    new_digests = Counter(b.digest for b in new)
    old_names = Counter(b.name for b in old)
    new_names = Counter(b.name for b in new)
    new_paths = {b.name: b.path for b in new}
    for body in old:
        if new_digests[body.digest]:
            if old_digests[body.digest] > 1 or new_digests[body.digest] > 1:
                yield body, "ambiguous_body"
            elif body.tokens < MIN_RENAMED_TOKENS:
                yield body, "short_body"
        elif new_names[body.name]:
            if old_names[body.name] > 1 or new_names[body.name] > 1:
                yield body, "ambiguous_name"
            elif new_paths[body.name] != renames.get(body.path, body.path):
                yield body, "edited_body" if can_compare else "unverified_edit"


def _renamed(symbol_id: str, renames: Mapping[str, str]) -> str:
    path = file_of_symbol(symbol_id)
    moved = renames.get(path)
    return f"{moved}{symbol_id[len(path) :]}" if moved else symbol_id


def _as_body(body: FunctionBody) -> SymbolBody:
    path = file_of_symbol(body.symbol_id)
    name = body.symbol_id[len(path) + 2 :]
    return SymbolBody(path, name, body.digest or "", body.tokens or 0)


def _id_of(body: SymbolBody) -> str:
    return make_symbol_id(body.path, body.name)


__all__ = [
    "FunctionBody",
    "LineageEvent",
    "LineagePlan",
    "SimilarFn",
    "mint_lineage",
    "pair_lineage",
    "similar_by_source",
]
