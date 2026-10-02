"""Tests for the Move Method refactoring detector (feature envy).

The detector reads the method/class membership and ``calls`` edges off the
in-memory graph (surfaced on ``RefactoringContext.graph``) and suggests
moving a method to the class it actually uses. Fixtures build a tiny NetworkX
graph directly so the entity sets — which class owns which member, who calls
whom — are explicit and the expected suggestion is unambiguous.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.health.refactoring import (
    RefactoringContext,
    detect_refactorings,
)
from repowise.core.analysis.health.refactoring.move_method import MoveMethodDetector


def _add_class(g: nx.DiGraph, file_path: str, cls: str, methods: list[str]) -> None:
    if file_path not in g:
        g.add_node(file_path, node_type="file")
    class_id = f"{file_path}::{cls}"
    g.add_node(class_id, node_type="symbol", kind="class", name=cls, file_path=file_path)
    for m in methods:
        mid = f"{file_path}::{cls}.{m}"
        g.add_node(
            mid,
            node_type="symbol",
            kind="method",
            name=m,
            parent_name=cls,
            file_path=file_path,
            start_line=1,
            end_line=20,
        )
        g.add_edge(class_id, mid, edge_type="has_method")
        g.add_edge(file_path, mid, edge_type="defines")


def _call(g: nx.DiGraph, src: str, dst: str) -> None:
    g.add_edge(src, dst, edge_type="calls")


def _ctx(g: nx.DiGraph, file_path: str) -> RefactoringContext:
    return RefactoringContext(file_path=file_path, language="python", nloc=40, graph=g)


def _detect(g: nx.DiGraph, file_path: str) -> list:
    return [
        s for s in detect_refactorings(_ctx(g, file_path)) if s.refactoring_type == "move_method"
    ]


def _envy_graph() -> nx.DiGraph:
    """C.envious calls 3 of T's members and 0 of its own — textbook envy."""
    g = nx.DiGraph()
    _add_class(g, "c.py", "C", ["envious", "helper"])
    _add_class(g, "t.py", "T", ["alpha", "beta", "gamma"])
    for m in ("alpha", "beta", "gamma"):
        _call(g, "c.py::C.envious", f"t.py::T.{m}")
    return g


def test_feature_envy_suggests_move_to_target_class():
    out = _detect(_envy_graph(), "c.py")
    assert len(out) == 1
    s = out[0]
    assert s.refactoring_type == "move_method"
    assert s.target_symbol == "C.envious"
    assert s.plan == {
        "method": "envious",
        "from_class": "C",
        "to_class": "T",
        "to_file": "t.py",
    }
    assert s.evidence["foreign_calls"] == 3
    assert s.evidence["own_calls"] == 0
    assert s.evidence["target_distance"] < s.evidence["own_distance"]
    assert s.confidence == "high"


def test_unreliable_calls_cannot_prove_feature_envy():
    graph = _envy_graph()
    for _source, _target, data in graph.edges(data=True):
        if data.get("edge_type") == "calls":
            data["resolution_origin"] = "global_unique"
    assert _detect(graph, "c.py") == []


def test_noncall_execution_edges_cannot_prove_feature_envy():
    graph = _envy_graph()
    for _source, _target, data in graph.edges(data=True):
        if data.get("edge_type") == "calls":
            data["edge_type"] = "dispatches_to"
    assert _detect(graph, "c.py") == []


def test_method_that_uses_its_own_class_is_not_envious():
    g = _envy_graph()
    # envious now also calls 2 of its own class's members → no longer clearly
    # foreign-leaning (own_calls above the max-own gate).
    _add_class(g, "c.py", "C", ["envious", "helper", "h2"])
    _call(g, "c.py::C.envious", "c.py::C.helper")
    _call(g, "c.py::C.envious", "c.py::C.h2")
    assert _detect(g, "c.py") == []


def test_single_foreign_call_below_threshold_is_ignored():
    g = nx.DiGraph()
    _add_class(g, "c.py", "C", ["m"])
    _add_class(g, "t.py", "T", ["alpha", "beta"])
    _call(g, "c.py::C.m", "t.py::T.alpha")  # only one foreign member
    assert _detect(g, "c.py") == []


def test_dunder_methods_never_move():
    g = nx.DiGraph()
    _add_class(g, "c.py", "C", ["__init__"])
    _add_class(g, "t.py", "T", ["alpha", "beta", "gamma"])
    for m in ("alpha", "beta", "gamma"):
        _call(g, "c.py::C.__init__", f"t.py::T.{m}")
    assert _detect(g, "c.py") == []


