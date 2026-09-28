"""A lazy relationship attribute read inside a loop over model rows.

Typed against the cross-file model index (:mod:`orm_models`): the loop's
iterable must resolve to one SQLAlchemy/Django model, and the accessed
attribute must be a ``lazy=True`` relation of it, not already eager-loaded on
the producer or a refinement. Python only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from ..complexity.ast_utils import _collect_function_nodes, _find_function_entry_name, _node_text
from ..complexity.models import PerfHit
from .dialects import PERF_DIALECTS
from .io_boundaries import collect_io_names
from .loop_facts import LoopFacts
from .orm_models import (
    ModelIndex,
    Relation,
    _call_args,
    _string_content,
    _target_name,
    build_model_index,
)
from .reduction_idioms import field_text, ident, walk

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from tree_sitter import Node

    from ..complexity.models import FileComplexity
    from ..source_reader import SourceReader
    from .dialects.base import BasePerfDialect

log = structlog.get_logger(__name__)

_KIND = "lazy_load_in_loop"
_ITER_HINTS: tuple[bytes, ...] = (b".query", b"query(", b".objects", b"select(", b"scalars(")
_SCOPES = frozenset({"function_definition", "lambda"})
_LOOP_KINDS = frozenset(
    {
        "for_statement", "list_comprehension", "set_comprehension",
        "dictionary_comprehension", "generator_expression",
    }
)
_SA_CHAIN_METHODS = frozenset(
    {
        "filter", "filter_by", "where", "order_by", "join", "outerjoin", "options", "limit",
        "offset", "distinct", "group_by", "having", "all", "yield_per", "execution_options",
        "params", "enable_eagerloads", "populate_existing", "with_for_update", "unique",
        "scalars", "select_from", "correlate", "paginate",
    }
)
_DJANGO_CHAIN_METHODS = frozenset(
    {
        "all", "filter", "exclude", "order_by", "select_related", "prefetch_related",
        "distinct", "annotate", "only", "defer", "using", "iterator", "reverse",
        "select_for_update",
    }
)
_DJANGO_WRITE_METHODS = frozenset(
    {
        "add", "remove", "set", "clear", "create", "get_or_create", "update_or_create",
        "update", "delete", "bulk_create",
    }
)
# Column options load nothing related, so they hide nothing from the eager check.
_SA_COLUMN_OPTIONS = frozenset(
    {"load_only", "defer", "undefer", "undefer_group", "with_expression"}
)
_SA_EAGER_FUNCS = frozenset(
    {
        "joinedload", "selectinload", "subqueryload", "contains_eager", "immediateload",
        "raiseload", "noload", "defaultload", "lazyload",
    }
)


@dataclass(frozen=True, slots=True)
class _FileScan:
    """Per-file facts every loop check in this file needs."""

    dialect: BasePerfDialect
    io_names: dict[str, str]
    has_db_import: bool
    index: ModelIndex


def collect_lazy_loads(
    walked: Iterable[tuple[Any, FileComplexity]],
    parsed_files: Iterable[Any],
    read_source: SourceReader | None = None,
) -> None:
    """Append hits to each walked file's ``perf_hits``; one file failing skips only it."""
    reader = read_source or _disk_read
    index = build_model_index(parsed_files, reader)
    for pf, fcx in walked:
        try:
            fcx.perf_hits.extend(_file_hits(pf, reader, index))
        except Exception as exc:
            log.debug("lazy_load_failed", path=pf.file_info.abs_path, error=str(exc))


def _disk_read(abs_path: str) -> bytes | None:
    try:
        return Path(abs_path).read_bytes()
    except OSError:
        return None


