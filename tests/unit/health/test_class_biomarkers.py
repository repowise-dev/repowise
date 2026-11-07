"""Tests for the class-level structural biomarkers: low_cohesion, god_class."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.god_class import GodClassDetector
from repowise.core.analysis.health.biomarkers.low_cohesion import LowCohesionDetector
from repowise.core.analysis.health.complexity import ClassComplexity, FunctionComplexity, walk_file


def _fn(name: str, *, nloc: int = 5, ccn: int = 1) -> FunctionComplexity:
    return FunctionComplexity(
        name=name, start_line=1, end_line=1 + nloc, ccn=ccn, max_nesting=0, cognitive=0, nloc=nloc
    )


def _cls(
    name: str,
    *,
    method_count: int,
    lcom4: int = 1,
    total_nloc: int = 50,
    methods: list[FunctionComplexity] | None = None,
    field_count: int = 0,
) -> ClassComplexity:
    methods = methods if methods is not None else [_fn(f"m{i}") for i in range(method_count)]
    return ClassComplexity(
        name=name,
        start_line=1,
        end_line=1 + total_nloc,
        method_count=method_count,
        total_nloc=total_nloc,
        methods=methods,
        lcom4=lcom4,
        max_method_ccn=max((m.ccn for m in methods), default=0),
        field_count=field_count,
    )


def _ctx(classes: list[ClassComplexity]) -> FileContext:
    return FileContext(
        file_path="src/example.py",
        language="python",
        nloc=200,
        has_test_file=False,
        module=None,
        class_metrics=classes,
    )


# ---- low_cohesion --------------------------------------------------------


def test_low_cohesion_flags_splintered_class():
    cls = _cls("Splintered", method_count=6, lcom4=3, field_count=4)
    out = LowCohesionDetector().detect(_ctx([cls]))
    assert len(out) == 1
    assert out[0].details["lcom4"] == 3
    assert out[0].severity == "high"  # lcom4 >= 3


def test_low_cohesion_severity_critical():
    cls = _cls("Massive", method_count=16, lcom4=5)
    out = LowCohesionDetector().detect(_ctx([cls]))
    assert out[0].severity == "critical"  # lcom4 >= 4 AND methods >= 15


def test_low_cohesion_skips_cohesive_class():
    cls = _cls("Cohesive", method_count=8, lcom4=1)
    assert LowCohesionDetector().detect(_ctx([cls])) == []


def test_low_cohesion_skips_tiny_class():
    # Splintered but too few methods to matter.
    cls = _cls("Tiny", method_count=3, lcom4=3)
    assert LowCohesionDetector().detect(_ctx([cls])) == []


def test_low_cohesion_no_classes_is_silent():
    assert LowCohesionDetector().detect(_ctx([])) == []


def test_low_cohesion_skips_overloaded_formatter_class():
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing for python")
    if _get_language("python") is None:
        pytest.skip("tree-sitter language pack missing for python")

    source = (
        b"from typing import overload\n\n"
        b"class Formatter:\n"
        b"    def __init__(self):\n"
        b"        self.value = 0\n"
        b"        self.other = 1\n\n"
        b"    @overload\n"
        b"    def format(self, x: int) -> str: ...\n"
        b"    @overload\n"
        b"    def format(self, x: str) -> str: ...\n"
        b"    @overload\n"
        b"    def format(self, x: float) -> str: ...\n"
        b"    def format(self, x):\n"
        b"        return str(self.value) + str(x)\n\n"
        b"    def reset(self):\n"
        b"        self.other = 0\n"
    )
    fc = walk_file("formatter.py", "python", source)
    assert fc.classes
    fmt = next(c for c in fc.classes if c.name == "Formatter")
    assert fmt.method_count == 3 and fmt.lcom4 == 1
    assert LowCohesionDetector().detect(_ctx([fmt])) == []


def _walk_one(path: str, language: str, source: str, name: str) -> ClassComplexity:
    from repowise.core.ingestion.parser import _get_language

    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    fc = walk_file(path, language, source.encode())
    return next(c for c in fc.classes if c.name == name)


_STATELESS_DIALECT = """
class Dialect(BaseDialect):
    literal_kinds = frozenset({"string"})

    def is_literal(self, node):
        return node.type in self.literal_kinds

    def sink_kind(self, name):
        return "query" if name == "execute" else None

    def loop_header(self, node):
        return node.child_by_field_name("condition")

    def param_names(self, fn):
        return self.enclosing_params(fn)

    def is_async_fn(self, fn):
        return fn.type == "async_function"

    def bindings_of(self, node):
        return [c for c in node.children if c.is_named]
"""


def test_low_cohesion_skips_stateless_hook_overrides():
    # Each hook touches no instance state: a static helper, not a class to extract.
    cls = _walk_one("dialect.py", "python", _STATELESS_DIALECT, "Dialect")
    assert cls.method_count == 6 and cls.lcom4 == 1
    assert LowCohesionDetector().detect(_ctx([cls])) == []
    # Every component stays listed, so move_method still sees the inherited call.
    assert len(cls.components) == 6
    assert any(g.calls == ("enclosing_params",) for g in cls.components)


def test_low_cohesion_still_counts_field_clusters_beside_stateless_helpers():
    source = """