def test_nearest_of_two_foreign_classes_wins():
    g = nx.DiGraph()
    _add_class(g, "c.py", "C", ["m"])
    _add_class(g, "t.py", "T", ["a", "b", "c"])
    _add_class(g, "u.py", "U", ["x", "y", "z", "w", "v"])
    # m touches all 3 of T (small class → distance 0) but only 2 of U's 5.
    for member in ("a", "b", "c"):
        _call(g, "c.py::C.m", f"t.py::T.{member}")
    for member in ("x", "y"):
        _call(g, "c.py::C.m", f"u.py::U.{member}")
    out = _detect(g, "c.py")
    assert len(out) == 1
    assert out[0].plan["to_class"] == "T"


def test_no_graph_yields_no_suggestions():
    ctx = RefactoringContext(file_path="c.py", language="python", nloc=40, graph=None)
    assert MoveMethodDetector().detect(ctx) == []


def test_calling_two_methods_of_a_huge_class_is_not_envy():
    # A method that touches 2 members of a 40-member god class is far from it
    # (Jaccard distance ~0.95) — normal collaboration, not envy.
    g = nx.DiGraph()
    _add_class(g, "c.py", "C", ["m"])
    _add_class(g, "big.py", "Big", [f"meth{i}" for i in range(40)])
    _call(g, "c.py::C.m", "big.py::Big.meth0")
    _call(g, "c.py::C.m", "big.py::Big.meth1")
    assert _detect(g, "c.py") == []


def test_method_in_test_file_is_skipped():
    # A test method exercising the class under test is not a move candidate.
    g = nx.DiGraph()
    _add_class(g, "tests/test_thing.py", "ThingTest", ["test_it"])
    _add_class(g, "t.py", "T", ["alpha", "beta", "gamma"])
    for m in ("alpha", "beta", "gamma"):
        _call(g, "tests/test_thing.py::ThingTest.test_it", f"t.py::T.{m}")
    assert _detect(g, "tests/test_thing.py") == []


def test_target_in_test_file_is_rejected():
    # Never propose moving production code into a test class.
    g = nx.DiGraph()
    _add_class(g, "c.py", "C", ["m"])
    _add_class(g, "tests/test_t.py", "T", ["alpha", "beta", "gamma"])
    for m in ("alpha", "beta", "gamma"):
        _call(g, "c.py::C.m", f"tests/test_t.py::T.{m}")
    assert _detect(g, "c.py") == []


def test_deterministic_and_stable_order():
    g = _envy_graph()
    # A second envious method in the same file → two suggestions, stable order.
    _add_class(g, "c.py", "C", ["envious", "envious2", "helper"])
    g.add_node(
        "c.py::C.envious2",
        node_type="symbol",
        kind="method",
        name="envious2",
        parent_name="C",
        file_path="c.py",
        start_line=1,
        end_line=20,
    )
    g.add_edge("c.py::C", "c.py::C.envious2", edge_type="has_method")
    g.add_edge("c.py", "c.py::C.envious2", edge_type="defines")
    for m in ("alpha", "beta", "gamma"):
        _call(g, "c.py::C.envious2", f"t.py::T.{m}")
    first = [s.target_symbol for s in _detect(g, "c.py")]
    second = [s.target_symbol for s in _detect(g, "c.py")]
    assert first == second
    assert len(first) == 2


# -- methods-by-file index equivalence -------------------------------------------
#
# The engine precomputes graph_signals.build_methods_by_file once per pass; it
# must agree with the detector's own per-file derivation (defines edges with a
# prefix-scan fallback) for every file, and the detector must produce the same
# suggestions whichever path supplies the methods.


def _index_fixture_graph() -> nx.DiGraph:
    g = _envy_graph()
    # A fallback-only file: method symbol nodes with NO defines edges.
    g.add_node("legacy.py", node_type="file")
    g.add_node(
        "legacy.py::L.only",
        node_type="symbol",
        kind="method",
        name="only",
        parent_name="L",
        file_path="legacy.py",
    )
    # A methodless file and a non-method symbol that must never appear.
    g.add_node("empty.py", node_type="file")
    g.add_node("t.py::free_fn", node_type="symbol", kind="function", name="free_fn")
    return g


def test_methods_index_matches_per_file_derivation():
    from repowise.core.analysis.health.refactoring.graph_signals import build_methods_by_file

    g = _index_fixture_graph()
    index = build_methods_by_file(g)
    det = MoveMethodDetector()
    files = [n for n, d in g.nodes(data=True) if d.get("node_type") == "file"]
    assert files
    for f in files:
        assert list(index.get(f, ())) == det._methods_in_file(g, f)
    # The fallback-only file is served by the prefix scan.
    assert index["legacy.py"] == ("legacy.py::L.only",)
    assert "empty.py" not in index


