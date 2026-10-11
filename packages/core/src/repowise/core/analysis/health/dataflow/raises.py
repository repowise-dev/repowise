"""Error exits of a function and the handlers that catch them.

Recorded by the CFG builder as it walks each statement (:mod:`cfg`), so no
extra tree walk: per ``try`` its clauses and handlers (:class:`TryRegion`),
per error exit its line, kind, exception and the handler that catches it
(:class:`RaiseSite`). Kinds: ``raise`` (``raise`` / ``throw``), ``rethrow``
(a bare ``raise`` / ``throw;``, or raising the handler's own name),
``panic`` (Go ``panic(...)``, Rust ``panic!`` and kin) and ``error_return``
(Rust ``?``, ``return Err(...)``, ``bail!``). Go's ``return ..., err`` is
not one: nothing in the syntax marks the value as an error.

A handler catches a raise when it names no type, names the raised type (by
last name segment), or names a root type that catches every ordinary
exception. Subclass relations are not known, so a handler naming a base class
of the raised type is missed and the raise reads as propagating.

:func:`exception_flow_refusal` is the Extract Method precondition built on
these facts: a span may not change which handler sees an exception or what
state it sees.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap
    from .cfg import CFG

# Why a span or stage was not offered (``StagePlan.refused``).
TRY_SPLIT = "try_split"
EXCEPTION_STATE = "exception_state"
DEFER_MOVED = "defer_moved"

# Handler types that catch every ordinary exception. Python's ``Exception``
# does not catch the interpreter's exit signals, ``BaseException`` does.
_ROOTS = frozenset({"Exception", "Throwable", "exception"})
_EXIT_SIGNALS = frozenset({"SystemExit", "KeyboardInterrupt", "GeneratorExit"})
_PANIC_MACROS = frozenset({"panic", "unreachable", "todo", "unimplemented"})


@dataclass(frozen=True)
class Handler:
    """One ``except`` / ``catch`` clause. ``types`` None: it catches
    everything. ``completes``: some path leaves it normally (it swallows)."""

    start_line: int
    end_line: int
    types: tuple[str, ...] | None
    name: str | None
    completes: bool = True


@dataclass(frozen=True)
class TryRegion:
    """One ``try`` statement: its lines, its body's, each clause's (body,
    ``else``, handlers, ``finally``) and its handlers."""

    start_line: int
    end_line: int
    body: tuple[int, int]
    clauses: tuple[tuple[int, int], ...]
    handlers: tuple[Handler, ...]
    finally_lines: tuple[int, int] | None = None


@dataclass(frozen=True)
class RaiseSite:
    """One error exit. ``exception`` is the raised type or value as written
    (None when unknown); ``caught_by`` the line of the handler in this
    function that catches it, None when it leaves the function."""

    line: int
    kind: str
    exception: str | None
    caught_by: int | None


def _text(node: Node | None) -> str | None:
    return (node.text or b"").decode("utf-8", "replace") if node is not None else None


def _segment(name: str) -> str:
    """``errors.NotFound`` / ``std::runtime_error`` / ``List<X>`` -> last name."""
    return name.split("<", 1)[0].replace("::", ".").rsplit(".", 1)[-1].strip(" &*")


def handler_head(node: Node) -> tuple[tuple[str, ...] | None, str | None]:
    """The types a handler clause names (None: all) and the name it binds."""
    value = node.child_by_field_name("value")  # Python ``except T as e``
    if value is not None:
        alias = value.child_by_field_name("alias") if value.type == "as_pattern" else None
        if value.type == "as_pattern":
            value = value.named_children[0]
        grouped = value.type in ("tuple", "parenthesized_expression")
        names = value.named_children if grouped else [value]
        return tuple(_text(n) or "" for n in names), _text(alias)
    for child in node.named_children:
        if child.type == "catch_formal_parameter":  # Java
            kinds = next((c for c in child.named_children if c.type == "catch_type"), None)
            types = tuple(_text(t) or "" for t in kinds.named_children) if kinds else None
            return types, _text(child.child_by_field_name("name"))
        if child.type == "catch_declaration":  # C#
            t = child.child_by_field_name("type")
            return ((_text(t) or "",) if t else None), _text(child.child_by_field_name("name"))
        if child.type == "parameter_list":  # C++ (``...`` names no declaration)
            decl = next(
                (c for c in child.named_children if c.type == "parameter_declaration"), None
            )
            t = decl.child_by_field_name("type") if decl is not None else None
            return ((_text(t) or "",) if t else None), None
    param = node.child_by_field_name("parameter")  # TS / JS: catches everything
    return None, _text(param) if param is not None and param.type == "identifier" else None


def catches(types: tuple[str, ...] | None, raised: tuple[str, ...] | None) -> bool:
    """Whether a handler naming *types* catches an exception of *raised*
    (None: unknown, a value rather than a class)."""
    if types is None:
        return True
    names = {_segment(t) for t in types}
    if "BaseException" in names:
        return True
    if raised is None:
        return bool(names & _ROOTS)
    segs = {_segment(r) for r in raised}
    return bool(names & segs) or (bool(names & _ROOTS) and not segs & _EXIT_SIGNALS)


def raised_exception(node: Node) -> str | None:
    """What a ``raise`` / ``throw`` statement raises, as written: the class
    of a construction, else the expression; None for a bare re-raise."""
    value = next((c for c in node.named_children if "comment" not in c.type), None)
    if value is None:
        return None
    for field in ("constructor", "type", "function"):
        inner = value.child_by_field_name(field)
        if inner is not None:
            return _text(inner)
    return _text(value)


def exit_kind(node: Node, lmap_returns: frozenset[str]) -> str | None:
    """The error-exit kind of a statement that is not a ``raise``: a panic or
    an explicit error return; None for any other statement."""
    if node.type in lmap_returns:
        value = next(iter(node.named_children), None)
        fn = value.child_by_field_name("function") if value is not None else None
        return "error_return" if fn is not None and _text(fn) == "Err" else None
    if node.type == "expression_statement" and node.named_child_count == 1:
        node = node.named_children[0]
    if node.type == "call_expression":
        fn = node.child_by_field_name("function")
        named = fn is not None and fn.type == "identifier" and _text(fn) == "panic"
        return "panic" if named else None
    if node.type == "macro_invocation":
        name = _segment(_text(node.child_by_field_name("macro")) or "")
        return "panic" if name in _PANIC_MACROS else "error_return" if name == "bail" else None
    return None


def error_exit(
    st: Node, lmap: LanguageNodeMap, handler: Handler | None
) -> tuple[str, str | None, tuple[str, ...] | None] | None:
    """``(kind, exception, raised types)`` of *st* when it is an error exit,
    *handler* being the clause it sits in; None otherwise. A raised value
    (lowercase, not dotted) has unknown types."""
    if st.type == "try_expression":
        return "error_return", None, None
    if st.type not in lmap.raise_kinds:
        kind = exit_kind(st, lmap.return_kinds)
        return (kind, None, None) if kind is not None else None
    exc = raised_exception(st)
    if exc is None or (handler is not None and exc == handler.name):
        types = handler.types if handler is not None else None
        return "rethrow", " | ".join(types) if types else None, types
    known = exc[:1].isupper() or "." in exc or ":" in exc
    return "raise", exc, (exc,) if known else None


def raises_in(cfg: CFG, start: int, end: int) -> tuple[RaiseSite, ...]:
    """The error exits on lines *start*..*end*."""
    return tuple(r for r in cfg.raises if start <= r.line <= end)


def exception_flow_refusal(
    cfg: CFG,
    s: int,
    e: int,
    reads: dict[str, list[tuple[int, frozenset[int]]]],
    def_lines: dict[str, list[int]],
) -> str | None:
    """Why lifting lines *s*..*e* into a helper would change exception flow,
    or None. :data:`TRY_SPLIT`: the span cuts a ``try`` apart from a handler
    or ``finally``, or re-raises outside the handler it sits in.
    :data:`EXCEPTION_STATE`: the span sits in a ``try`` body and writes a
    value that a handler or ``finally`` of that ``try`` (or code after it,
    past a handler that swallows the error) may read; when the helper raises,
    its writes are lost, so that code would see the value from before. A
    raise that only propagates is unchanged: the helper re-raises it.
    :data:`DEFER_MOVED`: the span holds a Go ``defer``, which in a helper runs
    when the helper returns and recovers only the helper's panics."""
    if any(s <= line <= e for line in cfg.defers):
        return DEFER_MOVED
    rethrows = [r.line for r in raises_in(cfg, s, e) if r.kind == "rethrow"]
    if any(not _inside(_handler_of(cfg, line), s, e) for line in rethrows):
        return TRY_SPLIT
    for region in cfg.try_regions:
        if region.end_line < s or e < region.start_line:
            continue
        if s <= region.start_line and region.end_line <= e:
            continue
        if not any(lo <= s and e <= hi for lo, hi in region.clauses):
            return TRY_SPLIT
        lo, hi = region.body
        if lo <= s and e <= hi and _state_seen(cfg, region, s, e, reads, def_lines):
            return EXCEPTION_STATE
    return None


