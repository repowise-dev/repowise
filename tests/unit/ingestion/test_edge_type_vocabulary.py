"""The declared edge-type vocabulary must stay true in both directions.

`EdgeType` in :mod:`repowise.core.ingestion.models` was decorative for most of
this repo's life: nothing checked it, so it drifted both ways at once. It
declared `has_property`, `method_overrides` and a bare `dynamic` that no
producer has ever emitted, and it omitted `reads`, `dynamic_uses`,
`dynamic_imports` and `dynamic_url_route`, which producers emit constantly
(6,750 rows across 42 local indexes).

That is not a cosmetic defect. Thirteen modules each wrote a private set of
"which edges are dependencies" *against the declaration*, so three of them
tested for the bare `"dynamic"` and matched none of the 6,153 real `dynamic_*`
edges, and `reads` reached exactly one of the thirteen. A vocabulary nothing
enforces produces consumers that are all subtly, invisibly wrong.

Two directions, two tests:

* **Nothing emits an undeclared type** — every `add_edge` call site in the
  packages passes an `edge_type=` the Literal knows.
* **Nothing consumes a type no one emits** — every module-level `*_EDGE_TYPES`
  set holds only real members, so a dead key like `"heritage"` cannot sit in a
  dependency set pretending to do work.

Ceiling: this is an AST check on *literal* arguments, so a call passing a
computed edge type (`add_dynamic_edges` builds one by prefixing) is recorded in
``_COMPUTED`` rather than verified. Typing is what covers those — the value
there comes from a `DynamicKind`-annotated field. mypy cannot do this test's
job instead: every `add_edge` in the tree ultimately lands on
``networkx.DiGraph.add_edge(**attrs)`` or ``GraphStore.add_edge(**attrs: Any)``,
so there is no parameter to annotate and no error for mypy to raise.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from repowise.core.ingestion.models import EDGE_TYPE_VALUES

_PACKAGES = pathlib.Path(__file__).resolve().parents[3] / "packages"

# Call sites that build the edge type instead of naming it. Each needs the
# reason it cannot be a literal, because "it's computed" is also what a call
# site looks like just before it starts emitting something undeclared.
_COMPUTED: frozenset[str] = frozenset(
    {
        # Prefixes a `DynamicKind` into `dynamic_<kind>`. The input vocabulary
        # is a Literal, so the output is closed even though it is not literal
        # here; `test_every_dynamic_kind_maps_into_the_vocabulary` pins it.
        "packages/core/src/repowise/core/ingestion/graph/_edges.py",
        # Replays persisted rows back onto a fresh graph. The edge type comes
        # from the database, which this test cannot reach; the rows were
        # written by the producers this test does check.
        "packages/core/src/repowise/core/ingestion/graph/_rehydrate.py",
        # Maps a `HeritageKind` to "extends"/"implements" via
        # `_heritage_kind_to_edge_type`, which is annotated to return EdgeType.
        "packages/core/src/repowise/core/ingestion/graph/_resolvers.py",
    }
)

# `add_edge` calls on graphs that are not the symbol/file dependency graph, so
# `EdgeType` does not govern them. Each is a separate model with its own
# vocabulary.
_OTHER_GRAPHS: frozenset[str] = frozenset(
    {
        # Cross-repo SystemGraph: `kind` is a SystemEdge kind ("package",
        # "co_change", contract-derived), a different vocabulary entirely.
        "packages/core/src/repowise/core/workspace/system_graph.py",
        "packages/core/src/repowise/core/workspace/architecture_metrics.py",
        # Scratch networkx graphs built for one algorithm and discarded:
        # community detection, cycle finding, split scoring, page levels.
        # They carry no edge_type at all.
        "packages/core/src/repowise/core/analysis/communities.py",
        "packages/core/src/repowise/core/analysis/health/refactoring/graph_signals.py",
        "packages/core/src/repowise/core/analysis/health/refactoring/split_file.py",
        "packages/core/src/repowise/core/generation/page_generator/levels.py",
        # Interface/passthrough declarations, not producers.
        "packages/core/src/repowise/core/persistence/_interfaces/graph_store.py",
        "packages/core/src/repowise/core/persistence/stores/in_process_graph_store.py",
        "packages/core/src/repowise/core/persistence/coordinator.py",
    }
)


def _python_files() -> list[pathlib.Path]:
    return [p for p in _PACKAGES.rglob("*.py") if "node_modules" not in p.parts]


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(_PACKAGES.parents[0]).as_posix()


def _edge_type_literals() -> dict[str, set[str]]:
    """Map of repo-relative path -> literal `edge_type=` values it passes to add_edge."""
    found: dict[str, set[str]] = {}
    for path in _python_files():
        rel = _rel(path)
        if rel in _OTHER_GRAPHS or rel in _COMPUTED:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "add_edge":
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "edge_type"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    found.setdefault(rel, set()).add(kw.value.value)
    return found


def _edge_type_set_members() -> dict[str, set[str]]:
    """Map of `path::CONSTANT_NAME` -> string members of every *_EDGE_TYPES collection."""
    found: dict[str, set[str]] = {}
    for path in _python_files():
        rel = _rel(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if not any(n.endswith("_EDGE_TYPES") for n in names):
                continue
            value = node.value
            if value is None:
                continue
            # frozenset({...}) / set({...}) unwrap to their single argument.
            if isinstance(value, ast.Call) and value.args:
                value = value.args[0]
            if not isinstance(value, ast.Set | ast.List | ast.Tuple):
                continue  # derived from another set (`A | B`), checked at its source
            members = {
                el.value
                for el in value.elts
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            }
            if members:
                found[f"{rel}::{names[0]}"] = members
    return found


def test_nothing_emits_an_undeclared_edge_type() -> None:
    offenders = {
        path: sorted(values - EDGE_TYPE_VALUES)
        for path, values in _edge_type_literals().items()
        if values - EDGE_TYPE_VALUES
    }
    assert not offenders, (
        "add_edge call site(s) emitting an edge type the EdgeType Literal does not declare:\n"
        + "\n".join(f"  {p}: {', '.join(v)}" for p, v in sorted(offenders.items()))
        + "\n\nAdd it to EdgeType in repowise.core.ingestion.models, or emit a declared type."
        " An undeclared type is invisible to every consumer that filters on the vocabulary."
    )


def test_no_consumer_set_holds_a_type_nothing_emits() -> None:
    offenders = {
        name: sorted(members - EDGE_TYPE_VALUES)
        for name, members in _edge_type_set_members().items()
        if members - EDGE_TYPE_VALUES
    }
    assert not offenders, (
        "Edge-type set(s) containing a type no producer emits:\n"
        + "\n".join(f"  {n}: {', '.join(v)}" for n, v in sorted(offenders.items()))
        + "\n\nA dead key does no work and hides that the set is incomplete —"
        " `_DEPENDENCY_EDGE_TYPES` held 'heritage' while missing every real dynamic_* type."
        " Remove it, or add the type to EdgeType if something really does emit it."
    )


def test_computed_call_sites_still_compute() -> None:
    """Every ``_COMPUTED`` exemption must still contain an add_edge, so the list cannot rot."""
    stale = sorted(
        rel
        for rel in _COMPUTED
        if "add_edge" not in (_PACKAGES.parents[0] / rel).read_text(encoding="utf-8")
    )
    assert not stale, (
        "These no longer call add_edge — remove them from _COMPUTED:\n"
        + "\n".join(f"  {p}" for p in stale)
    )


def test_every_dynamic_kind_maps_into_the_vocabulary() -> None:
    """The one computed edge type that matters, pinned to its real transform.

    Mirrors `EdgesMixin.add_dynamic_edges`. If a new `DynamicKind` lands
    without its prefixed form being declared, this fails rather than shipping
    an edge type no consumer can see.
    """
    from typing import get_args

    from repowise.core.ingestion.models import DynamicKind

    for kind in get_args(DynamicKind):
        emitted = kind if kind.startswith("dynamic") else f"dynamic_{kind}"
        assert emitted in EDGE_TYPE_VALUES, (
            f"DynamicKind {kind!r} reaches the graph as {emitted!r}, which EdgeType"
            " does not declare."
        )


def test_edge_verb_covers_the_vocabulary() -> None:
    """Every edge type must have a C4 label, and every label a real edge type.

    `relation_label` falls through to "depends on" for an unknown type, which
    is silent — `framework` and `dynamic_uses` rendered that way for their
    whole life, and `type_use` (6,596 rows locally) still did until this test
    existed. Both directions, so the map cannot grow a dead key either.
    """
    from repowise.server.services.c4_builder.labels import _EDGE_VERB, _VERB_PRIORITY

    assert not (EDGE_TYPE_VALUES - _EDGE_VERB.keys()), (
        "edge type(s) with no C4 verb — they render as the vague 'depends on': "
        f"{sorted(EDGE_TYPE_VALUES - _EDGE_VERB.keys())}"
    )
    assert not (_EDGE_VERB.keys() - EDGE_TYPE_VALUES), (
        f"C4 verb(s) keyed on a type nothing emits: {sorted(_EDGE_VERB.keys() - EDGE_TYPE_VALUES)}"
    )
    assert not (set(_EDGE_VERB.values()) - set(_VERB_PRIORITY)), (
        "verb(s) missing from _VERB_PRIORITY, so an aggregated edge cannot rank them: "
        f"{sorted(set(_EDGE_VERB.values()) - set(_VERB_PRIORITY))}"
    )


def test_edge_type_map_covers_the_vocabulary() -> None:
    """The knowledge-graph export must map every dependency edge type.

    `build_knowledge_graph_skeleton` drops an unmapped type silently, and six
    real ones were missing — framework and the dynamic_* family among them —
    so those relations never reached the graph at all.
    """
    from repowise.core.analysis.knowledge_graph import _EDGE_TYPE_MAP
    from repowise.core.ingestion.models import TEMPORAL_EDGE_TYPES

    # Temporal edges are excluded on purpose; see the comment on _EDGE_TYPE_MAP.
    expected = EDGE_TYPE_VALUES - TEMPORAL_EDGE_TYPES
    assert not (expected - _EDGE_TYPE_MAP.keys()), (
        "edge type(s) silently dropped from the knowledge-graph export: "
        f"{sorted(expected - _EDGE_TYPE_MAP.keys())}"
    )
    assert not (_EDGE_TYPE_MAP.keys() - EDGE_TYPE_VALUES), (
        f"export mapping keyed on a type nothing emits: {sorted(_EDGE_TYPE_MAP.keys() - EDGE_TYPE_VALUES)}"
    )
    assert not (_EDGE_TYPE_MAP.keys() & TEMPORAL_EDGE_TYPES), (
        "a temporal edge reached the dependency export — that is how a co-change"
        " partner starts looking like an import"
    )


@pytest.mark.parametrize("phantom", ["has_property", "method_overrides", "dynamic"])
def test_the_removed_phantoms_stay_removed(phantom: str) -> None:
    """Each measured at 0 rows across 42 local indexes with no producer in the tree.

    Named individually so a re-add has to argue with the specific string rather
    than a count.
    """
    assert phantom not in EDGE_TYPE_VALUES
