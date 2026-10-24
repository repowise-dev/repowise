"""Move Method detector — Feature Envy as a graph query.

A method suffers *feature envy* when it is more interested in another class
than the one it lives in: it calls into a foreign class's members far more
than its own. The fix is to move it next to the data it actually uses — a
cross-file architectural refactoring that falls straight out of the call
graph the health pass already built.

Detection (Jaccard-distance feature envy over the call graph):

- A method ``m`` in class ``C`` accesses a set of class-owned members (its
  callees that belong to some class, resolved via the ``calls`` graph).
- For ``C`` and every foreign class ``T`` that ``m`` touches, the Jaccard
  distance ``1 - |accessed AND members(T)| / |accessed OR members(T)|``
  measures how close ``m`` is to that class.
- If a foreign class ``T`` is *strictly nearer* than ``m``'s own class — and
  ``m`` barely uses its own class while leaning on ``T`` — ``m`` belongs in
  ``T``.

Feature envy is notoriously noisy, so the gate is deliberately strict
(high-precision, low-recall): the method must access at least
``_MIN_FOREIGN_MEMBERS`` distinct members of the target, more of the target
than of its own class, and almost nothing of its own class. We only ever
consider classes ``m`` already calls into, so the target is always a class
the method can legally reach. Dunder / framework hook methods are skipped —
moving ``__init__`` or ``__eq__`` is never the intent.

Two kinds of class are never a target, because touching their members is not
envy:

- a class the method **instantiates**. A method that builds a result object
  and fills it in (``result = WriteResult(); result.record(...)``) is that
  object's producer; moving it onto the result type turns a record into the
  place the work happens. Envy is operating on an instance someone else owns.
- an **ancestor** of the method's own class (``extends`` / ``implements``).
  Calling an inherited method is calling your own member; the base class is
  not somewhere else to move to.

And the target must draw more of the method's calls than its home file does.
The Jaccard sets see only class-owned callees, so a method whose work is three
module-level helpers plus ``result.record`` / ``result.note`` on a collector it
was handed looked like it envied the collector.

The ``calls`` graph does not see field reads, so own-class use is also read
off the class analysis: a method that reads one of its class's fields (a
service method using the repository injected into its constructor) or shares
a component holding fields works on its own class's state and stays. A method that fulfils a
contract (``@Override``, an ``override`` / ``virtual`` / ``abstract``
modifier, a member its base class or interface declares, a runtime contract
name such as ``toString``) cannot move either, since the type it overrides for
is the reason it exists. Building the target through a static factory
(``OperationResult.Ok()``, any target member returning the target type) counts
as instantiating it, and an interface, an exception type, a ``*Util``/``*Helper``
class or a C# ``static class`` is never a home for instance behaviour, and
neither is a class in another language family (a Swift call the graph resolved
onto a Kotlin class of the same name). The
other ``partial`` fragments of the method's own class are its own class.

Two more rules keep static utilities out of the data classes they read:

- a **static** method (Java ``static``, a Kotlin ``object`` / ``companion
  object`` function) has no instance of its own to be envious from, so the
  getters it calls on a parameter are its inputs, not envy. It never moves
  into a class whose members it uses are all accessors (``getX`` / ``isX`` /
  ``setX``, a record component): an entity, a DTO or a value class.
- a move never **reverses a dependency**. On the JVM, when the method calls
  something that is not on the target and the target's file cannot see it (no
  import, not in its package), the target would have to depend on the
  method's home, so that target is not a home for the method.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any

from repowise.core.analysis.dead_code.contract_methods import is_contract_method
from repowise.core.analysis.execution_graph import is_reliable_call_edge
from repowise.core.analysis.graph_view import heritage_ancestors

from ....test_paths import is_test_related_path
from ..effort import effort_bucket
from .language_family import same_language_family
from .models import RefactoringContext, RefactoringSuggestion
from .registry import RefactoringDetector, register

# Distinct foreign members the method must access for the envy to be real
# (a single shared call is incidental, not envy).
_MIN_FOREIGN_MEMBERS = 2

# The method may touch at most this many distinct members of its OWN class —
# above this it still has a real reason to live where it is.
_MAX_OWN_MEMBERS = 1

# The method must be genuinely close to the target class, not merely closer
# than its (empty) own class: calling 2 methods of a 100-member god class
# (Jaccard distance ~0.98) is not envy, it is normal collaboration. A real
# move target shares a meaningful fraction of its members with the method.
_MAX_TARGET_DISTANCE = 0.7

# The target must be clearly nearer than the own class by this margin, so a
# near-tie (the method is about as related to both) never fires.
_MIN_DISTANCE_MARGIN = 0.25

# Target kinds that hold no instance behaviour to move a method onto.
_NON_TARGET_KINDS = frozenset({"interface", "trait", "protocol"})
# Name suffixes of classes that are never a home for a moved method: an
# exception carries an error, a utility class only static helpers.
_NON_TARGET_SUFFIXES = ("Exception", "Error", "Util", "Utils", "Helper", "Helpers")
# Java ``@Override``, Python ``@override`` / ``@typing.override``.
_OVERRIDE_DECORATOR = re.compile(r"@(?:[\w.]+\.)?[Oo]verride\b")
# Keyword modifiers (``Symbol.modifiers``) that bind a member to a type
# hierarchy: it overrides a base member (C#, Kotlin, Swift, Scala, TypeScript
# ``override``; VB.NET ``Overrides``) or is declared for subclasses to
# override (C# ``virtual``, Kotlin/Swift ``open``, ``abstract``; VB.NET
# ``Overridable`` / ``MustOverride``).
_CONTRACT_MODIFIERS = frozenset(
    {"override", "overrides", "virtual", "open", "overridable", "abstract", "mustoverride"}
)
# The class modifier that makes a type instance-less, per language: a C#
# ``static class`` holds only static helpers, like a ``*Util`` class. Java's
# ``static`` nested class is instantiable, so the keyword alone is not enough.
_STATIC_HOLDER_MODIFIER = {"csharp": "static"}
# Factory-shaped names: a member returning its own class under one of these
# names builds an instance (``OperationResult.Ok()``, ``Foo.of(...)``); a
# getter such as ``parent() -> T`` does not.
_FACTORY_NAME = re.compile(
    r"^(?:of|from|create|new|build|make|ok|fail|success|failure|empty|parse|"
    r"value_?of|get_?instance|instance)",
    re.IGNORECASE,
)
# Python marks a factory with a decorator.
_FACTORY_DECORATORS = ("@staticmethod", "@classmethod")
# ``getX`` / ``isX`` / ``setX``: the accessor shape of a bean, an entity or a
# DTO. ``get`` alone (a map-style lookup) is behaviour, so a suffix is needed.
_ACCESSOR_NAME = re.compile(r"^(?:get|is|set)[A-Z0-9_]")
# Languages whose imports and packages decide whether one file can see another.
_VISIBILITY_LANGUAGES = frozenset({"java", "kotlin"})
# File-to-file edges that mean "this file reads that one".
_VISIBLE_EDGE_TYPES = frozenset({"imports", "type_use"})


def _node(graph: Any, node_id: str) -> dict | None:
    if node_id not in graph:
        return None
    return graph.nodes[node_id]


def _file_is_test(graph: Any, path: str) -> bool:
    """Whether *path* is test material, preferring the flag ingestion stored.

    A move target is a different file, so its language is not ``ctx.language``
    and a path-only check cannot resolve the cases that need one (Ruby's
    ``spec/``). The graph node carries the decision made at ingestion, with the
    language in hand; the path rules are the fallback when the file has no node.
    """
    node = _node(graph, path)
    if node is not None and "is_test" in node:
        return bool(node["is_test"])
    return is_test_related_path(path)


def _partial_fragments(graph: Any, file_path: str, class_name: str) -> set[str]:
    """The class ids of *class_name*'s ``partial`` fragments in other files.

    The graph links co-fragment files with ``partial_class`` import edges
    naming the type; every fragment is the same class, so none is foreign.
    """
    if file_path not in graph:
        return set()
    return {
        f"{other}::{class_name}"
        for _u, other, data in graph.out_edges(file_path, data=True)
        if data.get("hint_source") == "partial_class"
        and class_name in (data.get("imported_names") or ())
    }


def _class_name(graph: Any, class_id: str) -> str:
    return (_node(graph, class_id) or {}).get("name") or class_id.rsplit("::", 1)[-1]


def _class_members(graph: Any, class_id: str, cache: dict[str, set[str]]) -> set[str]:
    cached = cache.get(class_id)
    if cached is not None:
        return cached
    members: set[str] = set()
    if class_id in graph:
        for _u, v, data in graph.out_edges(class_id, data=True):
            if data.get("edge_type") == "has_method":
                members.add(v)
    cache[class_id] = members
    return members


def _overrides(graph: Any, data: dict, own_class_id: str, language: str) -> bool:
    """Whether the method fulfils a contract: an ``@Override`` annotation or
    an ``override`` / ``virtual`` / ``abstract`` modifier, a runtime contract
    name, or a member an ancestor class or interface also declares."""
    name = data.get("name") or ""
    if any(_OVERRIDE_DECORATOR.search(d) for d in data.get("decorators") or ()):
        return True
    if _CONTRACT_MODIFIERS.intersection(data.get("modifiers") or ()):
        return True
    if is_contract_method(name, data.get("kind"), language, modifiers=data.get("modifiers")):
        return True
    declared = {
        (_node(graph, member) or {}).get("name")
        for base in heritage_ancestors(graph, own_class_id)
        for member in _class_members(graph, base, {})
    }
    return name in declared


def _returns(signature: str | None, class_name: str) -> bool:
    """Whether a ``name(params) -> Type`` signature returns *class_name*."""
    pattern = rf"->\s*(?:[\w.]+\.)?{re.escape(class_name)}\b"
    return bool(signature) and re.search(pattern, signature) is not None


def _is_factory(member: dict, class_name: str) -> bool:
    """A constructor, or a factory returning *class_name*: factory-named or
    marked static/class-level (Python decorators)."""
    if member.get("name") == class_name:
        return True
    if not _returns(member.get("signature"), class_name):
        return False
    decorators = " ".join(member.get("decorators") or ())
    return bool(_FACTORY_NAME.match(member.get("name") or "")) or any(
        d in decorators for d in _FACTORY_DECORATORS
    )


def _builds(graph: Any, class_id: str, accessed: set[str]) -> bool:
    """Whether the method instantiates *class_id*: a constructor call, or a
    call to a factory of the class (``OperationResult.Ok()``)."""
    class_name = _class_name(graph, class_id)
    return class_id in accessed or any(
        _is_factory(_node(graph, member) or {}, class_name) for member in accessed
    )


def _is_static_holder(graph: Any, class_id: str) -> bool:
    """A C# ``static class``. C# lets one ``partial`` fragment carry the
    ``static`` for all of them, so every fragment is asked."""
    node = _node(graph, class_id) or {}
    modifier = _STATIC_HOLDER_MODIFIER.get(node.get("language") or "")
    if modifier is None:
        return False
    file_path, _sep, name = class_id.rpartition("::")
    fragments = _partial_fragments(graph, file_path, name) | {class_id}
    return any(modifier in ((_node(graph, f) or {}).get("modifiers") or ()) for f in fragments)


def _never_a_target(graph: Any, class_id: str) -> bool:
    """An interface, an exception type or a utility class (by name, or a C#
    ``static class``)."""
    if (_node(graph, class_id) or {}).get("kind") in _NON_TARGET_KINDS:
        return True
    if _is_static_holder(graph, class_id):
        return True
    names = [_class_name(graph, class_id)]
    names += [b.rsplit("::", 1)[-1] for b in heritage_ancestors(graph, class_id)]
    return any(n.endswith(_NON_TARGET_SUFFIXES) for n in names)


def _is_target(
    graph: Any, class_id: str, accessed: set[str], home: set[str], home_file: str
) -> bool:
    """A foreign class the method could move to: not its own class or an
    ancestor (*home*), not a class it builds, not a non-target kind, and in a
    file *home_file*'s code can move into (a call resolved across languages
    names a class the method cannot live in)."""
    target_file = (_node(graph, class_id) or {}).get("file_path") or class_id.rpartition("::")[0]
    return (
        class_id not in home
        and same_language_family(home_file, target_file)
        and not _builds(graph, class_id, accessed)
        and not _never_a_target(graph, class_id)
    )


def _uses_own_state(classes: list[Any], parent: str, name: str, line: int | None) -> bool:
    """Whether the method is bound to its class: its class implements a
    contract that fixes every method (a Rust trait impl), or it shares a
    cohesion component that holds fields, or calls an inherited or abstract
    member, with the rest of its class. The ``calls`` graph sees none of
    these; the class analysis does (``ClassComplexity``)."""
    own = next(
        (
            cls
            for cls in classes
            if getattr(cls, "name", None) == parent
            and (line is None or cls.start_line <= line <= cls.end_line)
        ),
        None,
    )
    return own is not None and _binds_method(own, name)


def _binds_method(cls: Any, name: str) -> bool:
    """Whether *cls* holds method *name* in place: a contract impl, a method
    that uses an instance field of the class (an injected repository or
    client, read by bare name in Java), or a cohesion component with fields or
    outside calls that contains it."""
    if getattr(cls, "contract_impl", False):
        return True
    # ``getattr``: a walk cached before the field existed reads as no signal.
    # Ceiling: only languages with class analysis fill it (Go emits no classes,
    # so a Go receiver's fields are not read here); static fields such as a
    # ``static final`` logger never count, being no instance's state.
    if name in getattr(cls, "field_readers", ()):
        return True
    groups = getattr(cls, "components", None) or ()
    return any(name in g.methods and (g.fields or g.calls) for g in groups)


def _is_static(data: dict) -> bool:
    """Whether the method has no instance: Java ``static``, a Kotlin
    ``object`` / ``companion object`` function (the parser records both in
    ``modifiers``)."""
    return "static" in (data.get("modifiers") or ())


def _split_params(text: str) -> list[str]:
    """Split a parameter list on its top-level commas (``Map<K, V> m`` is one)."""
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _constructor_param_names(graph: Any, class_id: str, cache: dict[str, set[str]]) -> set[str]:
    """The parameter names of *class_id*'s constructors. A record's components
    are its canonical constructor's parameters; each has a same-named accessor."""
    cached = cache.get(class_id)
    if cached is not None:
        return cached
    class_name = _class_name(graph, class_id)
    names: set[str] = set()
    for member in _class_members(graph, class_id, {}):
        node = _node(graph, member) or {}
        signature = node.get("signature") or ""
        if node.get("name") != class_name or "(" not in signature:
            continue
        params = signature[signature.index("(") + 1 : signature.rindex(")")]
        for param in _split_params(params):
            # ``String appId`` / ``final List<String> ids`` / ``val appId: String``
            declared = param.split(":", 1)[0].split("=", 1)[0].split()
            if declared:
                names.add(declared[-1].lstrip("*&"))
    cache[class_id] = names
    return names


