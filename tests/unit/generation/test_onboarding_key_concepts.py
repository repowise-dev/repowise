"""Symbol-level Key Concepts selection + grounding + self-heal.

These cover the reworked Key Concepts builder (rank by symbol-graph signals,
prefer domain nouns, spread across clusters, ground relationships), the
cross-cutting grounding post-check, and the generation-version self-heal.
None of them need an API key.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import networkx as nx

from repowise.core.generation import onboarding
from repowise.core.generation.models import compute_source_hash
from repowise.core.generation.onboarding.grounding import check_grounding, collect_known
from repowise.core.generation.onboarding.signals import OnboardingSignals
from repowise.core.generation.onboarding.slots import (
    ONBOARDING_GENERATION_VERSION,
    SLOT_KEY_CONCEPTS,
)
from repowise.core.generation.onboarding.subkinds.key_concepts import (
    ConceptSymbol,
    KeyConceptsContext,
)
from repowise.core.ingestion.models import FileInfo, ParsedFile, RepoStructure, Symbol

# ---------------------------------------------------------------------------
# Fixture builders: a ParsedFile + a real networkx symbol graph so the builder
# exercises its symbol-signal path (cross-file callers, symbol pagerank).
# ---------------------------------------------------------------------------


def _sym(path: str, name: str, kind: str, *, exported: bool = False, doc: str = "") -> Symbol:
    return Symbol(
        id=f"{path}::{name}",
        name=name,
        qualified_name=f"{path.replace('/', '.')}::{name}",
        kind=kind,
        signature=f"{kind} {name}",
        start_line=1,
        end_line=10,
        docstring=doc or None,
        decorators=[],
        visibility="public",
        is_async=False,
        complexity_estimate=1,
        language="python",
        parent_name="Owner" if kind == "method" else None,
        is_exported_symbol=exported,
    )


def _file(path: str, symbols: list[Symbol], *, is_test: bool = False) -> ParsedFile:
    fi = FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="python",
        size_bytes=512,
        git_hash="abc",
        last_modified=datetime(2026, 1, 1, tzinfo=UTC),
        is_test=is_test,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return ParsedFile(
        file_info=fi,
        symbols=symbols,
        imports=[],
        exports=[s.name for s in symbols],
        docstring=None,
        parse_errors=[],
        content_hash="abc",
    )


def _graph_builder(files: list[ParsedFile], edges: list[tuple[str, str, str]]):
    """Build a fake graph_builder backed by a real nx.DiGraph.

    *edges* are ``(source_id, target_id, edge_type)`` triples; symbol PageRank
    is computed on the call/heritage subgraph, matching production.
    """
    g = nx.DiGraph()
    for pf in files:
        g.add_node(pf.file_info.path, node_type="file", is_test=pf.file_info.is_test)
        for s in pf.symbols:
            g.add_node(
                s.id,
                node_type="symbol",
                kind=s.kind,
                name=s.name,
                file_path=pf.file_info.path,
                is_exported_symbol=s.is_exported_symbol,
                docstring=s.docstring or "",
            )
    for src, dst, et in edges:
        g.add_edge(src, dst, edge_type=et)

    concept_edges = [
        (u, v)
        for u, v, d in g.edges(data=True)
        if d.get("edge_type") in ("calls", "extends", "implements")
    ]
    sub = nx.DiGraph()
    sub.add_nodes_from(n for n, d in g.nodes(data=True) if d.get("node_type") == "symbol")
    sub.add_edges_from(concept_edges)
    pr = nx.pagerank(sub) if sub.number_of_edges() else {n: 0.0 for n in sub.nodes()}

    return SimpleNamespace(
        graph=lambda: g,
        symbol_pagerank=lambda: pr,
        community_info=lambda: {},
        execution_flows=lambda: SimpleNamespace(flows=[]),
    )


def _signals(
    files, graph_builder, *, kg_layers=(), layer_order=(), community=None
) -> OnboardingSignals:
    paths = [f.file_info.path for f in files]
    return OnboardingSignals(
        repo_name="testrepo",
        repo_structure=RepoStructure(
            is_monorepo=False,
            packages=[],
            root_language_distribution={"python": 1.0},
            total_files=len(files),
            total_loc=len(files) * 50,
            entry_points=[],
        ),
        parsed_files=tuple(files),
        source_map={},
        graph_builder=graph_builder,
        pagerank={p: 0.1 for p in paths},
        betweenness={p: 0.0 for p in paths},
        community=community or dict.fromkeys(paths, 0),
        sccs=(),
        kg_layers=kg_layers,
        layer_order=layer_order,
    )


# ---------------------------------------------------------------------------
# Item 1: symbol-level ranking + filtering + spread
# ---------------------------------------------------------------------------


def _repo_with_layers():
    """Two layers, one containing a class with many cross-file callers plus a
    pile of methods/dunders from a single file (the old failure mode)."""
    core = _file(
        "core/registry.py",
        [
            _sym("core/registry.py", "LanguageRegistry", "class", doc="Central registry."),
            _sym("core/registry.py", "__init__", "method"),
            _sym("core/registry.py", "get", "method"),
            _sym("core/registry.py", "from_extension", "method"),
            _sym("core/registry.py", "import_support_map", "method"),
        ],
    )
    spec = _file(
        "core/spec.py", [_sym("core/spec.py", "LanguageSpec", "class", doc="One language.")]
    )
    parser = _file(
        "core/parser.py", [_sym("core/parser.py", "ASTParser", "class", doc="Parses source.")]
    )
    store = _file(
        "store/db.py", [_sym("store/db.py", "VectorStore", "class", doc="Persists vectors.")]
    )
    search = _file("store/search.py", [_sym("store/search.py", "FullTextSearch", "class")])
    files = [core, spec, parser, store, search]

    # Callers from many other files → high cross-file in-degree on the classes.
    edges: list[tuple[str, str, str]] = []
    for i in range(9):
        caller = f"caller/c{i}.py"
        files.append(_file(caller, [_sym(caller, f"use{i}", "function")]))
        edges.append((f"{caller}::use{i}", "core/registry.py::LanguageRegistry", "calls"))
        if i < 7:
            edges.append((f"{caller}::use{i}", "core/spec.py::LanguageSpec", "calls"))
        if i < 6:
            edges.append((f"{caller}::use{i}", "core/parser.py::ASTParser", "calls"))
        if i < 4:
            edges.append((f"{caller}::use{i}", "store/db.py::VectorStore", "calls"))
        if i < 3:
            edges.append((f"{caller}::use{i}", "store/search.py::FullTextSearch", "calls"))
    # The registry's own methods are only called locally (same file) → 0 cross-file.
    edges.append(("core/registry.py::LanguageRegistry", "core/registry.py::get", "calls"))

    kg_layers = (
        {
            "name": "Core",
            "nodeIds": ["file:core/registry.py", "file:core/spec.py", "file:core/parser.py"],
        },
        {"name": "Storage", "nodeIds": ["file:store/db.py", "file:store/search.py"]},
    )
    gb = _graph_builder(files, edges)
    return _signals(files, gb, kg_layers=kg_layers, layer_order=("Core", "Storage"))


def test_key_concepts_ranks_classes_over_methods() -> None:
    ctx = onboarding.get_spec(SLOT_KEY_CONCEPTS).build_context(_repo_with_layers())
    assert ctx is not None
    names = [c.name for c in ctx.concept_symbols]
    kinds = {c.kind for c in ctx.concept_symbols}
    # No constructor, dunder, or trivial accessor survived.
    assert "__init__" not in names
    assert "get" not in names
    assert "from_extension" not in names
    # Every chosen concept is a class (a domain noun), not a method.
    assert kinds == {"class"}
    # The most-depended-on class leads.
    assert names[0] == "LanguageRegistry"


def test_key_concepts_spreads_across_clusters() -> None:
    ctx = onboarding.get_spec(SLOT_KEY_CONCEPTS).build_context(_repo_with_layers())
    assert ctx is not None
    clusters = {c.cluster for c in ctx.concept_symbols}
    # Both layers are represented; one cluster does not own the page.
    assert clusters == {"Core", "Storage"}
    core_count = sum(1 for c in ctx.concept_symbols if c.cluster == "Core")
    assert core_count <= 3  # half-the-page cap on a single cluster


def test_key_concepts_grounds_relationships_from_edges() -> None:
    """A heritage edge among two chosen concepts is surfaced as a relation."""
    base = _file("m/base.py", [_sym("m/base.py", "BaseProvider", "class", doc="Interface.")])
    impl = _file("m/openai.py", [_sym("m/openai.py", "OpenAIProvider", "class", doc="Concrete.")])
    other = _file("m/client.py", [_sym("m/client.py", "ApiClient", "class", doc="Client.")])
    conf = _file("m/config.py", [_sym("m/config.py", "Settings", "class", doc="Config.")])
    files = [base, impl, other, conf]
    edges = [("m/openai.py::OpenAIProvider", "m/base.py::BaseProvider", "extends")]
    # Give every class cross-file callers so all are selected.
    for i in range(5):
        c = f"call/u{i}.py"
        files.append(_file(c, [_sym(c, f"u{i}", "function")]))
        for tgt in (
            "m/base.py::BaseProvider",
            "m/openai.py::OpenAIProvider",
            "m/client.py::ApiClient",
            "m/config.py::Settings",
        ):
            edges.append((f"{c}::u{i}", tgt, "calls"))
    ctx = onboarding.get_spec(SLOT_KEY_CONCEPTS).build_context(
        _signals(files, _graph_builder(files, edges))
    )
    assert ctx is not None
    rels = {(r.source, r.kind, r.target) for r in ctx.relations}
    assert ("OpenAIProvider", "extends", "BaseProvider") in rels


def test_key_concepts_excludes_test_helpers() -> None:
    prod = [
        _file("app/service.py", [_sym("app/service.py", "Service", "class")]),
        _file("app/model.py", [_sym("app/model.py", "Model", "class")]),
        _file("app/repo.py", [_sym("app/repo.py", "Repository", "class")]),
        _file("app/view.py", [_sym("app/view.py", "View", "class")]),
    ]
    test = _file(
        "tests/helpers.py", [_sym("tests/helpers.py", "MegaHelper", "class")], is_test=True
    )
    files = [*prod, test]
    edges = []
    # Give the test helper the MOST cross-file callers - it must still be excluded.
    for i in range(12):
        c = f"tests/t{i}.py"
        files.append(_file(c, [_sym(c, f"t{i}", "function")], is_test=True))
        edges.append((f"{c}::t{i}", "tests/helpers.py::MegaHelper", "calls"))
    for i in range(3):
        c = f"call/p{i}.py"
        files.append(_file(c, [_sym(c, f"p{i}", "function")]))
        for tgt in (
            "app/service.py::Service",
            "app/model.py::Model",
            "app/repo.py::Repository",
            "app/view.py::View",
        ):
            edges.append((f"{c}::p{i}", tgt, "calls"))
    ctx = onboarding.get_spec(SLOT_KEY_CONCEPTS).build_context(
        _signals(files, _graph_builder(files, edges))
    )
    assert ctx is not None
    assert "MegaHelper" not in {c.name for c in ctx.concept_symbols}


def test_key_concepts_gate_still_fails_below_minimum() -> None:
    files = [_file("a.py", [_sym("a.py", "Only", "class")])]
    ctx = onboarding.get_spec(SLOT_KEY_CONCEPTS).build_context(
        _signals(files, _graph_builder(files, []))
    )
    assert ctx is None


def test_key_concepts_uses_full_graph_when_parsed_files_empty() -> None:
    """On an incremental update, ``parsed_files`` is only the changed files
    (empty when nothing changed). The builder must still draw the whole
    concept set from the graph so the page self-heals on update."""
    import dataclasses

    # Simulate the incremental-update call shape: full graph, no parsed files.
    sig = dataclasses.replace(_repo_with_layers(), parsed_files=())
    ctx = onboarding.get_spec(SLOT_KEY_CONCEPTS).build_context(sig)
    assert ctx is not None
    names = {c.name for c in ctx.concept_symbols}
    assert "LanguageRegistry" in names
    assert len(ctx.concept_symbols) >= 4


# ---------------------------------------------------------------------------
# Item 1 self-heal: a changed concept set changes the rendered prompt hash
# ---------------------------------------------------------------------------


def _render_key_concepts(ctx) -> str:
    from pathlib import Path

    import jinja2

    templates_dir = (
        Path(__file__).resolve().parents[3] / "packages/core/src/repowise/core/generation/templates"
    )
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(templates_dir)),
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )
    return env.get_template("onboarding/key_concepts.j2").render(ctx=ctx, slot=SLOT_KEY_CONCEPTS)


def test_changed_concept_set_changes_source_hash() -> None:
    a = KeyConceptsContext(
        repo_name="r",
        concept_symbols=[
            ConceptSymbol(name="Alpha", kind="class", file_path="a.py", cluster="Core"),
            ConceptSymbol(name="Beta", kind="class", file_path="b.py", cluster="Core"),
            ConceptSymbol(name="Gamma", kind="class", file_path="c.py", cluster="Store"),
            ConceptSymbol(name="Delta", kind="class", file_path="d.py", cluster="Store"),
        ],
    )
    b = KeyConceptsContext(
        repo_name="r",
        concept_symbols=[
            ConceptSymbol(name="Alpha", kind="class", file_path="a.py", cluster="Core"),
            ConceptSymbol(
                name="Epsilon", kind="class", file_path="e.py", cluster="Core"
            ),  # changed
            ConceptSymbol(name="Gamma", kind="class", file_path="c.py", cluster="Store"),
            ConceptSymbol(name="Delta", kind="class", file_path="d.py", cluster="Store"),
        ],
    )
    ha = compute_source_hash(_render_key_concepts(a) + ONBOARDING_GENERATION_VERSION)
    hb = compute_source_hash(_render_key_concepts(b) + ONBOARDING_GENERATION_VERSION)
    assert ha != hb


def test_generation_version_folds_into_source_hash() -> None:
    prompt = "identical rendered prompt"
    h_v2 = compute_source_hash(prompt + "2")
    h_v3 = compute_source_hash(prompt + "3")
    assert h_v2 != h_v3
    # The shipped version is what pertype folds in; keep it a plain string.
    assert isinstance(ONBOARDING_GENERATION_VERSION, str)


# ---------------------------------------------------------------------------
# Item 2: grounding post-check
# ---------------------------------------------------------------------------


def _ctx_for_grounding() -> KeyConceptsContext:
    return KeyConceptsContext(
        repo_name="r",
        concept_symbols=[
            ConceptSymbol(
                name="GraphBuilder", kind="class", file_path="core/graph/builder.py", cluster="Core"
            ),
            ConceptSymbol(
                name="LanguageSpec",
                kind="class",
                file_path="core/languages/spec.py",
                cluster="Core",
            ),
        ],
    )


def test_grounding_passes_known_citations() -> None:
    ctx = _ctx_for_grounding()
    content = (
        "The `GraphBuilder` in `core/graph/builder.py` produces the graph that "
        "`LanguageSpec` (`spec.py`) describes. It supports `full` import mode."
    )
    cleaned, ungrounded = check_grounding(content, ctx)
    assert ungrounded == []
    assert cleaned == content  # nothing stripped


def test_grounding_catches_fabricated_path_and_symbol() -> None:
    ctx = _ctx_for_grounding()
    content = (
        "Ingestion starts in `ingestion/resolvers/dotnet/index.py`, the entry "
        "point, wired up by the `SecretOrchestrator` class."
    )
    cleaned, ungrounded = check_grounding(content, ctx)
    assert "ingestion/resolvers/dotnet/index.py" in ungrounded
    assert "SecretOrchestrator" in ungrounded
    # Demoted to plain text: the code-span backticks are gone.
    assert "`ingestion/resolvers/dotnet/index.py`" not in cleaned
    assert "`SecretOrchestrator`" not in cleaned
    # Text preserved (sentence not deleted).
    assert "dotnet/index.py" in cleaned
    assert "SecretOrchestrator" in cleaned


def test_grounding_cleans_reused_page_content() -> None:
    """The check runs on content, so a reused (cached) page carrying a stale
    fabrication is cleaned the same way a fresh one is."""
    ctx = _ctx_for_grounding()
    reused = "Cached page still cites the fabricated `PhantomAnalyzer` symbol."
    cleaned, ungrounded = check_grounding(reused, ctx)
    assert ungrounded == ["PhantomAnalyzer"]
    assert "`PhantomAnalyzer`" not in cleaned


def test_grounding_leaves_lowercase_words_alone() -> None:
    """Plain enum-value words in backticks (`full`, `none`) are not symbols."""
    ctx = _ctx_for_grounding()
    content = "Import support is `full`, `partial`, or `none`."
    cleaned, ungrounded = check_grounding(content, ctx)
    assert ungrounded == []
    assert cleaned == content


def test_collect_known_gathers_paths_and_symbols() -> None:
    paths, symbols = collect_known(_ctx_for_grounding())
    assert "core/graph/builder.py" in paths
    assert "builder.py" in paths  # basename included
    assert "GraphBuilder" in symbols
    assert "LanguageSpec" in symbols