def test_detector_equal_with_and_without_provided_index():
    from repowise.core.analysis.health.refactoring.graph_signals import build_methods_by_file

    g = _envy_graph()
    index = build_methods_by_file(g)
    for f in ("c.py", "t.py"):
        derived = MoveMethodDetector().detect(_ctx(g, f))
        provided = MoveMethodDetector().detect(
            RefactoringContext(
                file_path=f,
                language="python",
                nloc=40,
                graph=g,
                file_methods=index.get(f, ()),
            )
        )
        assert [(s.target_symbol, s.plan) for s in provided] == [
            (s.target_symbol, s.plan) for s in derived
        ]


def test_a_class_the_method_instantiates_is_not_a_move_target():
    # ``result = T(); result.alpha(); ...``: the method produces T, it does not
    # envy it. The constructor call is a ``calls`` edge onto the class node.
    g = _envy_graph()
    _call(g, "c.py::C.envious", "t.py::T")
    assert _detect(g, "c.py") == []


def test_an_ancestor_of_the_own_class_is_not_a_move_target():
    g = _envy_graph()
    g.add_edge("c.py::C", "t.py::T", edge_type="extends")
    assert _detect(g, "c.py") == []


def test_a_method_whose_work_is_in_its_home_file_does_not_envy():
    # Three module-level helpers in c.py outweigh three calls on T.
    g = _envy_graph()
    for fn in ("load", "write", "render"):
        g.add_node(f"c.py::{fn}", node_type="symbol", kind="function", name=fn, file_path="c.py")
        _call(g, "c.py::C.envious", f"c.py::{fn}")
    assert _detect(g, "c.py") == []
    g.remove_node("c.py::render")
    assert len(_detect(g, "c.py")) == 1


# ---- contracts, factories and non-targets ---------------------------------


def _java_envy_graph(method: str = "envious", target: str = "T") -> nx.DiGraph:
    g = nx.DiGraph()
    _add_class(g, "C.java", "C", [method, "helper"])
    _add_class(g, f"{target}.java", target, ["alpha", "beta", "gamma"])
    for m in ("alpha", "beta", "gamma"):
        _call(g, f"C.java::C.{method}", f"{target}.java::{target}.{m}")
    return g


def _detect_java(g: nx.DiGraph, classes: list | None = None) -> list:
    ctx = RefactoringContext(
        file_path="C.java", language="java", nloc=40, graph=g, classes=classes or []
    )
    return [s for s in detect_refactorings(ctx) if s.refactoring_type == "move_method"]


def test_java_envy_control_still_fires():
    assert len(_detect_java(_java_envy_graph())) == 1


def test_override_annotated_method_never_moves():
    g = _java_envy_graph()
    g.nodes["C.java::C.envious"]["decorators"] = ["@Override\n    public"]
    assert _detect_java(g) == []


def test_runtime_contract_method_never_moves():
    assert _detect_java(_java_envy_graph(method="toString")) == []


def test_method_its_base_type_declares_never_moves():
    g = _java_envy_graph()
    _add_class(g, "Base.java", "Base", ["envious"])
    g.add_edge("C.java::C", "Base.java::Base", edge_type="implements")
    assert _detect_java(g) == []


def test_static_factory_on_target_counts_as_instantiation():
    g = _java_envy_graph()
    g.nodes["T.java::T.alpha"].update(name="of", signature="of(String key) -> T")
    assert _detect_java(g) == []


def test_getter_returning_the_target_type_is_not_instantiation():
    g = _java_envy_graph()
    g.nodes["T.java::T.alpha"]["signature"] = "alpha() -> T"
    assert len(_detect_java(g)) == 1


def test_python_override_decorator_and_classmethod_factory():
    g = _envy_graph()
    g.nodes["c.py::C.envious"]["decorators"] = ["@typing.override"]
    assert _detect(g, "c.py") == []
    g = _envy_graph()
    g.nodes["t.py::T.alpha"].update(signature="def alpha(cls) -> T", decorators=["@classmethod"])
    assert _detect(g, "c.py") == []
    # Control: plain Python envy still fires.
    assert len(_detect(_envy_graph(), "c.py")) == 1


def test_exception_interface_and_utility_targets_are_rejected():
    assert _detect_java(_java_envy_graph(target="ParseException")) == []
    assert _detect_java(_java_envy_graph(target="StringUtils")) == []
    g = _java_envy_graph()
    g.nodes["T.java::T"]["kind"] = "interface"
    assert _detect_java(g) == []
    # An enum carries real behaviour; it stays a valid target.
    g = _java_envy_graph()
    g.nodes["T.java::T"]["kind"] = "enum"
    assert len(_detect_java(g)) == 1


