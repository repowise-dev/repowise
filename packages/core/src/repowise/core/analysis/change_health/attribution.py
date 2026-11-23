"""Why a finding is charged to this change, and how sure that is.

Presence at head is never on its own a reason. Every surfaced finding carries
the basis that ties it to the diff, and a finding the diff cannot explain is
reported as such rather than quietly counted as new.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ...ingestion.models import Symbol
from ...symbol_spans import innermost, symbols_overlapping
from ..changed_lines import line_ranges
from ..health import HealthFindingData
from .models import ATTRIBUTION_CONFIDENCE, AttributionBasis, ChangedSymbol, SymbolChange
from .moves import MoveMap, SymbolKey, is_body_symbol
from .sources import FileChange


@dataclass(frozen=True, slots=True)
class Attribution:
    basis: AttributionBasis
    confidence: str
    detail: str


_UNKNOWN = Attribution(
    "unknown",
    ATTRIBUTION_CONFIDENCE["unknown"],
    "Present at head, but the diff does not explain it.",
)


def _overlaps(finding: HealthFindingData, added: set[int]) -> bool:
    start, end = finding.line_start, finding.line_end
    if start is None:
        return False
    return any(line in added for line in range(start, (end or start) + 1))


class FindingAttributor:
    """Decide the strongest basis tying a head finding to the change."""

    def __init__(self, changes: dict[str, FileChange]) -> None:
        self.changes = changes

    def attribute(
        self,
        finding: HealthFindingData,
        *,
        changed_symbols: set[tuple[str, str]] | None = None,
        changed_call_edge: str | None = None,
        moved_from: SymbolKey | None = None,
    ) -> Attribution:
        if moved_from is not None:
            # Ahead of new_file: a split's new file holds code the change only carried.
            return Attribution(
                "moved",
                ATTRIBUTION_CONFIDENCE["moved"],
                f"Moved here from {moved_from[0]} ({moved_from[1]}).",
            )
        change = self.changes.get(finding.file_path)
        if change is None:
            return _UNKNOWN
        if change.is_new:
            return Attribution(
                "new_file",
                ATTRIBUTION_CONFIDENCE["new_file"],
                f"{finding.file_path} is added by this change.",
            )
        added = change.added_lines
        if _overlaps(finding, added):
            return Attribution(
                "added_lines",
                ATTRIBUTION_CONFIDENCE["added_lines"],
                f"Lines {finding.line_start}-{finding.line_end or finding.line_start} "
                "are added or rewritten by this change.",
            )
        symbol = finding.function_name
        if symbol and changed_symbols and (finding.file_path, symbol) in changed_symbols:
            return Attribution(
                "changed_symbol",
                ATTRIBUTION_CONFIDENCE["changed_symbol"],
                f"{symbol} is edited by this change.",
            )
        if changed_call_edge:
            return Attribution(
                "changed_call_edge",
                ATTRIBUTION_CONFIDENCE["changed_call_edge"],
                f"Reached through {changed_call_edge}, whose call path this change edits.",
            )
        if added:
            return Attribution(
                "file_change",
                ATTRIBUTION_CONFIDENCE["file_change"],
                f"{finding.file_path} is edited by this change, away from these lines.",
            )
        return Attribution(
            "context_change",
            ATTRIBUTION_CONFIDENCE["context_change"],
            f"{finding.file_path} changed only in ways the diff does not localise.",
        )


def changed_symbols_for(
    changes: dict[str, FileChange], findings_by_file: dict[str, list[HealthFindingData]]
) -> set[tuple[str, str]]:
    """``(path, symbol)`` pairs whose body overlaps the change's added lines."""
    out: set[tuple[str, str]] = set()
    for path, change in changes.items():
        added = change.added_lines
        if not added:
            continue
        for finding in findings_by_file.get(path, ()):
            if finding.function_name and _overlaps(finding, added):
                out.add((path, finding.function_name))
    return out


