"""Error exits of a function and the handlers that catch them.

Recorded by the CFG builder as it walks each statement (:mod:`cfg`), so no
extra tree walk: per ``try`` its clauses and handlers (:class:`TryRegion`),
per error exit its line, kind, exception and the handler that catches it
(:class:`RaiseSite`). Kinds: ``raise`` (``raise`` / ``throw``), ``rethrow``
(a bare ``raise`` / ``throw;``, or raising the handler's own name),
``panic`` (Go ``panic(...)``, Rust ``panic!`` and kin) and ``error_return``
(Rust ``?``, ``return Err(...)``, ``bail!``). Go's ``return ..., err`` is
not one: nothing in the syntax marks the value as an error.

``caught_by`` is best effort. A handler catches a raise when it names no
type, names the raised type (by last name segment), or names a root type that
catches every ordinary exception. Subclass relations are not known, so a
handler naming a base class of the raised type is missed and the raise reads
as propagating; a filtered handler (C# ``catch (E) when (...)``) may decline,
so it is never counted as catching. What a handler names comes from the
language's dialect (``handler_head``).

:func:`exception_flow_refusal` is the Extract Method precondition built on
these facts: a span may not change which handler sees an exception or what
state it sees.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..complexity.error_handling import PY_CATCH_ALL, RUST_PANIC_MACROS
from .dialects.base import node_text

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap
    from .cfg import CFG

# Why a span or stage was not offered (``StagePlan.refused``).
TRY_SPLIT = "try_split"
EXCEPTION_STATE = "exception_state"
DEFER_MOVED = "defer_moved"

# Handler types that catch every ordinary exception (Python / C# ``Exception``,
# Java ``Throwable``, C++ ``std::exception``). Python's ``Exception`` does not
# catch the interpreter's exit signals; ``BaseException`` catches everything.
_ROOTS = frozenset({PY_CATCH_ALL[0], "Throwable", "exception"})
_EXIT_SIGNALS = frozenset({"SystemExit", "KeyboardInterrupt", "GeneratorExit"})


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

    def exposed_to(self, s: int, e: int) -> tuple[list[tuple[int, int]], bool] | None:
        """Where an exception raised in lines *s*..*e* is handled inside this
        ``try``: the clauses that run next, and whether a handler that
        swallows it lets the code after the ``try`` run. None when the lines
        are not inside one clause of it. From the body: the handlers, the
        ``finally``, and past a swallowing handler. From a handler or
        ``else``: the ``finally``. From the ``finally``: nothing here."""
        clause = next(((lo, hi) for lo, hi in self.clauses if lo <= s and e <= hi), None)
        if clause is None:
            return None
        after_finally = [self.finally_lines] if self.finally_lines is not None else []
        if clause == self.body:
            return [(h.start_line, h.end_line) for h in self.handlers] + after_finally, True
        return ([] if clause == self.finally_lines else after_finally), False


@dataclass(frozen=True)
class RaiseSite:
    """One error exit. ``exception`` is the raised type or value as written
    (None when unknown); ``caught_by`` the line of the handler in this
    function that catches it, None when it leaves the function."""

    line: int
    kind: str
    exception: str | None
    caught_by: int | None


def _segment(name: str) -> str:
    """``errors.NotFound`` / ``std::runtime_error`` / ``List<X>`` -> last name."""
    return name.split("<", 1)[0].replace("::", ".").rsplit(".", 1)[-1].strip(" &*")


def catches(types: tuple[str, ...] | None, raised: tuple[str, ...] | None) -> bool:
    """Whether a handler naming *types* catches an exception of *raised*
    (None: unknown, a value rather than a class). Best effort: no subclass
    relations, and a filtered handler (``types == ()``) never catches."""
    if types is None:
        return True
    names = {_segment(t) for t in types}
    if PY_CATCH_ALL[1] in names:
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
            return node_text(inner)
    return node_text(value)


def exit_kind(node: Node, lmap: LanguageNodeMap) -> str | None:
    """The error-exit kind of a statement that is not a ``raise``: a panic or
    an explicit error return (``return Err(...)``); None for any other."""
    if node.type in lmap.return_kinds:
        value = next(iter(node.named_children), None)
        fn = value.child_by_field_name("function") if value is not None else None
        return "error_return" if fn is not None and node_text(fn) == "Err" else None
    if node.type == "expression_statement" and node.named_child_count == 1:
        node = node.named_children[0]
    if node.type == "call_expression":  # Go ``panic(...)``
        fn = node.child_by_field_name("function")
        return "panic" if fn is not None and node_text(fn) == "panic" else None
    if node.type in lmap.exit_macro_kinds:
        return _macro_exit(node, lmap)
    return None


def _macro_exit(node: Node, lmap: LanguageNodeMap) -> str | None:
    """Rust: a panic-family macro, or one that returns an error (``bail!``)."""
    macro = node.child_by_field_name("macro")
    name = _segment(node_text(macro)) if macro is not None else ""
    if name in RUST_PANIC_MACROS:
        return "panic"
    return "error_return" if name in lmap.exit_macro_names else None


def error_exit(
    st: Node, lmap: LanguageNodeMap, handler: Handler | None
) -> tuple[str, str | None, tuple[str, ...] | None] | None:
    """``(kind, exception, raised types)`` of *st* when it is an error exit,
    *handler* being the clause it sits in; None otherwise. A raised value
    (lowercase, not dotted) has unknown types."""
    if st.type == "try_expression":
        return "error_return", None, None
    if st.type not in lmap.raise_kinds:
        kind = exit_kind(st, lmap)
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
    or None. :data:`DEFER_MOVED`: the lines hold a Go ``defer``, which in a
    helper runs when the helper returns and recovers only its panics.
    :data:`TRY_SPLIT`: they cut a ``try`` apart from a handler or ``finally``,
    or re-raise outside the handler they sit in. :data:`EXCEPTION_STATE`: they
    write a value that code an exception from them reaches may read (a
    handler, a ``finally``, or the code after a ``try`` whose handler swallows
    it, at any nesting depth); a helper that raises loses its writes, so that
    code would see the value from before. A raise that only propagates is
    unchanged: the helper re-raises it."""
    if any(s <= line <= e for line in cfg.defers):
        return DEFER_MOVED
    if _splits_a_try(cfg, s, e):
        return TRY_SPLIT
    for region in cfg.try_regions:
        if not region.start_line <= s <= e <= region.end_line or (
            s == region.start_line and e == region.end_line
        ):
            continue
        exposed = region.exposed_to(s, e)
        if exposed is not None and _state_seen(cfg, region, exposed, s, e, reads, def_lines):
            return EXCEPTION_STATE
    return None