def test_method_sharing_its_class_state_does_not_move():
    from repowise.core.analysis.health.complexity import ClassComplexity, CohesionGroup

    cls = ClassComplexity(
        name="C",
        start_line=1,
        end_line=40,
        method_count=2,
        total_nloc=30,
        methods=[],
        lcom4=1,
        components=[CohesionGroup(methods=["envious", "helper"], fields=["settings"])],
    )
    assert _detect_java(_java_envy_graph(), classes=[cls]) == []
    stateless = CohesionGroup(methods=["envious"], fields=[])
    cls.components = [stateless]
    assert len(_detect_java(_java_envy_graph(), classes=[cls])) == 1


def test_method_calling_an_inherited_member_or_in_a_trait_impl_does_not_move():
    from repowise.core.analysis.health.complexity import ClassComplexity, CohesionGroup

    cls = ClassComplexity(
        name="C",
        start_line=1,
        end_line=40,
        method_count=2,
        total_nloc=30,
        methods=[],
        lcom4=1,
        components=[CohesionGroup(methods=["envious"], fields=[], calls=("base",))],
    )
    assert _detect_java(_java_envy_graph(), classes=[cls]) == []
    cls.components = []
    cls.contract_impl = True
    assert _detect_java(_java_envy_graph(), classes=[cls]) == []


# ---- keyword modifiers and partial classes (C#, Kotlin, VB.NET) ------------


def _envy_graph_in(ext: str, language: str) -> nx.DiGraph:
    g = nx.DiGraph()
    _add_class(g, f"C.{ext}", "C", ["Envious", "Helper"])
    _add_class(g, f"T.{ext}", "T", ["Alpha", "Beta", "Gamma"])
    for node in g.nodes.values():
        node["language"] = language
    for m in ("Alpha", "Beta", "Gamma"):
        _call(g, f"C.{ext}::C.Envious", f"T.{ext}::T.{m}")
    return g


def _detect_in(g: nx.DiGraph, file_path: str, language: str) -> list:
    ctx = RefactoringContext(file_path=file_path, language=language, nloc=40, graph=g)
    return [s for s in detect_refactorings(ctx) if s.refactoring_type == "move_method"]


def test_csharp_envy_control_still_fires():
    g = _envy_graph_in("cs", "csharp")
    g.nodes["C.cs::C.Envious"]["modifiers"] = ("public", "static", "async")
    assert len(_detect_in(g, "C.cs", "csharp")) == 1


def test_override_virtual_and_abstract_modifiers_never_move():
    for ext, language, modifiers in (
        ("cs", "csharp", ("protected", "override", "async")),
        ("cs", "csharp", ("public", "virtual")),
        ("cs", "csharp", ("public", "abstract")),
        ("kt", "kotlin", ("override",)),
        ("vb", "vbnet", ("public", "overrides")),
    ):
        g = _envy_graph_in(ext, language)
        g.nodes[f"C.{ext}::C.Envious"]["modifiers"] = modifiers
        assert _detect_in(g, f"C.{ext}", language) == [], modifiers


def test_csharp_static_class_is_never_a_move_target():
    g = _envy_graph_in("cs", "csharp")
    g.nodes["T.cs::T"]["modifiers"] = ("internal", "static")
    assert _detect_in(g, "C.cs", "csharp") == []
    # A Java ``static`` nested class has instances; it stays a target.
    g = _java_envy_graph()
    g.nodes["T.java::T"].update(language="java", modifiers=("static",))
    assert len(_detect_java(g)) == 1


def test_other_partial_fragments_of_the_own_class_are_home():
    g = _envy_graph_in("cs", "csharp")
    # ``T`` here is a second fragment of ``C`` declared in another file.
    g = nx.relabel_nodes(g, {f"T.cs::T.{m}": f"T.cs::C.{m}" for m in ("Alpha", "Beta", "Gamma")})
    g = nx.relabel_nodes(g, {"T.cs::T": "T.cs::C"})
    g.nodes["T.cs::C"]["name"] = "C"
    for m in ("Alpha", "Beta", "Gamma"):
        g.nodes[f"T.cs::C.{m}"]["parent_name"] = "C"
    assert len(_detect_in(g, "C.cs", "csharp")) == 1  # unlinked: reads as foreign
    g.add_edge(
        "C.cs", "T.cs", edge_type="imports", imported_names=["C"], hint_source="partial_class"
    )
    assert _detect_in(g, "C.cs", "csharp") == []