def _file_hits(pf: Any, read_source: SourceReader, index: ModelIndex) -> list[PerfHit]:
    info = pf.file_info
    if info.language != "python":
        return []
    source = read_source(info.abs_path)
    if source is None or not any(h in source for h in _ITER_HINTS):
        return []
    from ..dataflow.parsing import parse_source  # deferred: dataflow imports perf

    parsed = parse_source(info.abs_path, info.language, source)
    if parsed is None:
        return []
    root, lmap = parsed
    dialect = PERF_DIALECTS.get("python")
    if dialect is None:
        return []
    io_names = collect_io_names(root, "python")
    scan = _FileScan(
        dialect=dialect,
        io_names=io_names,
        has_db_import=any(v == "db" for v in io_names.values()),
        index=index,
    )
    functions = _collect_function_nodes(root, lmap)
    named = [(fn, _find_function_entry_name(fn, lmap)) for fn in functions]
    return [hit for fn, name in named for hit in _function_hits(fn, name, scan)]


def _function_hits(fn: Node, fn_name: str | None, scan: _FileScan) -> list[PerfHit]:
    if scan.dialect.is_async_fn(fn):
        return []
    body = fn.child_by_field_name("body")
    if body is None:
        return []
    hits: list[PerfHit] = []
    for loop in (n for n in walk(body, _SCOPES) if n.type in _LOOP_KINDS):
        hit = _loop_hit(loop, fn, fn_name, scan)
        if hit is not None:
            hits.append(hit)
    return hits


# -- loop header -----------------------------------------------------------------