def _is_accessor(graph: Any, member_id: str, component_names: set[str]) -> bool:
    """A ``getX`` / ``isX`` / ``setX`` member, or the no-argument accessor of a
    record component (``appId()``)."""
    node = _node(graph, member_id) or {}
    name = node.get("name") or ""
    if _ACCESSOR_NAME.match(name):
        return True
    signature = node.get("signature") or ""
    return (
        name in component_names and re.search(rf"\b{re.escape(name)}\(\s*\)", signature) is not None
    )


def _only_accessors(
    graph: Any, class_id: str, members: set[str], cache: dict[str, set[str]]
) -> bool:
    """Whether every member in *members* is an accessor of *class_id*: the class
    is a data holder as far as this method is concerned."""
    component_names = _constructor_param_names(graph, class_id, cache)
    return bool(members) and all(_is_accessor(graph, m, component_names) for m in members)


def _file_of(graph: Any, node_id: str) -> str | None:
    return (_node(graph, node_id) or {}).get("file_path")


def _can_see(graph: Any, from_file: str, to_file: str) -> bool:
    """Whether code in *from_file* can name what *to_file* declares: the same
    file, the same package directory, or an import / type reference to it."""
    if from_file == to_file:
        return True
    if posixpath.dirname(from_file) == posixpath.dirname(to_file):
        return True
    if from_file not in graph or to_file not in graph:
        return False
    data = graph.get_edge_data(from_file, to_file)
    if not data:
        return False
    edges = data.values() if graph.is_multigraph() else (data,)
    return any(e.get("edge_type") in _VISIBLE_EDGE_TYPES for e in edges)