def _handler_of(cfg: CFG, line: int) -> tuple[int, int] | None:
    """The lines of the innermost handler holding *line*."""
    held = [
        (h.start_line, h.end_line)
        for region in cfg.try_regions
        for h in region.handlers
        if h.start_line <= line <= h.end_line
    ]
    return max(held, default=None)


def _inside(lines: tuple[int, int] | None, s: int, e: int) -> bool:
    return lines is None or (s <= lines[0] and lines[1] <= e)


def _state_seen(
    cfg: CFG,
    region: TryRegion,
    s: int,
    e: int,
    reads: dict[str, list[tuple[int, frozenset[int]]]],
    def_lines: dict[str, list[int]],
) -> bool:
    """Whether a write on lines *s*..*e* that code in the span may follow
    (and so raise after) is read on *region*'s exception path."""
    clauses = [(h.start_line, h.end_line) for h in region.handlers]
    if region.finally_lines is not None:
        clauses.append(region.finally_lines)
    for var, entries in reads.items():
        for line, seen in entries:
            exposed = any(lo <= line <= hi for lo, hi in clauses) or (
                line > region.end_line and _swallowed(region, def_lines.get(var, ()))
            )
            if exposed and any(s <= d <= e and _followed(cfg, d, s, e) for d in seen):
                return True
    return False