def _for_target(node: Node) -> tuple[str, Node] | None:
    """``(target name, iterable expr)`` for a ``for_statement`` / ``for_in_clause``."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if right is None:
        return None
    name = ident(left)
    if name is not None:
        return name, right
    return _enumerate_target(left, right)


def _enumerate_target(left: Node | None, right: Node) -> tuple[str, Node] | None:
    """``for i, row in enumerate(xs):`` -> the second name and ``xs``."""
    names = _tuple_pattern_names(left)
    if names is None or right.type != "call" or not _is_enumerate_call(right):
        return None
    positional, _kw = _call_args(right)
    if len(positional) != 1:
        return None
    return names[1], positional[0]


def _tuple_pattern_names(left: Node | None) -> list[str] | None:
    """The two bound names of a ``pattern_list``/``tuple_pattern``, else ``None``."""
    if left is None or left.type not in ("pattern_list", "tuple_pattern"):
        return None
    names = [ident(c) for c in left.children if c.is_named]
    return names if len(names) == 2 and all(names) else None


def _is_enumerate_call(call: Node) -> bool:
    callee = call.child_by_field_name("function")
    return callee is not None and _node_text(callee) == "enumerate"


def _loop_target(loop: Node) -> tuple[str, Node] | None:
    if loop.type == "for_statement":
        return _for_target(loop)
    for_in = next((c for c in loop.children if c.type == "for_in_clause"), None)
    return _for_target(for_in) if for_in is not None else None


def _scan_scope(loop: Node) -> list[Node]:
    """Nodes to search for a lazy access: the ``for`` body, or a comprehension's
    element/key/value expressions and ``if`` clauses."""
    if loop.type == "for_statement":
        body = loop.child_by_field_name("body")
        return [body] if body is not None else []
    return [c for c in loop.children if c.is_named and c.type != "for_in_clause"]


def _target_reassigned(nodes: list[Node], target: str) -> bool:
    for root in nodes:
        for node in walk(root, _SCOPES):
            if node.type == "assignment" and ident(node.child_by_field_name("left")) == target:
                return True
            if node.type == "del_statement" and any(ident(c) == target for c in node.children):
                return True
            if node.type in ("for_statement", "for_in_clause") and target in _pattern_names(
                node.child_by_field_name("left")
            ):
                return True
    return False


# -- typing: resolving the iterable to one model -----------------------------------


def _single_model_arg(call: Node, index: ModelIndex) -> str | None:
    positional, _kwargs = _call_args(call)
    if len(positional) != 1:
        return None
    name = _target_name(positional[0])
    return name if name in index.models else None


def _match_select_chain(expr: Node, index: ModelIndex) -> str | None:
    """``select(M)<chain>`` -> ``M``, walking the SQLAlchemy chain allowlist."""
    if expr.type != "call":
        return None
    callee = expr.child_by_field_name("function")
    if callee is None:
        return None
    if callee.type == "identifier" and _node_text(callee) == "select":
        return _single_model_arg(expr, index)
    if callee.type == "attribute":
        method = field_text(callee, "attribute")
        receiver = callee.child_by_field_name("object")
        if receiver is not None and method in _SA_CHAIN_METHODS:
            return _match_select_chain(receiver, index)
    return None


def _select_model(arg: Node | None, fn: Node, index: ModelIndex) -> str | None:
    if arg is None:
        return None
    if arg.type == "identifier":
        rhs = _assignments_before(fn, _node_text(arg) or "", arg.start_byte)
        return _select_model(rhs[-1], fn, index) if rhs else None
    return _match_select_chain(arg, index)


def _match_producer(
    expr: Node | None,
    fn: Node,
    index: ModelIndex,
    self_name: str | None = None,
    self_value: tuple[str, str] | None = None,
) -> tuple[str, str] | None:
    """``(model, orm)`` if *expr* is a producer chain typed to one model, else ``None``."""
    if expr is None:
        return None
    if expr.type == "parenthesized_expression":
        inner = next((c for c in expr.children if c.is_named), None)
        return _match_producer(inner, fn, index, self_name, self_value)
    if expr.type == "identifier":
        return _match_self_producer(expr, self_name, self_value)
    if expr.type == "subscript":
        return _match_slice_producer(expr, fn, index, self_name, self_value)
    if expr.type == "attribute":
        return _match_attribute_producer(expr, fn, index, self_name, self_value)
    if expr.type == "call":
        return _match_call_producer(expr, fn, index, self_name, self_value)
    return None


def _match_self_producer(
    expr: Node, self_name: str | None, self_value: tuple[str, str] | None
) -> tuple[str, str] | None:
    if self_name is None or _node_text(expr) != self_name:
        return None
    return self_value


def _match_slice_producer(
    expr: Node,
    fn: Node,
    index: ModelIndex,
    self_name: str | None,
    self_value: tuple[str, str] | None,
) -> tuple[str, str] | None:
    piece = expr.child_by_field_name("subscript")
    value = expr.child_by_field_name("value")
    if piece is None or piece.type != "slice" or value is None:
        return None
    return _match_producer(value, fn, index, self_name, self_value)


def _match_attribute_producer(
    expr: Node,
    fn: Node,
    index: ModelIndex,
    self_name: str | None,
    self_value: tuple[str, str] | None,
) -> tuple[str, str] | None:
    method = field_text(expr, "attribute")
    obj = expr.child_by_field_name("object")
    if obj is None:
        return None
    if method == "query":
        model = ident(obj)
        return (model, "sqlalchemy") if model in index.models else None
    if method == "objects":
        model = ident(obj)
        typed = model in index.models and model not in index.custom_managers
        return (model, "django") if typed else None
    if method == "items" and obj.type == "call":
        inner_fn = obj.child_by_field_name("function")
        if inner_fn is not None and field_text(inner_fn, "attribute") == "paginate":
            return _match_producer(obj, fn, index, self_name, self_value)
    return None


def _match_call_producer(
    expr: Node,
    fn: Node,
    index: ModelIndex,
    self_name: str | None,
    self_value: tuple[str, str] | None,
) -> tuple[str, str] | None:
    callee = expr.child_by_field_name("function")
    if callee is None or callee.type != "attribute":
        return None
    method = field_text(callee, "attribute")
    receiver = callee.child_by_field_name("object")
    if method == "query":
        model = _single_model_arg(expr, index)
        return (model, "sqlalchemy") if model else None
    if method == "scalars":
        return _match_scalars_producer(expr, fn, index, receiver)
    if receiver is not None:
        return _match_chain_producer(method, receiver, fn, index, self_name, self_value)
    return None


def _match_scalars_producer(
    expr: Node, fn: Node, index: ModelIndex, receiver: Node | None
) -> tuple[str, str] | None:
    positional, _kw = _call_args(expr)
    if len(positional) == 1:
        model = _select_model(positional[0], fn, index)
        return (model, "sqlalchemy") if model else None
    if receiver is None or receiver.type != "call":
        return None
    inner_callee = receiver.child_by_field_name("function")
    if inner_callee is None or field_text(inner_callee, "attribute") != "execute":
        return None
    exec_pos, _ek = _call_args(receiver)
    model = _select_model(exec_pos[0], fn, index) if len(exec_pos) == 1 else None
    return (model, "sqlalchemy") if model else None


def _match_chain_producer(
    method: str,
    receiver: Node,
    fn: Node,
    index: ModelIndex,
    self_name: str | None,
    self_value: tuple[str, str] | None,
) -> tuple[str, str] | None:
    inner = _match_producer(receiver, fn, index, self_name, self_value)
    if inner is None:
        return None
    model, orm = inner
    allowed = _SA_CHAIN_METHODS if orm == "sqlalchemy" else _DJANGO_CHAIN_METHODS
    return (model, orm) if method in allowed else None


def _is_param(fn: Node, name: str) -> bool:
    params = fn.child_by_field_name("parameters")
    if params is None:
        return False
    for p in params.children:
        if not p.is_named:
            continue
        if p.type == "identifier" and _node_text(p) == name:
            return True
        nm = p.child_by_field_name("name") or next(
            (c for c in p.children if c.type == "identifier"), None
        )
        if nm is not None and _node_text(nm) == name:
            return True
    return False


def _pattern_names(node: Node | None) -> set[str]:
    if node is None:
        return set()
    return {ident(c) for c in walk(node) if ident(c) is not None}


def _rebinds(fn: Node, name: str, before: int) -> bool:
    """A parameter, an augmented assignment, or a ``for``/``with``/``except`` binding
    of *name* anywhere before *before* -> the name is not a clean producer chain."""
    for node in walk(fn, _SCOPES):
        if node.start_byte >= before:
            continue
        if node.type == "augmented_assignment" and ident(node.child_by_field_name("left")) == name:
            return True
        left = node.child_by_field_name("left") if node.type == "for_statement" else None
        if left is not None and name in _pattern_names(left):
            return True
        if node.type == "as_pattern":
            named = [c for c in node.children if c.is_named]
            target = named[-1] if len(named) >= 2 else None
            if name in _pattern_names(target):
                return True
    return False


def _assignments_before(fn: Node, name: str, before: int) -> list[Node]:
    out = [
        n.child_by_field_name("right")
        for n in walk(fn, _SCOPES)
        if n.type == "assignment"
        and n.start_byte < before
        and ident(n.child_by_field_name("left")) == name
    ]
    out = [n for n in out if n is not None]
    out.sort(key=lambda n: n.start_byte)
    return out


def _model_of_iterable(
    iterable: Node, loop: Node, fn: Node, index: ModelIndex
) -> tuple[str, str] | None:
    if iterable.type != "identifier":
        return _match_producer(iterable, fn, index)
    name = _node_text(iterable) or ""
    if _is_param(fn, name) or _rebinds(fn, name, loop.start_byte):
        return None
    current: tuple[str, str] | None = None
    for rhs in _assignments_before(fn, name, loop.start_byte):
        matched = _match_producer(rhs, fn, index, self_name=name, self_value=current)
        if matched is None or (current is not None and matched != current):
            return None
        current = matched
    return current


# -- eager check ------------------------------------------------------------------


def _last_segment(callee: Node) -> str | None:
    if callee.type == "identifier":
        return _node_text(callee)
    if callee.type == "attribute":
        return field_text(callee, "attribute")
    return None


def _walk_scan_scope(nodes: list[Node]) -> Iterable[Node]:
    """Flatten a scan scope's roots into one node stream."""
    for root in nodes:
        yield from walk(root, _SCOPES)


