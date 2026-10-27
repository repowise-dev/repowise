"""Symbols a change carried from one file to another.

A symbol's body is its token stream with comments and whitespace dropped and
its own name blanked, so a reindent, a comment edit or a rename does not hide a
move. Pairing runs in three passes over the symbols that left their place on
the base side and the ones that appeared on the head side, each pass taking
only keys unique on both sides: same name and body, then same body under a new
name, then same name with an edited body that kept most of its token pairs. A
move is cross-file only; a symbol that stays in its file, or in the file a
rename carried it to, is not a move.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from itertools import pairwise
from types import MappingProxyType

from ...ingestion.models import Symbol
from ..health.duplication.tokenizer import TokenRow, tokenize_file_rows
from .analyzer import language_of

#: ``(path, symbol name)``.
SymbolKey = tuple[str, str]

_SKIPPED_KINDS = frozenset({"module", "impl"})

#: A body pairs under a new name only when it is this many tokens or more;
#: shorter bodies (``return x``, ``X = 1``) match by chance.
MIN_RENAMED_TOKENS = 20

#: An edited move may grow or shrink by at most this factor.
_MAX_EDITED_GROWTH = 3

#: An edited move keeps at least this share of its adjacent token pairs.
_MIN_EDITED_OVERLAP = 0.5

_NAME_MARK = b"\x00"
_SEPARATOR = b"\x1f"


@dataclass(frozen=True, slots=True)
class SymbolBody:
    """One symbol's normalized body, as the move passes compare it."""

    path: str
    name: str
    digest: str
    tokens: int
    #: Adjacent token pairs, hashed; how much of the body an edit kept.
    pairs: frozenset[int] = field(default=frozenset(), compare=False, repr=False)
    #: ``(start_line, end_line)`` in its file.
    span: tuple[int, int] = field(default=(0, 0), compare=False)

    @property
    def key(self) -> SymbolKey:
        return (self.path, self.name)


