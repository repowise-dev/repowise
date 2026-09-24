"""Cross-file model index: SQLAlchemy relationships + Django FK/O2O/M2M.

Built once per :func:`..lazy_load.collect_lazy_loads` call from every parsed
Python file of the repo (not only the changed ones an incremental update
walks), because a lazy access in file A can only be typed against a model
declared in file B. Byte-prefiltered so a repo with no ORM pays nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..complexity.ast_utils import _node_text
from .reduction_idioms import field_text, ident, walk

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tree_sitter import Node

    from ..source_reader import SourceReader

# Byte prefilter: only a file mentioning one of these is worth parsing.
_RELATION_HINTS: tuple[bytes, ...] = (
    b"relationship(",
    b"ForeignKey(",
    b"OneToOneField(",
    b"ManyToManyField(",
    b"declared_attr",
)

# A manager or queryset class that eager-loads is found by these, even with no relation.
_MANAGER_HINTS: tuple[bytes, ...] = (b"get_queryset",)
_EAGER_QUERYSET_CALLS = frozenset({"select_related", "prefetch_related"})

_SA_LAZY_TRUE_STRINGS = frozenset({"select", "dynamic"})
_SA_LAZY_FALSE_STRINGS = frozenset(
    {"joined", "selectin", "subquery", "immediate", "raise", "raise_on_sql", "noload", "write_only"}
)
_MAPPED_LIST_HEADS = frozenset({"list", "List", "set"})
_DJANGO_FORWARD_KIND = {
    "ForeignKey": "object",
    "OneToOneField": "object",
    "ManyToManyField": "collection",
}
_DECLARED_ATTR_HINT = "declared_attr"


@dataclass(frozen=True, slots=True)
class Relation:
    target: str | None
    orm: str  # "sqlalchemy" | "django"
    kind: str  # "object" | "collection"
    lazy: bool


@dataclass(frozen=True, slots=True)
class ModelIndex:
    relations: dict[str, dict[str, Relation]]
    models: frozenset[str]
    # Models whose ``objects`` manager (or queryset) class, defined in this repo, has a
    # ``get_queryset`` that select_related / prefetch_related: the caller's chain hides it.
    custom_managers: frozenset[str] = frozenset()


@dataclass
class _RawClass:
    relations: dict[str, Relation] = field(default_factory=dict)
    bases: list[str] = field(default_factory=list)
    manager_names: set[str] = field(default_factory=set)
    eager_queryset: bool = False


# -- literal readers -----------------------------------------------------------


def _string_content(node: Node | None) -> str | None:
    if node is None or node.type != "string":
        return None
    parts = [c.text.decode("utf-8", "replace") for c in node.children if c.type == "string_content"]
    return "".join(parts) if parts else None


def _is_true(node: Node | None) -> bool:
    return node is not None and node.type == "true"


def _target_name(node: Node | None) -> str | None:
    """The last dotted segment of a string literal / identifier / attribute."""
    text = _string_content(node)
    if text is not None:
        return text.rsplit(".", 1)[-1] or None
    if node is not None and node.type in ("identifier", "attribute"):
        raw = _node_text(node)
        return raw.rsplit(".", 1)[-1] if raw else None
    return None


def _callee_last_segment(call: Node) -> str | None:
    fn = call.child_by_field_name("function")
    if fn is None:
        return None
    text = _node_text(fn)
    return text.rsplit(".", 1)[-1] if text else None


def _call_args(call: Node) -> tuple[list[Node], dict[str, Node]]:
    args = call.child_by_field_name("arguments")
    positional: list[Node] = []
    kwargs: dict[str, Node] = {}
    for a in args.children if args is not None else ():
        if not a.is_named:
            continue
        if a.type == "keyword_argument":
            name = field_text(a, "name")
            value = a.child_by_field_name("value")
            if name and value is not None:
                kwargs[name] = value
        else:
            positional.append(a)
    return positional, kwargs


def _class_bases(node: Node) -> list[str]:
    args = node.child_by_field_name("superclasses")
    out: list[str] = []
    for c in args.children if args is not None else ():
        if not c.is_named or c.type == "keyword_argument":
            continue
        text = _node_text(c)
        if text:
            out.append(text.rsplit(".", 1)[-1])
    return out


# -- SQLAlchemy ------------------------------------------------------------------


def _sa_lazy_mode(value: Node | None) -> bool | None:
    """True/False for a recognized ``lazy=`` literal; ``None`` -> skip the relationship."""
    if value is None:
        return True
    if value.type == "true":
        return True
    if value.type in ("false", "none"):
        return False
    text = _string_content(value)
    if text is not None:
        if text in _SA_LAZY_TRUE_STRINGS:
            return True
        if text in _SA_LAZY_FALSE_STRINGS:
            return False
    return None


def _mapped_inner(node: Node | None) -> tuple[str | None, bool]:
    if node is None:
        return None, False
    if node.type == "subscript":
        value = node.child_by_field_name("value")
        head = _node_text(value) if value is not None else ""
        inner = node.child_by_field_name("subscript")
        if head in _MAPPED_LIST_HEADS:
            target, _ = _mapped_inner(inner)
            return target, True
        if head == "Optional":
            return _mapped_inner(inner)
        return None, False
    return _target_name(node), False


def _mapped_info(type_node: Node | None) -> tuple[bool, str | None, bool]:
    """``(is_mapped, target, is_list)`` for a ``Mapped[...]`` annotation."""
    if type_node is None or type_node.type != "subscript":
        return False, None, False
    value = type_node.child_by_field_name("value")
    if value is None or _node_text(value) != "Mapped":
        return False, None, False
    target, is_list = _mapped_inner(type_node.child_by_field_name("subscript"))
    return True, target, is_list


def _sa_backref(kwargs: dict[str, Node]) -> tuple[str, bool] | None:
    """``(attr name, lazy)`` from ``backref="x"`` / ``backref=backref("x", lazy=...)``."""
    value = kwargs.get("backref")
    if value is None:
        return None
    text = _string_content(value)
    if text is not None:
        return text, True
    if value.type == "call" and _callee_last_segment(value) == "backref":
        positional, bkwargs = _call_args(value)
        name = _string_content(positional[0]) if positional else None
        if name is None:
            return None
        lazy = _sa_lazy_mode(bkwargs.get("lazy"))
        return (name, lazy) if lazy is not None else None
    return None


def _sa_relationship_call(
    call: Node, type_node: Node | None, declaring: str
) -> tuple[Relation, tuple[str, str, Relation] | None] | None:
    positional, kwargs = _call_args(call)
    lazy = _sa_lazy_mode(kwargs.get("lazy"))
    if lazy is None:
        return None
    is_mapped, mapped_target, mapped_is_list = _mapped_info(type_node)
    target = _target_name(positional[0]) if positional else mapped_target
    uselist = kwargs.get("uselist")
    if "secondary" in kwargs or (uselist is not None and _is_true(uselist)) or mapped_is_list:
        kind = "collection"
    elif (uselist is not None and not _is_true(uselist)) or (is_mapped and not mapped_is_list):
        kind = "object"
    else:
        kind = "collection"
    relation = Relation(target=target, orm="sqlalchemy", kind=kind, lazy=lazy)
    pending = None
    backref = _sa_backref(kwargs)
    if backref is not None and target is not None:
        attr_name, backref_lazy = backref
        reverse = Relation(target=declaring, orm="sqlalchemy", kind="collection", lazy=backref_lazy)
        pending = (target, attr_name, reverse)
    return relation, pending


def _sa_relation_from_assignment(
    stmt: Node, declaring: str
) -> tuple[str, Relation, tuple[str, str, Relation] | None] | None:
    attr = ident(stmt.child_by_field_name("left"))
    right = stmt.child_by_field_name("right")
    if attr is None or right is None or right.type != "call":
        return None
    if _callee_last_segment(right) != "relationship":
        return None
    result = _sa_relationship_call(right, stmt.child_by_field_name("type"), declaring)
    if result is None:
        return None
    relation, pending = result
    return attr, relation, pending


def _has_declared_attr_decorator(stmt: Node) -> bool:
    decorators = [c for c in stmt.children if c.type == "decorator"]
    return any(_DECLARED_ATTR_HINT in (_node_text(d) or "") for d in decorators)


def _relationship_return(
    body: Node, declaring: str
) -> tuple[Relation, tuple[str, str, Relation] | None] | None:
    """The resolved call of the first ``return relationship(...)`` in *body*, if any."""
    for ret in walk(body):
        if ret.type != "return_statement":
            continue
        value = next((c for c in ret.children if c.is_named), None)
        if value is None or value.type != "call":
            continue
        if _callee_last_segment(value) == "relationship":
            return _sa_relationship_call(value, None, declaring)
    return None


def _sa_declared_attr(
    stmt: Node, declaring: str
) -> tuple[str, Relation, tuple[str, str, Relation] | None] | None:
    """A ``@declared_attr`` method whose body returns a ``relationship(...)`` call."""
    if not _has_declared_attr_decorator(stmt):
        return None
    fn = stmt.child_by_field_name("definition")
    if fn is None or fn.type != "function_definition":
        return None
    name = field_text(fn, "name")
    body = fn.child_by_field_name("body")
    if not name or body is None:
        return None
    result = _relationship_return(body, declaring)
    return (name, *result) if result is not None else None


# -- Django ------------------------------------------------------------------------


def _django_target(positional: list[Node], kwargs: dict[str, Node], declaring: str) -> str | None:
    arg = positional[0] if positional else kwargs.get("to")
    if arg is None:
        return None
    text = _string_content(arg)
    if text is not None:
        return declaring if text == "self" else text.rsplit(".", 1)[-1]
    if arg.type in ("identifier", "attribute"):
        raw = _node_text(arg)
        return raw.rsplit(".", 1)[-1] if raw else None
    return None


def _django_reverse_name(kwargs: dict[str, Node], declaring: str, ctor: str) -> str | None:
    value = kwargs.get("related_name")
    if value is not None:
        text = _string_content(value)
        if text is None or "%(" in text or text == "+" or text.endswith("+"):
            return None
        return text
    lower = declaring.lower()
    return lower if ctor == "OneToOneField" else f"{lower}_set"


def _django_relation_from_assignment(
    stmt: Node, declaring: str, is_django_file: bool
) -> tuple[str, Relation, tuple[str, str, Relation] | None] | None:
    if not is_django_file:
        return None
    attr = ident(stmt.child_by_field_name("left"))
    right = stmt.child_by_field_name("right")
    if attr is None or right is None or right.type != "call":
        return None
    ctor = _callee_last_segment(right)
    if ctor not in _DJANGO_FORWARD_KIND:
        return None
    positional, kwargs = _call_args(right)
    target = _django_target(positional, kwargs, declaring)
    relation = Relation(target=target, orm="django", kind=_DJANGO_FORWARD_KIND[ctor], lazy=True)
    pending = None
    reverse_name = _django_reverse_name(kwargs, declaring, ctor)
    if target is not None and reverse_name:
        reverse_kind = "object" if ctor == "OneToOneField" else "collection"
        reverse = Relation(target=declaring, orm="django", kind=reverse_kind, lazy=True)
        pending = (target, reverse_name, reverse)
    return attr, relation, pending


# -- per-file scan + cross-file merge ---------------------------------------------


def _manager_names(stmt: Node) -> set[str]:
    """Every name in ``objects = X()`` / ``X.as_manager()`` / ``X.from_queryset(Y)()``."""
    right = stmt.child_by_field_name("right")
    if ident(stmt.child_by_field_name("left")) != "objects" or right is None:
        return set()
    return {name for n in walk(right) if (name := ident(n))}


def _has_eager_get_queryset(node: Node) -> bool:
    """A ``get_queryset`` in this class body that select_related / prefetch_related."""
    body = node.child_by_field_name("body")
    return body is not None and any(
        fn.type == "function_definition"
        and field_text(fn, "name") == "get_queryset"
        and any(
            c.type == "call" and _callee_last_segment(c) in _EAGER_QUERYSET_CALLS
            for c in walk(fn)
        )
        for fn in walk(body)
    )


def _scan_class_body(node: Node, name: str, is_django_file: bool, pending: list) -> _RawClass:
    body = node.child_by_field_name("body")
    rc = _RawClass(bases=_class_bases(node), eager_queryset=_has_eager_get_queryset(node))
    for raw_stmt in body.children if body is not None else ():
        if not raw_stmt.is_named:
            continue
        stmt = (
            next((c for c in raw_stmt.children if c.is_named), raw_stmt)
            if raw_stmt.type == "expression_statement"
            else raw_stmt
        )
        result = None
        if stmt.type == "assignment" and (names := _manager_names(stmt)):
            rc.manager_names |= names
        elif stmt.type == "assignment":
            result = _sa_relation_from_assignment(stmt, name) or _django_relation_from_assignment(
                stmt, name, is_django_file
            )
        elif stmt.type == "decorated_definition":
            result = _sa_declared_attr(stmt, name)
        if result is None:
            continue
        attr, relation, added = result
        rc.relations[attr] = relation
        if added is not None:
            pending.append(added)
    return rc


def _scan_file(
    abs_path: str, language: str, source: bytes, pending: list
) -> dict[str, _RawClass] | None:
    from ..dataflow.parsing import parse_source  # deferred: dataflow imports perf

    parsed = parse_source(abs_path, language, source)
    if parsed is None:
        return None
    root, _lmap = parsed
    is_django_file = b"django" in source
    classes: dict[str, _RawClass] = {}
    for node in walk(root):
        if node.type != "class_definition":
            continue
        name = field_text(node, "name")
        if not name:
            continue
        rc = _scan_class_body(node, name, is_django_file, pending)
        existing = classes.get(name)
        if existing is None:
            classes[name] = rc
        else:
            existing.relations.update(rc.relations)
            existing.bases.extend(rc.bases)
            existing.manager_names |= rc.manager_names
            existing.eager_queryset |= rc.eager_queryset
    return classes


_Merged = dict[str, tuple[dict[str, Relation], list[str]]]


def _merge_collisions(raw: dict[str, list[_RawClass]]) -> _Merged:
    merged: _Merged = {}
    for name, occurrences in raw.items():
        bases = [b for rc in occurrences for b in rc.bases]
        if len(occurrences) == 1:
            merged[name] = (occurrences[0].relations, bases)
            continue
        keys = set(occurrences[0].relations)
        for rc in occurrences[1:]:
            keys &= set(rc.relations)
        relations = {
            k: occurrences[0].relations[k]
            for k in keys
            if all(rc.relations[k] == occurrences[0].relations[k] for rc in occurrences)
        }
        merged[name] = (relations, bases)
    return merged


def _resolve_inherited(
    name: str, merged: _Merged, cache: dict[str, dict[str, Relation]]
) -> dict[str, Relation]:
    if name in cache:
        return cache[name]
    entry = merged.get(name)
    if entry is None:
        return {}
    relations, bases = entry
    result = dict(relations)
    cache[name] = result  # registered before recursing: cycle-safe
    for base in bases:
        if base == name:
            continue
        for k, v in _resolve_inherited(base, merged, cache).items():
            result.setdefault(k, v)
    return result


def build_model_index(parsed_files: Iterable[Any], read_source: SourceReader) -> ModelIndex:
    raw: dict[str, list[_RawClass]] = {}
    pending: list[tuple[str, str, Relation]] = []
    for pf in parsed_files:
        info = pf.file_info
        if info.language != "python":
            continue
        source = read_source(info.abs_path)
        if source is None or not any(h in source for h in _RELATION_HINTS + _MANAGER_HINTS):
            continue
        classes = _scan_file(info.abs_path, info.language, source, pending)
        if classes is None:
            continue
        for name, rc in classes.items():
            raw.setdefault(name, []).append(rc)
    for target, attr, relation in pending:
        for rc in raw.get(target, ()):
            rc.relations.setdefault(attr, relation)
    merged = _merge_collisions(raw)
    cache: dict[str, dict[str, Relation]] = {}
    resolved = {name: _resolve_inherited(name, merged, cache) for name in merged}
    return ModelIndex(
        relations=resolved,
        models=frozenset(merged),
        custom_managers=_with_custom_manager(raw, merged),
    )


def _with_custom_manager(raw: dict[str, list[_RawClass]], merged: _Merged) -> frozenset[str]:
    """Models whose ``objects`` class eager-loads in ``get_queryset``, or inherit one."""
    eager = {name for name, rcs in raw.items() if any(rc.eager_queryset for rc in rcs)}
    found = {
        name for name, rcs in raw.items() if any(rc.manager_names & eager for rc in rcs)
    }
    grew = True
    while grew:
        grew = False
        for name, (_relations, bases) in merged.items():
            if name not in found and found.intersection(bases):
                found.add(name)
                grew = True
    return frozenset(found)


__all__ = ["ModelIndex", "Relation", "build_model_index"]