def _sa_eager_names(nodes: list[Node]) -> tuple[set[str], bool]:
    """Relations the loaders name, and whether an option hides what it loads (a helper
    call, a variable, ``"*"``): then nothing on these rows is reported."""
    names: set[str] = set()
    opaque = False
    for node in _walk_scan_scope(nodes):
        if node.type == "call":
            found_names, found_opaque = _sa_call_names(node)
            names |= found_names
            opaque |= found_opaque
    return names, opaque


def _sa_call_names(node: Node) -> tuple[set[str], bool]:
    """Names and an opaque flag from one call, if it is a loader or ``.options()``."""
    callee = node.child_by_field_name("function")
    fname = _last_segment(callee) if callee is not None else None
    if fname == "options":
        return set(), any(_opaque_option(arg) for arg in _call_args(node)[0])
    if fname not in _SA_EAGER_FUNCS:
        return set(), False
    names: set[str] = set()
    opaque = False
    for arg in _call_args(node)[0]:
        if arg.type == "attribute":
            names.add(field_text(arg, "attribute"))
        elif (text := _string_content(arg)) == "*":
            opaque = True
        elif text:
            names.update(text.split("."))
    return names, opaque


def _opaque_option(arg: Node) -> bool:
    """An ``.options()`` argument that is not a loader or column option call we can read."""
    callee = arg.child_by_field_name("function") if arg.type == "call" else None
    return callee is None or _last_segment(callee) not in _SA_EAGER_FUNCS | _SA_COLUMN_OPTIONS


