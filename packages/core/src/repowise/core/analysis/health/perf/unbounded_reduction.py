"""An unbounded read whose rows are then reduced to one per key in code.

A query with no ``limit``/``range``/``single`` in its chain, run once, whose
result a loop deduplicates per key, pays to transfer every row to keep a
handful. The loop may sit in the same function or in one same-file helper the
result is passed to. Python only: the dialect names the bounding methods, and
every other dialect names none.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from ..complexity.ast_utils import _collect_function_nodes, _find_function_entry_name
from ..complexity.models import PerfHit
from .dialects import PERF_DIALECTS
from .io_boundaries import collect_io_names
from .reduction_idioms import field_text, ident, reduces_per_key, walk

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tree_sitter import Node

    from ..complexity.models import FileComplexity
    from ..source_reader import SourceReader
    from .dialects.base import BasePerfDialect

log = structlog.get_logger(__name__)

_KIND = "unbounded_read_reduced_in_memory"
_LOOPS = ("for_statement", "while_statement")
# Accessors that unwrap a result into its rows (``res.data``, ``res.scalars().all()``).
_RESULT_ACCESSORS = frozenset({"data", "scalars", "all", "fetchall"})


@dataclass(frozen=True, slots=True)
class _FileScan:
    dialect: BasePerfDialect
    io_names: dict[str, str]
    bound_methods: frozenset[str]
    functions: dict[str, Node]
    scopes: frozenset[str]

    def is_db_read(self, call: Node) -> bool:
        # The name lexicon, not ``call_sink_kind``: a ``session.get`` reads one row.
        return (
            self.dialect.sink_kind(
                self.dialect.callee_root_name(call) or "",
                self.dialect.callee_method_name(call) or "",
                awaited=False,
                is_attribute=self.dialect.callee_is_attribute(call),
                io_names=self.io_names,
                has_db_import="db" in self.io_names.values(),
            )
            == "db"
        )

    def is_bounded(self, stmt: Node) -> bool:
        chain = stmt.child_by_field_name("right")
        return chain is not None and any(
            n.type == "call" and self.dialect.callee_method_name(n) in self.bound_methods
            for n in walk(chain)
        )


def collect_unbounded_reductions(
    walked: Iterable[tuple[Any, FileComplexity]],
    read_source: SourceReader | None = None,
) -> None:
    """Append hits to each file's ``perf_hits``; one file failing skips only it."""
    reader = read_source or _disk_read
    for pf, fcx in walked:
        try:
            fcx.perf_hits.extend(_file_hits(pf, reader))
        except Exception as exc:
            log.debug("unbounded_reduction_failed", path=pf.file_info.abs_path, error=str(exc))


def _disk_read(abs_path: str) -> bytes | None:
    try:
        return Path(abs_path).read_bytes()
    except OSError:
        return None


def _file_hits(pf: Any, read_source: SourceReader) -> list[PerfHit]:
    language = pf.file_info.language
    dialect = PERF_DIALECTS.get(language)
    bound_methods = dialect.unbounded_read_bound_methods() if dialect else frozenset()
    source = read_source(pf.file_info.abs_path) if bound_methods else None
    if dialect is None or source is None:
        return []
    from ..dataflow.parsing import parse_source  # deferred: dataflow imports perf

    parsed = parse_source(pf.file_info.abs_path, language, source)
    if parsed is None:
        return []
    root, lmap = parsed
    named = [(fn, _find_function_entry_name(fn, lmap)) for fn in _collect_function_nodes(root, lmap)]
    scan = _FileScan(
        dialect=dialect,
        io_names=collect_io_names(root, language),
        bound_methods=bound_methods,
        functions={name: fn for fn, name in reversed(named) if name},
        scopes=lmap.function_kinds | lmap.lambda_kinds,
    )
    return [hit for fn, name in named for hit in _function_hits(fn, name, scan)]


def _function_hits(fn: Node, name: str | None, scan: _FileScan) -> list[PerfHit]:
    body = fn.child_by_field_name("body")
    if body is None:
        return []
    hits: list[PerfHit] = []
    seen: set[tuple[int, int]] = set()
    for call in walk(body, scan.scopes):
        if call.type != "call" or not scan.is_db_read(call):
            continue
        site = _read_site(call, fn)
        # A chain holds several sink calls; its statement is the unit.
        if site is None or (site[0].start_byte, site[0].end_byte) in seen:
            continue
        stmt, var = site
        seen.add((stmt.start_byte, stmt.end_byte))
        reducer = None if scan.is_bounded(stmt) else _reducer(stmt, var, fn, scan)
        if reducer is not None:
            hits.append(
                PerfHit(
                    _KIND,
                    stmt.start_point[0] + 1,
                    name,
                    "db",
                    func_start=fn.start_point[0] + 1,
                    path=(reducer,) if reducer else (),
                )
            )
    return hits