def _splits_a_try(cfg: CFG, s: int, e: int) -> bool:
    """Whether lines *s*..*e* re-raise outside their handler, or hold part of
    a ``try`` but not one whole clause of it."""
    rethrows = [r.line for r in raises_in(cfg, s, e) if r.kind == "rethrow"]
    if any(not _inside(_handler_of(cfg, line), s, e) for line in rethrows):
        return True
    return any(
        not (region.end_line < s or e < region.start_line)
        and not (s <= region.start_line and region.end_line <= e)
        and region.exposed_to(s, e) is None
        for region in cfg.try_regions
    )


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
    exposed: tuple[list[tuple[int, int]], bool],
    s: int,
    e: int,
    reads: dict[str, list[tuple[int, frozenset[int]]]],
    def_lines: dict[str, list[int]],
) -> bool:
    """Whether a write on lines *s*..*e* that code in them may follow (and so
    raise after) is read where *exposed* (:meth:`TryRegion.exposed_to`) says
    an exception from them is handled in *region*."""
    clauses, past = exposed
    for var, entries in reads.items():
        for line, seen in entries:
            hit = any(lo <= line <= hi for lo, hi in clauses) or (
                past and line > region.end_line and _swallowed(region, def_lines.get(var, ()))
            )
            if hit and any(s <= d <= e and _followed(cfg, d, s, e) for d in seen):
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
    "raises_in",
]