def _django_eager_names(nodes: list[Node]) -> tuple[set[str], bool, bool]:
    """``(names, bare select_related(), opaque argument)``."""
    names: set[str] = set()
    bare = opaque = False
    for node in _walk_scan_scope(nodes):
        result = _django_call_names(node)
        if result is None:
            continue
        found_names, found_bare, found_opaque = result
        names |= found_names
        bare |= found_bare
        opaque |= found_opaque
    return names, bare, opaque


def _django_call_names(node: Node) -> tuple[set[str], bool, bool] | None:
    """``(names, bare select_related(), opaque)`` for one call, or ``None`` if not a loader."""
    callee = node.child_by_field_name("function") if node.type == "call" else None
    fname = _last_segment(callee) if callee is not None else None
    if fname not in ("select_related", "prefetch_related"):
        return None
    positional = _call_args(node)[0]
    bare = fname == "select_related" and not positional
    names: set[str] = set()
    opaque = False
    for arg in positional:
        found_names, found_opaque = _django_arg_names(arg)
        names |= found_names
        opaque |= found_opaque
    return names, bare, opaque


def _django_arg_names(arg: Node) -> tuple[set[str], bool]:
    """``(names, opaque)`` for one ``select_related``/``prefetch_related`` argument."""
    inner = arg.child_by_field_name("function") if arg.type == "call" else None
    if inner is not None and _last_segment(inner) == "Prefetch":
        arg = next(iter(_call_args(arg)[0]), arg)
    text = _string_content(arg)
    if text:
        return set(text.split("__")), False
    return set(), True


def _eager_check(orm: str, nodes: list[Node]) -> Callable[[str, str], bool]:
    """``(attr, kind) -> loaded with the rows?`` over the producer and its assignments."""
    if orm == "sqlalchemy":
        names, opaque = _sa_eager_names(nodes)
        return lambda attr, kind: opaque or attr in names
    names, bare, opaque = _django_eager_names(nodes)
    # A bare ``select_related()`` follows forward FK / O2O only; ``kind`` cannot tell a
    # reverse O2O from a forward one, so it is silenced too (recall only).
    return lambda attr, kind: opaque or attr in names or (bare and kind == "object")


# -- access + hit assembly ----------------------------------------------------------


def _is_write_target(node: Node) -> bool:
    parent = node.parent
    if parent is None:
        return False
    if parent.type == "assignment" and parent.child_by_field_name("left") == node:
        return True
    if parent.type == "del_statement":
        return any(c == node for c in parent.children if c.is_named)
    return False


def _writes_through_manager(node: Node) -> bool:
    """``row.rel.add(x)`` / ``row.rel.all().update(...)``: a Django manager used to write.
    The per-row query is a write, which no eager load removes."""
    cur = node
    while cur.parent is not None:
        parent = cur.parent
        if parent.type == "attribute" and parent.child_by_field_name("object") == cur:
            if field_text(parent, "attribute") in _DJANGO_WRITE_METHODS:
                return True
        elif not (parent.type == "call" and parent.child_by_field_name("function") == cur):
            return False
        cur = parent
    return False