def _reverses_dependency(
    graph: Any, language: str, target_file: str, other_callees: set[str]
) -> bool:
    """Whether moving the method into *target_file* would make that file depend
    on something it cannot see today: a callee that is not on the target and
    lives in a file the target does not import and shares no package with. The
    move would invert the dependency (the entity depends on the utility)."""
    if language not in _VISIBILITY_LANGUAGES or not target_file:
        return False
    for callee in other_callees:
        callee_file = _file_of(graph, callee)
        if callee_file is not None and not _can_see(graph, target_file, callee_file):
            return True
    return False


def _owning_class_id(graph: Any, callee_id: str) -> str | None:
    """The class node a callee belongs to: the method's parent class, or the
    class itself for a constructor call. ``None`` for free functions and
    unresolved callees (they carry no class membership)."""
    data = _node(graph, callee_id)
    if data is None:
        return None
    kind = data.get("kind")
    if kind == "method":
        parent = data.get("parent_name")
        file_path = data.get("file_path")
        if parent and file_path:
            class_id = f"{file_path}::{parent}"
            return class_id if class_id in graph else None
        return None
    if kind == "class":
        return callee_id
    return None


@register
class MoveMethodDetector(RefactoringDetector):
    name = "move_method"

    def detect(self, ctx: RefactoringContext) -> list[RefactoringSuggestion]:
        graph = ctx.graph
        if graph is None or is_test_related_path(ctx.file_path, ctx.language):
            return []

        if ctx.file_methods is not None:
            methods = list(ctx.file_methods)
        else:
            methods = self._methods_in_file(graph, ctx.file_path)
        if not methods:
            return []

        members_cache: dict[str, set[str]] = {}
        out: list[RefactoringSuggestion] = []
        for method_id in methods:
            suggestion = self._envy_for(ctx, graph, method_id, members_cache)
            if suggestion is not None:
                out.append(suggestion)

        # Deterministic: impact is 0 for this graph-native type, so order by
        # the strength of the envy (foreign members accessed), then symbol.
        out.sort(key=lambda s: (-int(s.evidence.get("foreign_calls", 0)), s.target_symbol))
        return out

    def _methods_in_file(self, graph: Any, file_path: str) -> list[str]:
        """Symbol ids of methods defined in *file_path*, via ``defines`` edges
        (falling back to a prefix scan), sorted for determinism."""
        out: list[str] = []
        if file_path in graph:
            for _u, v, data in graph.out_edges(file_path, data=True):
                if data.get("edge_type") != "defines":
                    continue
                node = _node(graph, v)
                if node and node.get("kind") == "method":
                    out.append(v)
        if not out:
            prefix = f"{file_path}::"
            for node_id, data in graph.nodes(data=True):
                if (
                    data.get("node_type") == "symbol"
                    and data.get("kind") == "method"
                    and node_id.startswith(prefix)
                ):
                    out.append(node_id)
        return sorted(set(out))

    def _envy_for(
        self,
        ctx: RefactoringContext,
        graph: Any,
        method_id: str,
        members_cache: dict[str, set[str]],
    ) -> RefactoringSuggestion | None:
        data = _node(graph, method_id)
        if data is None:
            return None
        name = data.get("name") or ""
        if name.startswith("__"):  # dunder / hook methods don't move
            return None
        parent = data.get("parent_name")
        if not parent:
            return None
        own_class_id = f"{ctx.file_path}::{parent}"
        if own_class_id not in graph:
            return None
        if _overrides(graph, data, own_class_id, ctx.language) or _uses_own_state(
            ctx.classes, parent, name, data.get("start_line")
        ):
            return None

        # Group the method's class-owned callees by the class they belong to.
        accessed_by_class: dict[str, set[str]] = {}
        home: set[str] = set()
        for _u, callee, edata in graph.out_edges(method_id, data=True):
            if (
                not is_reliable_call_edge(edata.get("edge_type"), edata.get("resolution_origin"))
                or callee == method_id
            ):
                continue
            if (_node(graph, callee) or {}).get("file_path") == ctx.file_path:
                home.add(callee)
            owner = _owning_class_id(graph, callee)
            if owner is None:
                continue
            accessed_by_class.setdefault(owner, set()).add(callee)

        accessed: set[str] = set()
        for members in accessed_by_class.values():
            accessed |= members
        if not accessed:
            return None

        own_ids = _partial_fragments(graph, ctx.file_path, parent) | {own_class_id}
        own_distinct = len(set().union(*(accessed_by_class.get(c, ()) for c in own_ids)))
        if own_distinct > _MAX_OWN_MEMBERS:
            return None

        # Nearest foreign class by Jaccard distance (tie-break on class id).
        # A constructor call lands the class id in its own member set.
        own_and_inherited = heritage_ancestors(graph, own_class_id) | own_ids
        static = _is_static(data)
        components: dict[str, set[str]] = {}
        foreign = [
            (c, m)
            for c, m in accessed_by_class.items()
            if _is_target(graph, c, m, own_and_inherited, ctx.file_path)
            # A static method reading a data holder's accessors has inputs, not envy.
            and not (static and _only_accessors(graph, c, m, components))
            and not _reverses_dependency(
                graph,
                ctx.language,
                _file_of(graph, c) or c.rpartition("::")[0],
                accessed - m,
            )
        ]
        if not foreign:
            return None
        own_distance = self._distance(graph, own_class_id, accessed, members_cache)

        def _dist(item: tuple[str, set[str]]) -> tuple[float, str]:
            return (self._distance(graph, item[0], accessed, members_cache), item[0])

        target_id, target_accessed = min(foreign, key=_dist)
        target_distance = self._distance(graph, target_id, accessed, members_cache)
        foreign_distinct = len(target_accessed)

        if foreign_distinct < _MIN_FOREIGN_MEMBERS:
            return None
        # The own class lives in the home file, so this covers it too.
        if foreign_distinct <= len(home):
            return None
        if target_distance > _MAX_TARGET_DISTANCE:
            return None
        if own_distance - target_distance < _MIN_DISTANCE_MARGIN:
            return None

        target_node = _node(graph, target_id) or {}
        target_class = target_node.get("name") or target_id.rsplit("::", 1)[-1]
        target_file = target_node.get("file_path")
        if target_file and _file_is_test(graph, target_file):
            return None  # never propose moving production code into a test class

        callers = self._caller_count(graph, method_id)
        plan = {
            "method": name,
            "from_class": parent,
            "to_class": target_class,
            "to_file": target_file,
        }
        evidence = {
            "foreign_calls": foreign_distinct,
            "own_calls": own_distinct,
            "own_distance": round(own_distance, 3),
            "target_distance": round(target_distance, 3),
        }
        blast_radius = {
            "callers": callers,
            "files": sorted({ctx.file_path, target_file} - {None}),
        }
        nloc = self._method_nloc(data)
        confidence = "high" if own_distinct == 0 and foreign_distinct >= 3 else "medium"
        return RefactoringSuggestion(
            refactoring_type=self.name,
            file_path=ctx.file_path,
            target_symbol=f"{parent}.{name}",
            line_start=data.get("start_line"),
            line_end=data.get("end_line"),
            plan=plan,
            evidence=evidence,
            impact_delta=0.0,
            effort_bucket=effort_bucket(nloc),
            blast_radius=blast_radius,
            confidence=confidence,
            source_biomarker="",
        )

    def _distance(
        self, graph: Any, class_id: str, accessed: set[str], cache: dict[str, set[str]]
    ) -> float:
        """Jaccard distance between the method's accessed-entity set and a
        class's members. 0 = the method only touches this class; 1 = no
        overlap. Empty union degrades to max distance."""
        members = _class_members(graph, class_id, cache)
        union = accessed | members
        if not union:
            return 1.0
        return 1.0 - len(accessed & members) / len(union)

    @staticmethod
    def _caller_count(graph: Any, method_id: str) -> int:
        count = 0
        if method_id in graph:
            for _u, _v, data in graph.in_edges(method_id, data=True):
                if is_reliable_call_edge(data.get("edge_type"), data.get("resolution_origin")):
                    count += 1
        return count

    @staticmethod
    def _method_nloc(data: dict) -> int:
        start = data.get("start_line")
        end = data.get("end_line")
        if isinstance(start, int) and isinstance(end, int) and end >= start:
            return end - start + 1
        return 0