def _in_loop_body(node: Node, fn: Node) -> bool:
    cur = node
    while cur.parent is not None and cur != fn:
        if cur.parent.type in _LOOPS and cur == cur.parent.child_by_field_name("body"):
            return True
        cur = cur.parent
    return False


def _read_site(call: Node, fn: Node) -> tuple[Node, str | None] | None:
    """The statement a read runs in once: a loop header, or ``name = read``."""
    if _in_loop_body(call, fn):
        return None
    cur = call
    while cur.parent is not None and cur != fn:
        parent = cur.parent
        if cur == parent.child_by_field_name("right"):
            if parent.type == "for_statement":
                return parent, None
            if parent.type == "assignment":
                name = ident(parent.child_by_field_name("left"))
                return (parent, name) if name else None
        cur = parent
    return None


def _reducer(stmt: Node, var: str | None, fn: Node, scan: _FileScan) -> str | None:
    """``""`` when *fn* reduces the rows itself, a helper's name, or ``None``."""
    if var is None:
        return "" if reduces_per_key(stmt) else None
    if _rebound_after(fn, var, stmt.end_byte, scan.scopes):
        return None
    loop = _consuming_loop(fn, var, scan.scopes)
    if loop is not None and reduces_per_key(loop):
        return ""
    return _reducing_helper(fn, var, scan)


def _rebound_after(fn: Node, var: str, after: int, scopes: frozenset[str]) -> bool:
    """A name reused for other data later must not read as the query result."""
    return any(
        n.type == "assignment"
        and n.start_byte > after
        and ident(n.child_by_field_name("left")) == var
        for n in walk(fn, scopes)
    )


def _unwrap(node: Node) -> Node:
    """Peel parentheses and ``rows or []`` down to the rows."""
    while node.type in ("parenthesized_expression", "boolean_operator"):
        inner = (
            node.child_by_field_name("left")
            if node.type == "boolean_operator"
            else next((c for c in node.children if c.is_named), None)
        )
        if inner is None:
            break
        node = inner
    return node


def _is_rows_of(node: Node, var: str) -> bool:
    """``var``, ``var.data``, ``var.scalars().all()`` and similar."""
    node = _unwrap(node)
    while ident(node) is None:
        target = node.child_by_field_name("function") if node.type == "call" else node
        if target is None or target.type != "attribute":
            return False
        if field_text(target, "attribute") not in _RESULT_ACCESSORS:
            return False
        inner = target.child_by_field_name("object")
        if inner is None:
            return False
        node = inner
    return ident(node) == var


def _consuming_loop(fn: Node, var: str, scopes: frozenset[str]) -> Node | None:
    return next(
        (
            n
            for n in walk(fn, scopes)
            if n.type == "for_statement"
            and (iterable := n.child_by_field_name("right")) is not None
            and _is_rows_of(iterable, var)
            and not _in_loop_body(n, fn)
        ),
        None,
    )


def _param_names(fn: Node) -> list[str]:
    params = fn.child_by_field_name("parameters")
    names = []
    for param in params.children if params is not None else ():
        name = ident(param) or ident(param.child_by_field_name("name"))
        name = name or next(filter(None, (ident(c) for c in param.children)), None)
        if param.is_named and name:
            names.append(name)
    return names


def _bound_param(args: Node, var: str, helper: Node) -> str | None:
    """The helper parameter that receives *var*'s rows at this call."""
    params = _param_names(helper)
    position = 0
    for arg in (a for a in args.children if a.is_named):
        if arg.type == "keyword_argument":
            value = arg.child_by_field_name("value")
            if value is not None and _is_rows_of(value, var):
                return field_text(arg, "name") or None
            continue
        if _is_rows_of(arg, var):
            return params[position] if position < len(params) else None
        position += 1
    return None


def _reducing_helper(fn: Node, var: str, scan: _FileScan) -> str | None:
    for call in walk(fn, scan.scopes):
        name = ident(call.child_by_field_name("function")) if call.type == "call" else None
        helper = scan.functions.get(name) if name else None
        args = call.child_by_field_name("arguments")
        if helper is None or helper == fn or args is None:
            continue
        param = _bound_param(args, var, helper)
        loop = _consuming_loop(helper, param, scan.scopes) if param else None
        if loop is not None and reduces_per_key(loop):
            return name
    return None


__all__ = ["collect_unbounded_reductions"]