@dataclass(frozen=True, slots=True)
class MoveMap:
    """``{(new_path, symbol): (old_path, old_symbol)}`` for the symbols that moved."""

    by_new: Mapping[SymbolKey, SymbolKey] = field(default_factory=lambda: MappingProxyType({}))
    #: Moved keys whose body also changed on the way.
    edited: frozenset[SymbolKey] = frozenset()
    #: Line spans of the moved symbols at head, by new key.
    new_spans: Mapping[SymbolKey, tuple[int, int]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    #: Line spans of their origins on the base side, by origin key.
    old_spans: Mapping[SymbolKey, tuple[int, int]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def origin_of(self, path: str, symbol: str | None) -> SymbolKey | None:
        return self.by_new.get((path, symbol)) if symbol else None

    def locate(self, path: str, symbol: str | None, line: int | None) -> SymbolKey | None:
        """The moved key owning a finding: its symbol, else the innermost span at *line*."""
        if symbol:
            return (path, symbol) if (path, symbol) in self.by_new else None
        held = [k for k, span in self.new_spans.items() if k[0] == path and _within(span, line)]
        return min(held, key=lambda k: _length(self.new_spans[k]), default=None)

    def offset(self, key: SymbolKey, line: int | None, *, old: bool = False) -> int:
        """*line* relative to the start of *key*'s span, which travels with the code."""
        spans = self.old_spans if old else self.new_spans
        return (line or 0) - spans.get(key, (0, 0))[0]

    def holds(self, old_key: SymbolKey, line: int | None) -> bool:
        """True when *line* falls inside the origin symbol *old_key*."""
        return _within(self.old_spans.get(old_key), line)

    @property
    def moved_total(self) -> int:
        return len(self.by_new)

    def destinations(self) -> dict[str, list[str]]:
        """``{old_path: [new_path, ...]}``: where each source file's moved symbols went."""
        out: dict[str, set[str]] = {}
        for (new_path, _), (old_path, _) in self.by_new.items():
            out.setdefault(old_path, set()).add(new_path)
        return {old: sorted(new) for old, new in sorted(out.items())}


def symbol_bodies(path: str, raw: bytes, symbols: Iterable[Symbol]) -> list[SymbolBody]:
    """The normalized body of each named symbol in *path*, read from its *raw* bytes."""
    language = language_of(path)
    rows = tokenize_file_rows(language, raw, path) if language else []
    if not rows:
        return []
    starts = [row[1] for row in rows]
    out: list[SymbolBody] = []
    for symbol in symbols:
        if not symbol.name or symbol.is_declaration or symbol.kind in _SKIPPED_KINDS:
            continue
        # Line-bounded: code sharing a line with the symbol joins its body.
        span = rows[bisect_left(starts, symbol.start_line) : bisect_right(starts, symbol.end_line)]
        if span:
            out.append(_body(symbol, path, raw, span))
    return out


def bodies_for(
    sources: Mapping[str, bytes], symbols: Mapping[str, Iterable[Symbol]]
) -> list[SymbolBody]:
    """Every symbol body across the files in *symbols*, read from *sources*."""
    return [
        body
        for path, file_symbols in symbols.items()
        if path in sources
        for body in symbol_bodies(path, sources[path], file_symbols)
    ]


def build_move_map(
    base: Iterable[SymbolBody],
    head: Iterable[SymbolBody],
    *,
    rename_map: Mapping[str, str] | None = None,
) -> MoveMap:
    """Pair the base symbols that left their place with the head symbols that appeared."""
    renamed = dict(rename_map or {})
    based = [(b, replace(b, path=renamed.get(b.path, b.path))) for b in sorted(base, key=_order)]
    head_rows = sorted(head, key=_order)
    base_keys = Counter(on_head.key for _, on_head in based)
    head_keys = Counter(h.key for h in head_rows)
    # A key still present on the other side did not move, and one held twice
    # on its own side cannot name a single symbol.
    removed = [p for p in based if p[1].key not in head_keys and base_keys[p[1].key] == 1]
    added = [h for h in head_rows if h.key not in base_keys and head_keys[h.key] == 1]

    verbatim = _pair_unique(removed, added, lambda b: (b.name, b.digest))
    removed, added = _rest(removed, added, verbatim)
    sized = [h for h in added if h.tokens >= MIN_RENAMED_TOKENS]
    verbatim += _pair_unique(removed, sized, lambda b: b.digest)
    removed, added = _rest(removed, added, verbatim)
    edited = [
        (old, new)
        for old, new in _pair_unique(removed, added, lambda b: b.name)
        if _similar(old, new)
    ]
    by_new = {new.key: old.key for old, new in verbatim + edited}
    return MoveMap(
        MappingProxyType(by_new),
        frozenset(new.key for _, new in edited),
        MappingProxyType({new.key: new.span for _, new in verbatim + edited}),
        MappingProxyType({old.key: old.span for old, _ in verbatim + edited}),
    )


def _pair_unique(
    removed: list[tuple[SymbolBody, SymbolBody]],
    added: list[SymbolBody],
    key: Callable[[SymbolBody], Hashable],
) -> list[tuple[SymbolBody, SymbolBody]]:
    """``(old, new)`` pairs whose *key* occurs once on each side, across files."""
    removed_counts = Counter(key(on_head) for _, on_head in removed)
    added_counts = Counter(key(h) for h in added)
    added_by_key = {key(h): h for h in added}
    pairs = []
    for old, on_head in removed:
        k = key(on_head)
        if removed_counts[k] == 1 and added_counts[k] == 1 and on_head.path != added_by_key[k].path:
            pairs.append((old, added_by_key[k]))
    return pairs


def _rest(
    removed: list[tuple[SymbolBody, SymbolBody]],
    added: list[SymbolBody],
    pairs: list[tuple[SymbolBody, SymbolBody]],
) -> tuple[list[tuple[SymbolBody, SymbolBody]], list[SymbolBody]]:
    """What *pairs* left unclaimed on each side."""
    old_taken = {id(old) for old, _ in pairs}
    new_taken = {id(new) for _, new in pairs}
    return (
        [p for p in removed if id(p[0]) not in old_taken],
        [h for h in added if id(h) not in new_taken],
    )


def _similar(old: SymbolBody, new: SymbolBody) -> bool:
    """An edited move keeps most of its body; a same-named stranger does not."""
    small, large = sorted((old.tokens, new.tokens))
    if small < MIN_RENAMED_TOKENS or large > small * _MAX_EDITED_GROWTH:
        return False
    shared = len(old.pairs & new.pairs)
    return shared >= _MIN_EDITED_OVERLAP * max(len(old.pairs), len(new.pairs), 1)


def _within(span: tuple[int, int] | None, line: int | None) -> bool:
    return span is not None and line is not None and span[0] <= line <= span[1]


def _length(span: tuple[int, int]) -> int:
    return span[1] - span[0]


def _body(symbol: Symbol, path: str, raw: bytes, rows: list[TokenRow]) -> SymbolBody:
    name = symbol.name
    parts = [_NAME_MARK if row[5] == name else raw[row[3] : row[4]] for row in rows]
    digest = hashlib.blake2b(_SEPARATOR.join(parts), digest_size=16).hexdigest()
    pairs = frozenset(hash(pair) for pair in pairwise(parts))
    span = (symbol.start_line, symbol.end_line)
    return SymbolBody(path, name, digest, len(parts), pairs, span)


def _order(body: SymbolBody) -> tuple[str, str, str]:
    return (body.path, body.name, body.digest)