def changed_symbols(
    changes: Iterable[FileChange],
    head_symbols: Mapping[str, list[Symbol]],
    base_symbols: Mapping[str, list[Symbol]],
    moves: MoveMap,
) -> list[ChangedSymbol]:
    """Every symbol the diff touches, findings or not, by path then line.

    Head symbols are read against the new-side lines, base symbols against
    the old-side ranges: a base symbol that lost lines and is still at head
    is ``edited`` there, and one head lacks is ``deleted``. A head symbol
    base lacks is ``added``, unless the move map pairs it across files, in
    which case it is ``moved`` and reported once, at head. An edit is charged
    to the innermost symbol it touched, not to every enclosing one.
    """
    origins = set(moves.by_new.values())
    out: list[ChangedSymbol] = []
    for change in changes:
        base = _held(base_symbols, change.base_path)
        head = _held(head_symbols, change.head_path)
        cut = _lost_lines(change, base)
        if change.head_path and head:
            known_base = change.base_path is not None and change.base_path in base_symbols
            out += _head_side(change, change.head_path, head, base, cut, known_base, moves)
        head_ids = {_local_id(s) for s in head}
        out += [
            _row(change.base_path, s, "deleted")
            for s in cut
            if change.base_path
            and _local_id(s) not in head_ids
            and (change.base_path, s.name) not in origins
        ]
    out.sort(key=lambda r: (r.path, r.start_line, r.end_line, r.symbol))
    return out


def _head_side(
    change: FileChange,
    path: str,
    head: list[Symbol],
    base: list[Symbol],
    cut: list[Symbol],
    known_base: bool,
    moves: MoveMap,
) -> list[ChangedSymbol]:
    if change.is_new:
        touched = head
    else:
        hit = {id(s) for s in symbols_overlapping(head, line_ranges(change.added_lines))}
        lost_lines = {_local_id(s) for s in cut}
        touched = [s for s in head if id(s) in hit or _local_id(s) in lost_lines]
    base_ids = {_local_id(s) for s in base}
    # Without a parsed base side, nothing proves a symbol is new.
    new = change.is_new or known_base
    kinds = {id(s): _head_kind(path, s, base_ids if new else None, moves) for s in touched}
    leaves = {id(s) for s in innermost(touched)}
    return [
        _row(path, s, *kinds[id(s)])
        for s in touched
        if kinds[id(s)][0] != "edited" or id(s) in leaves
    ]


def _lost_lines(change: FileChange, base: list[Symbol]) -> list[Symbol]:
    """Base symbols the change removed lines from: all of them for a deleted file."""
    if change.is_deleted:
        return base
    if change.is_new or not change.diff:
        return []
    return symbols_overlapping(base, change.diff.old_ranges)


def _head_kind(
    path: str, symbol: Symbol, base_ids: set[str] | None, moves: MoveMap
) -> tuple[SymbolChange, SymbolKey | None]:
    """``moved`` with its origin, ``added`` when *base_ids* lacks it, else ``edited``."""
    origin = moves.origin_of(path, symbol.name)
    if origin is not None:
        return "moved", origin
    if base_ids is not None and _local_id(symbol) not in base_ids:
        return "added", None
    return "edited", None


def _held(symbols: Mapping[str, list[Symbol]], path: str | None) -> list[Symbol]:
    return [s for s in symbols.get(path or "", ()) if is_body_symbol(s)]


def _local_id(symbol: Symbol) -> str:
    """The symbol's identity inside its file (``Class::method``), stable across a rename."""
    _, sep, local = symbol.id.partition("::")
    return local if sep else symbol.name


def _row(
    path: str, symbol: Symbol, change: SymbolChange, moved_from: SymbolKey | None = None
) -> ChangedSymbol:
    return ChangedSymbol(
        path=path,
        symbol=_local_id(symbol),
        kind=str(symbol.kind),
        change=change,
        start_line=symbol.start_line,
        end_line=symbol.end_line,
        moved_from=moved_from,
    )
