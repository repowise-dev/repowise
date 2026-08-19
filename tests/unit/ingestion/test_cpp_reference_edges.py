"""C/C++ functions named without being called (#1602).

A dispatch table, a callback field and a registration macro all mention a
function as a plain identifier. None of them is a call expression, so the
named function carried no inbound edge and read as a ``safe_to_delete``
unused export, which swept up whole handler and interop layers.

The precision half matters as much as the recall half: the captures are broad
syntactic positions, and C++ names its getters exactly like the locals that
feed them, so the tests below pin what must *not* produce an edge.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _build(repo: Path):
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _referrers_of(graph, symbol_id: str) -> set[str]:
    if not graph.has_node(symbol_id):
        return set()
    return {
        pred
        for pred in graph.predecessors(symbol_id)
        if graph[pred][symbol_id].get("edge_type") == "references"
    }


def _reference_edges(graph) -> list[tuple[str, str]]:
    return [(u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "references"]


class TestDispatchTable:
    def test_function_pointer_in_table_is_a_reference(self, tmp_path: Path) -> None:
        (tmp_path / "registry.cpp").write_text(
            "struct Entry { const char* name; void (*fn)(); };\n"
            "void HandleAlpha() {}\n"
            "void HandleBeta() {}\n"
            'Entry g_table[] = { {"a", HandleAlpha}, {"b", HandleBeta} };\n'
        )
        graph = _build(tmp_path)
        assert _referrers_of(graph, "registry.cpp::HandleAlpha")
        assert _referrers_of(graph, "registry.cpp::HandleBeta")

    def test_nested_initialiser_row(self, tmp_path: Path) -> None:
        (tmp_path / "nodes.cpp").write_text(
            "void InitAddNode() {}\n"
            'struct Init { int type; struct { void (*fn)(); const char* cat; } v; };\n'
            'Init g_inits[] = { { 1, { InitAddNode, "math" } } };\n'
        )
        graph = _build(tmp_path)
        assert _referrers_of(graph, "nodes.cpp::InitAddNode")

    def test_designated_initialiser(self, tmp_path: Path) -> None:
        (tmp_path / "ops.c").write_text(
            "int my_write(void) { return 0; }\n"
            "struct ops { int (*write)(void); };\n"
            "static struct ops g_ops = { .write = my_write };\n"
        )
        graph = _build(tmp_path)
        assert _referrers_of(graph, "ops.c::my_write")


class TestRegistrationMacro:
    def test_screaming_case_macro_argument_is_a_reference(self, tmp_path: Path) -> None:
        (tmp_path / "hooks.cpp").write_text(
            "#define REGISTER_HOOK(fn) RegisterHook(#fn, fn)\n"
            "void RegisterHook(const char* n, void (*f)());\n"
            "void OnFrameStart() {}\n"
            "void InstallHooks() { REGISTER_HOOK(OnFrameStart); }\n"
        )
        graph = _build(tmp_path)
        assert _referrers_of(graph, "hooks.cpp::OnFrameStart") == {"hooks.cpp::InstallHooks"}

    def test_multi_argument_macro_is_not_a_registration(self, tmp_path: Path) -> None:
        # gtest spells its assertions in SCREAMING_CASE too, and they take
        # values. Registering something registers one thing.
        (tmp_path / "t.cpp").write_text(
            "int capacity() { return 1; }\n"
            "void body() { int capacity = 2; EXPECT_EQ(capacity, 2); }\n"
        )
        graph = _build(tmp_path)
        assert _referrers_of(graph, "t.cpp::capacity") == set()

    def test_lowercase_callee_argument_is_not_a_reference(self, tmp_path: Path) -> None:
        # Without the SCREAMING_CASE guard this would claim every identifier
        # argument of every ordinary call in the codebase.
        (tmp_path / "plain.cpp").write_text(
            "void helper() {}\n"
            "void compute(int v);\n"
            "void run() { int helper = 1; compute(helper); }\n"
        )
        graph = _build(tmp_path)
        assert _referrers_of(graph, "plain.cpp::helper") == set()


class TestPrecision:
    def test_plain_local_assignment_is_not_a_reference(self, tmp_path: Path) -> None:
        # ``offset_ = offset`` beside an ``offset()`` accessor is the shape
        # that made half of leveldb look cross-referenced.
        (tmp_path / "handle.cpp").write_text(
            "void offset() {}\n"
            "void store(int v) { int offset_ = 0; int offset = v; offset_ = offset; }\n"
        )
        graph = _build(tmp_path)
        assert _referrers_of(graph, "handle.cpp::offset") == set()

    def test_local_aggregate_colliding_with_a_free_function(self, tmp_path: Path) -> None:
        # ``count`` and ``size`` are both ordinary locals and plausible free
        # function names. Inside a function body the braces are a local
        # aggregate, never a dispatch table.
        (tmp_path / "agg.cpp").write_text(
            "int count() { return 1; }\n"
            "int size() { return 2; }\n"
            "void body() { int count = 1, size = 2; int arr[] = {count, size}; (void)arr; }\n"
        )
        graph = _build(tmp_path)
        assert _referrers_of(graph, "agg.cpp::count") == set()
        assert _referrers_of(graph, "agg.cpp::size") == set()

    def test_method_target_is_never_referenced_by_bare_name(self, tmp_path: Path) -> None:
        # A bare identifier cannot name a member function in C++; it needs
        # ``&Class::method``. A plain name matching a method is a collision.
        (tmp_path / "m.cpp").write_text(
            "struct Iter { int value() { return 1; } };\n"
            "struct Cfg { int value; };\n"
            "void use(Cfg& c, int value) { c.value = value; }\n"
        )
        graph = _build(tmp_path)
        assert _referrers_of(graph, "m.cpp::Iter::value") == set()

    def test_enum_value_in_a_table_is_not_a_reference(self, tmp_path: Path) -> None:
        # ``NodeType::Add`` parses as a qualified_identifier, so the bare
        # identifier capture never sees it.
        (tmp_path / "e.cpp").write_text(
            "namespace NodeType { enum E { Add }; }\n"
            "int g_t[] = { NodeType::Add };\n"
        )
        graph = _build(tmp_path)
        assert not [e for e in _reference_edges(graph) if e[1].endswith("::Add")]

    def test_other_languages_emit_no_reference_edges(self, tmp_path: Path) -> None:
        # Only the C/C++ queries define ``@reference.name``; everything else
        # pays a dict lookup and produces nothing.
        (tmp_path / "mod.py").write_text("def handler():\n    pass\n\nTABLE = [handler]\n")
        graph = _build(tmp_path)
        assert _reference_edges(graph) == []


class TestDeadCodeEffect:
    def test_referenced_handler_is_not_an_unused_export(self, tmp_path: Path) -> None:
        from repowise.core.analysis.dead_code import DeadCodeAnalyzer

        (tmp_path / "registry.cpp").write_text(
            "struct Entry { const char* name; void (*fn)(); };\n"
            "void HandleAlpha() {}\n"
            'Entry g_table[] = { {"a", HandleAlpha} };\n'
        )
        graph = _build(tmp_path)
        report = DeadCodeAnalyzer(graph).analyze()
        flagged = {f.symbol_name for f in report.findings if f.kind == "unused_export"}
        assert "HandleAlpha" not in flagged