class Report:
    def add_page(self, page):
        self.pages.append(page)

    def page_count(self):
        return len(self.pages)

    def add_tokens(self, n):
        self.tokens += n

    def cost(self):
        return self.tokens * 0.01

    def label(self, kind):
        return kind.title()

    def describe(self):
        return "report"
"""
    cls = _walk_one("report.py", "python", source, "Report")
    assert cls.lcom4 == 2  # the pages pair and the tokens pair, one field each
    [finding] = LowCohesionDetector().detect(_ctx([cls]))
    assert finding.details["lcom4"] == 2


def test_low_cohesion_skips_stateless_typescript_strategy():
    source = """
class Strategy extends Base {
  kinds = new Set(["string"]);
  isLiteral(n) { return this.kinds.has(n.type); }
  sinkKind(name) { return name === "execute" ? "query" : null; }
  loopHeader(n) { return n.childForFieldName("condition"); }
  isAsync(fn) { return fn.type === "async_function"; }
  bindings(n) { return n.namedChildren; }
}
"""
    cls = _walk_one("strategy.ts", "typescript", source, "Strategy")
    assert cls.method_count == 5 and cls.lcom4 == 1


# ---- god_class -----------------------------------------------------------


def _brainy_methods() -> list[FunctionComplexity]:
    methods = [_fn(f"m{i}") for i in range(15)]
    methods.append(_fn("brain", nloc=90, ccn=12))  # the brain method
    return methods


def test_god_class_flags_large_complex_class():
    cls = _cls("God", method_count=16, total_nloc=320, methods=_brainy_methods())
    out = GodClassDetector().detect(_ctx([cls]))
    assert len(out) == 1
    assert out[0].details["total_nloc"] == 320
    assert out[0].severity == "high"  # total_nloc >= 300


def test_god_class_requires_a_brain_method():
    # Large + many methods but every method is flat → not a god class.
    flat = [_fn(f"m{i}", nloc=12, ccn=2) for i in range(16)]
    cls = _cls("BigButFlat", method_count=16, total_nloc=320, methods=flat)
    assert GodClassDetector().detect(_ctx([cls])) == []


def test_god_class_requires_size_and_method_count():
    # Has a brain method but is small / few methods.
    cls = _cls("Small", method_count=4, total_nloc=120, methods=[_fn("brain", nloc=90, ccn=12)])
    assert GodClassDetector().detect(_ctx([cls])) == []


def _god(name: str, *, init: bool = False) -> ClassComplexity:
    methods = _brainy_methods() + ([_fn("__init__")] if init else [])
    return _cls(name, method_count=len(methods), total_nloc=320, methods=methods)


def test_god_class_skips_stateless_python_mixin():
    assert GodClassDetector().detect(_ctx([_god("ResolveMixin")])) == []


@pytest.mark.parametrize(("name", "init"), [("StatefulMixin", True), ("Resolver", False)])
def test_god_class_still_flags_stateful_mixin_and_plain_class(name: str, init: bool):
    assert len(GodClassDetector().detect(_ctx([_god(name, init=init)]))) == 1


def test_god_class_mixin_rule_is_python_only():
    ctx = _ctx([_god("ResolveMixin")])
    ctx.language = "typescript"
    assert len(GodClassDetector().detect(ctx)) == 1


def test_god_class_skips_partial_type_split_across_files():
    # ``Encoder`` stands for a partial whose only other part is source
    # generated: no co-fragment edge, so the file holds the whole class.
    ctx = _ctx([_god("StdAdoDelegate"), _god("Encoder")])
    ctx.split_partial_types = frozenset({"StdAdoDelegate"})
    assert [r.function_name for r in GodClassDetector().detect(ctx)] == ["Encoder"]


def test_split_partial_types_reads_partial_class_edges():
    import networkx as nx

    from repowise.core.analysis.health.refactoring.graph_signals import (
        build_split_partial_types,
    )

    g = nx.DiGraph()
    # The reverse direction already had a plain import, so the linker added one edge.
    g.add_edge("A.Jobs.cs", "A.cs", edge_type="imports", imported_names=["X"])
    g.add_edge("A.cs", "A.Jobs.cs", edge_type="imports", imported_names=["A"], hint_source="partial_class")
    g.add_edge("A.cs", "B.cs", edge_type="imports", imported_names=["B"])
    assert build_split_partial_types(g) == {
        "A.cs": frozenset({"A"}),
        "A.Jobs.cs": frozenset({"A"}),
    }
    assert build_split_partial_types(None) == {}


def test_god_class_critical_severity():
    cls = _cls("Behemoth", method_count=26, total_nloc=450, methods=_brainy_methods())
    out = GodClassDetector().detect(_ctx([cls]))
    assert out[0].severity == "critical"  # total_nloc >= 400 AND methods >= 25
