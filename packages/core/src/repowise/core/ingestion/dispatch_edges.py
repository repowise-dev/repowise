"""Override dispatch: a base method and the implementations that answer for it.

A call written against a base type resolves to the base's method, and there the
graph stops. Nothing joins ``Handler.handle`` to the twelve classes that
implement it, so a traversal from the caller never reaches the code that runs,
and every implementation reads as called by nobody.

This pass runs once over the built graph and emits ``dispatches_to`` from a
base method to each same-named method declared by a type that inherits from it.
It is a post-pass rather than a resolution tier because it needs the heritage
edges to exist first, and it reads only the graph — no source text, no parse.

**Node-anchored.** Both the ancestor walk and the method lookup go through
symbol ids: parents come from the graph's own ``extends`` / ``implements``
edges, methods from ``has_method``. A name-keyed version would union the
parents of every same-named class in the repo, which is the error
``heritage_ancestors`` refuses in terms.

**What it deliberately cannot see.** ``has_method`` is emitted per file, so a
method declared away from its type — a Go method set spread over a package, a
C++ class whose bodies live in the ``.cc``, a C# partial class — is invisible
here. That is a missed edge, never a wrong one.
"""

from __future__ import annotations

from collections import defaultdict

import structlog

from .heritage_resolver import heritage_ancestors

log = structlog.get_logger(__name__)

# Languages whose dispatch edges passed a precision audit. A language absent
# here is not excluded; most were never attempted.
DISPATCH_LANGUAGES: frozenset[str] = frozenset(
    {"java", "csharp", "kotlin", "swift", "python", "cpp"}
)

_HERITAGE_EDGE_TYPES = frozenset({"extends", "implements"})

# Ancestors within four hops: ``heritage_ancestors`` bounds expansion, not
# reach, so 3 reaches 4. Same depth the resolver's inherited tier uses.
_MAX_EXPAND_DEPTH = 3

# A base method answered by more implementations than this is a dispatch point
# too broad to say anything useful about — every implementation would gain an
# incoming edge from it and the base would gain the out-degree of a hub it is
# not. Refused whole rather than truncated, so the cap cannot be mistaken for
# a complete answer.
_MAX_IMPLEMENTATIONS = 24

# Name-matched, no signature compared, so the relation is a possible dispatch
# target rather than a proven override. Sits at the confidence the resolver
# gives its own "the pair exists somewhere in the repo" tier.
_DISPATCH_CONFIDENCE = 0.75

# A constructor is never dispatched to: calling the base's runs the base's.
# Python spells it; the C family names it after the class, which is the same
# rule `_rivals_a_class_method` encodes in the resolver.
_CONSTRUCTOR_NAMES = frozenset({"__init__", "__new__", "constructor"})

# Languages where `private` is enforced and therefore excludes a member from
# dispatch entirely: a private base is not virtual and a private member of a
# subtype overrides nothing. Python and TypeScript are absent because their
# `_name` / `private` are conventions the runtime does not enforce. Found by
# the C# precision audit, where four of six wrong edges were a `private` test
# helper that happened to share a name with a `virtual` base.
_PRIVATE_IS_NOT_DISPATCHED = frozenset({"java", "csharp", "kotlin", "swift", "cpp"})


def _methods_by_name(graph, type_id: str) -> dict[str, list[str]]:
    """``{method name: [symbol ids]}`` for one type node, dispatchable only.

    A list, not a symbol: a class declaring two same-named methods is an
    overload group, and keying by name alone would keep one of them
    arbitrarily.
    """
    out: dict[str, list[str]] = defaultdict(list)
    for _, member, data in graph.out_edges(type_id, data=True):
        if data.get("edge_type") != "has_method":
            continue
        node = graph.nodes[member]
        name = node.get("name")
        if not name:
            continue
        if (
            node.get("visibility") == "private"
            and node.get("language") in _PRIVATE_IS_NOT_DISPATCHED
        ):
            continue
        out[name].append(member)
    return out


def _is_constructor(name: str, type_id: str) -> bool:
    return name in _CONSTRUCTOR_NAMES or name == type_id.rpartition("::")[2]


def resolve_override_dispatch(
    graph,
    *,
    languages: frozenset[str] = DISPATCH_LANGUAGES,
    max_implementations: int = _MAX_IMPLEMENTATIONS,
) -> int:
    """Emit ``dispatches_to`` edges. Returns the number added."""
    parents: dict[str, set[str]] = defaultdict(set)
    for child, parent, data in graph.edges(data=True):
        if data.get("edge_type") not in _HERITAGE_EDGE_TYPES:
            continue
        if (
            graph.nodes[child].get("node_type") == "symbol"
            and graph.nodes[parent].get("node_type") == "symbol"
        ):
            parents[child].add(parent)

    methods: dict[str, dict[str, list[str]]] = {}

    def methods_of(type_id: str) -> dict[str, list[str]]:
        got = methods.get(type_id)
        if got is None:
            got = _methods_by_name(graph, type_id)
            methods[type_id] = got
        return got

    # base method id -> the implementations that could answer for it
    candidates: dict[str, set[str]] = defaultdict(set)
    for child in sorted(parents):
        if graph.nodes[child].get("language") not in languages:
            continue
        child_methods = methods_of(child)
        if not child_methods:
            continue
        for ancestor in heritage_ancestors(
            child, lambda t: parents.get(t, ()), max_expand_depth=_MAX_EXPAND_DEPTH
        ):
            if ancestor == child:
                continue
            for name, bases in methods_of(ancestor).items():
                impls = child_methods.get(name)
                if impls is None or _is_constructor(name, ancestor):
                    continue
                for base in bases:
                    candidates[base].update(impl for impl in impls if impl != base)

    added = 0
    refused = 0
    for base in sorted(candidates):
        impls = candidates[base]
        if len(impls) > max_implementations:
            refused += 1
            continue
        for impl in sorted(impls):
            if graph.has_edge(base, impl):
                continue
            graph.add_edge(
                base,
                impl,
                edge_type="dispatches_to",
                confidence=_DISPATCH_CONFIDENCE,
            )
            added += 1

    log.info("dispatch_edges", added=added, bases=len(candidates), over_cap=refused)
    return added