def _is_lazy_access(
    node: Node,
    target: str,
    relations: dict[str, Relation],
    orm: str,
    loaded: Callable[[str, str], bool],
) -> str | None:
    """The relation name if *node* is an unresolved lazy access on *target*, else ``None``."""
    if node.type != "attribute":
        return None
    obj = node.child_by_field_name("object")
    if ident(obj) != target:
        return None
    attr = field_text(node, "attribute")
    relation = relations.get(attr)
    if relation is None or not relation.lazy or relation.orm != orm:
        return None
    if _is_write_target(node) or loaded(attr, relation.kind):
        return None
    manager = relation.orm == "django" and relation.kind == "collection"
    if manager and _writes_through_manager(node):
        return None
    return attr


def _find_access(
    nodes: list[Node],
    target: str,
    relations: dict[str, Relation],
    orm: str,
    loaded: Callable[[str, str], bool],
) -> tuple[Node, str] | None:
    best: tuple[Node, str, tuple[int, int]] | None = None
    for root in nodes:
        for node in walk(root, _SCOPES):
            attr = _is_lazy_access(node, target, relations, orm, loaded)
            if attr is None:
                continue
            pos = (node.start_point[0], node.start_point[1])
            if best is None or pos < best[2]:
                best = (node, attr, pos)
    return (best[0], best[1]) if best is not None else None


def _fix_text(model: str, attr: str, relation: Relation) -> tuple[str, str]:
    subject = f"{model}.{attr}"
    if relation.orm == "sqlalchemy":
        return subject, f"selectinload({subject})"
    if relation.kind == "object":
        return subject, f'select_related("{attr}")'
    return subject, f'prefetch_related("{attr}")'


def _loop_facts(loop: Node, scan: _FileScan) -> LoopFacts | None:
    def probe(call: Node) -> str | None:
        return scan.dialect.call_sink_kind(
            call,
            awaited=scan.dialect.is_awaited(call),
            io_names=scan.io_names,
            has_db_import=scan.has_db_import,
        )

    facts = LoopFacts(
        chunked=scan.dialect.is_chunked_loop(loop),
        magnitude=scan.dialect.loop_magnitude(loop, probe) or "unknown",
    )
    return None if facts == LoopFacts() else facts


def _eager_candidate_nodes(iterable_expr: Node, fn: Node, loop: Node) -> list[Node]:
    """The producer, and every assignment before the loop to a name it reads, transitively:
    the rows' own refinements and a ``stmt = select(M).options(...)`` behind ``scalars``."""
    eager_nodes = [iterable_expr]
    seen: set[str] = set()
    for node in eager_nodes:
        for name in _pattern_names(node) - seen:
            seen.add(name)
            eager_nodes += _assignments_before(fn, name, loop.start_byte)
    return eager_nodes


def _loop_hit(
    loop: Node,
    fn: Node,
    fn_name: str | None,
    scan: _FileScan,
) -> PerfHit | None:
    is_for = loop.type == "for_statement"
    if is_for and scan.dialect.is_constant_loop(loop):
        return None
    target_info = _loop_target(loop)
    if target_info is None:
        return None
    target, iterable_expr = target_info
    typed = _model_of_iterable(iterable_expr, loop, fn, scan.index)
    if typed is None:
        return None
    model, orm = typed
    relations = scan.index.relations.get(model, {})
    if not relations:
        return None
    scan_nodes = _scan_scope(loop)
    if not scan_nodes or _target_reassigned(scan_nodes, target):
        return None
    eager_nodes = _eager_candidate_nodes(iterable_expr, fn, loop)
    access = _find_access(scan_nodes, target, relations, orm, _eager_check(orm, eager_nodes))
    if access is None:
        return None
    node, attr = access
    relation = relations[attr]
    subject, fix = _fix_text(model, attr, relation)
    loop_facts = _loop_facts(loop, scan) if is_for else None
    return PerfHit(
        _KIND,
        node.start_point[0] + 1,
        fn_name,
        "db",
        func_start=fn.start_point[0] + 1,
        path=(subject, fix),
        loop=loop_facts,
    )


__all__ = ["collect_lazy_loads"]