def _swallowed(region: TryRegion, defs: list[int]) -> bool:
    """Whether a handler of *region* completes without writing the name."""
    return any(
        h.completes and not any(h.start_line <= d <= h.end_line for d in defs)
        for h in region.handlers
    )


def _followed(cfg: CFG, line: int, s: int, e: int) -> bool:
    """Whether some path runs another statement of lines *s*..*e* after the
    statement writing on *line* (in the flow graph), before leaving them."""
    at = cfg.statement_at(line)
    if at is None:
        return True
    block, k = at
    rest = cfg.block(block).statements[k + 1 :]
    if rest:
        return s <= rest[0].start_line <= e
    seen: set[int] = set()
    stack = list(cfg.block(block).successors)
    while stack:
        bid = stack.pop()
        if bid in seen or bid == cfg.exit_id:
            continue
        seen.add(bid)
        nxt = cfg.block(bid)
        if not nxt.statements:
            stack.extend(nxt.successors)
        elif s <= nxt.statements[0].start_line <= e:
            return True
    return False


__all__ = [
    "DEFER_MOVED",
    "EXCEPTION_STATE",
    "TRY_SPLIT",
    "Handler",
    "RaiseSite",
    "TryRegion",
    "catches",
    "exception_flow_refusal",
    "handler_head",
    "raises_in",
]
