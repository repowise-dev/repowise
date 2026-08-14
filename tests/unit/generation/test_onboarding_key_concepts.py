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
from repowise.core.generation.concept_tree.vocabulary import HouseTerm
from repowise.core.generation.models import compute_source_hash
from repowise.core.generation.onboarding.grounding import check_grounding, collect_known
from repowise.core.generation.onboarding.signals import OnboardingSignals
from repowise.core.generation.onboarding.slots import (
    ONBOARDING_GENERATION_VERSION,
    SLOT_KEY_CONCEPTS,
)
from repowise.core.generation.onboarding.subkinds.key_concepts import (
    _CONCEPT_EDGE_TYPES,
    _RELATION_VERB,
    ConceptRelation,
    ConceptSymbol,
    KeyConceptsContext,
    _prose_hits,
)
from repowise.core.ingestion.models import (
    SYMBOL_USE_EDGE_TYPES,
    FileInfo,
    ParsedFile,
    RepoStructure,
    Symbol,
)

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
        if d.get("edge_type") in SYMBOL_USE_EDGE_TYPES
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


def _term(term: str, *, docs: int = 1) -> HouseTerm:
    """One mined term, with the fields ranking reads set truthfully."""
    return HouseTerm(
        term=term,
        definition=None,
        definition_source=None,
        source_paths=tuple(f"docs/{i}.md" for i in range(docs)),
        doc_frequency=docs,
        code_frequency=1,
        is_indexed_symbol=True,
    )


def _signals(
    files, graph_builder, *, kg_layers=(), layer_order=(), community=None, house_terms=()
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
        house_terms=tuple(house_terms),
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


def test_relation_verb_covers_the_edge_types() -> None:
    """Every type the relations list can carry needs a verb.

    The fall-through is silent: an unmapped type renders as "depends on" into
    an LLM prompt that says to use the verb given. The template this replaced
    fell through to "imports from", which would have called a Go
    ``method_implements`` edge an import.
    """
    assert not (_CONCEPT_EDGE_TYPES - _RELATION_VERB.keys())
    assert not (_RELATION_VERB.keys() - _CONCEPT_EDGE_TYPES)


def test_go_interface_satisfaction_is_a_relation() -> None:
    """``method_implements`` was absent from the private set this file kept, so
    a Go type satisfying an interface produced no relation at all."""
    base = _file("m/base.go", [_sym("m/base.go", "Storer", "interface", doc="Interface.")])
    impl = _file("m/disk.go", [_sym("m/disk.go", "DiskStore", "struct", doc="Concrete.")])
    other = _file("m/client.go", [_sym("m/client.go", "ApiClient", "struct", doc="Client.")])
    conf = _file("m/config.go", [_sym("m/config.go", "Settings", "struct", doc="Config.")])
    files = [base, impl, other, conf]
    edges = [("m/disk.go::DiskStore", "m/base.go::Storer", "method_implements")]
    for i in range(5):
        c = f"call/u{i}.go"
        files.append(_file(c, [_sym(c, f"u{i}", "function")]))
        for tgt in (
            "m/base.go::Storer",
            "m/disk.go::DiskStore",
            "m/client.go::ApiClient",
            "m/config.go::Settings",
        ):
            edges.append((f"{c}::u{i}", tgt, "calls"))
    ctx = onboarding.get_spec(SLOT_KEY_CONCEPTS).build_context(
        _signals(files, _graph_builder(files, edges))
    )
    assert ctx is not None
    rels = {(r.source, r.kind, r.target) for r in ctx.relations}
    assert ("DiskStore", "method_implements", "Storer") in rels
    assert ConceptRelation("DiskStore", "Storer", "method_implements").verb == "implements"


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


# ---------------------------------------------------------------------------
# Ranking on what the repository says, not only on what its graph does
# ---------------------------------------------------------------------------


def _repo_for_ranking(*, scaffold_doc: str = "The vector store.") -> list[ParsedFile]:
    """Five concepts, one per directory so no spread cap can reorder them.

    ``VectorStore`` is the graph's favourite: every caller reaches it. Nothing
    written down mentions it. ``BlastRadius`` has one caller and is what the
    documents are about.
    """
    files = [
        _file(
            "store/db.py",
            [_sym("store/db.py", "VectorStore", "class", exported=True, doc=scaffold_doc)],
        ),
        _file(
            "analysis/blast.py",
            [
                _sym(
                    "analysis/blast.py",
                    "BlastRadius",
                    "class",
                    exported=True,
                    doc="What a change can reach.",
                )
            ],
        ),
        _file(
            "analysis/risk.py",
            [_sym("analysis/risk.py", "ChangeRisk", "class", exported=True, doc="Scores a diff.")],
        ),
        _file("io/reader.py", [_sym("io/reader.py", "Reader", "class", exported=True)]),
        _file("io/writer.py", [_sym("io/writer.py", "Writer", "class", exported=True)]),
    ]
    edges: list[tuple[str, str, str]] = []
    for i in range(9):
        caller = f"caller/c{i}.py"
        files.append(_file(caller, [_sym(caller, f"use{i}", "function")]))
        edges.append((f"{caller}::use{i}", "store/db.py::VectorStore", "calls"))
        if i < 5:
            edges.append((f"{caller}::use{i}", "io/reader.py::Reader", "calls"))
        if i < 4:
            edges.append((f"{caller}::use{i}", "io/writer.py::Writer", "calls"))
        if i < 2:
            edges.append((f"{caller}::use{i}", "analysis/risk.py::ChangeRisk", "calls"))
        if i < 1:
            edges.append((f"{caller}::use{i}", "analysis/blast.py::BlastRadius", "calls"))
    return files, edges


def _rank(*, house_terms=(), scaffold_doc: str = "The vector store.") -> list[str]:
    files, edges = _repo_for_ranking(scaffold_doc=scaffold_doc)
    signals = _signals(files, _graph_builder(files, edges), house_terms=house_terms)
    ctx = onboarding.get_spec(SLOT_KEY_CONCEPTS).build_context(signals)
    assert ctx is not None
    return [c.name for c in ctx.concept_symbols]


def test_a_symbol_the_documents_name_outranks_one_the_graph_merely_calls() -> None:
    """A class the team writes about beats a class with nine callers.

    The four original keys all measure how much code leans on a symbol. None
    of them can see whether a human thought it worth explaining.
    """
    graph_only = _rank()
    assert graph_only.index("VectorStore") < graph_only.index("BlastRadius")

    with_prose = _rank(house_terms=[_term("Blast radius", docs=3), _term("Change risk", docs=2)])
    assert with_prose.index("BlastRadius") < with_prose.index("VectorStore")
    # Two documents beat one, so the ranking reads the count and not just the
    # fact of a match.
    assert with_prose.index("BlastRadius") < with_prose.index("ChangeRisk")


def test_a_mined_term_matches_however_the_code_spells_it() -> None:
    """The documents write "blast radius"; the code writes ``BlastRadius``."""
    assert _rank(house_terms=[_term("blast radius")]).index("BlastRadius") == 0
    assert _rank(house_terms=[_term("Blast Radius")]).index("BlastRadius") == 0


def test_a_term_matches_the_type_named_after_it() -> None:
    """A term is rarely a class name; it is the idea the class is named after.

    Matching "blast radius" exactly finds nothing in a codebase whose class
    is ``BlastRadiusReport``, which is how codebases are usually named.
    """
    assert _prose_hits("BlastRadiusReport", {("blast", "radius"): 2}) == 2
    assert _prose_hits("CrossRepoBlastRadius", {("blast", "radius"): 2}) == 2
    assert _prose_hits("blast_radius_of", {("blast", "radius"): 2}) == 2
    assert _prose_hits("RadiusBlast", {("blast", "radius"): 2}) == 0


def test_a_single_word_term_has_to_lead_the_name() -> None:
    """Otherwise the short terms claim every name that contains the word.

    "risk" would take ``OwnershipRiskDetector`` and "stats" would take
    ``CFGPassStats`` — names that contain the word while being about
    something else, and there are far more of those than real matches.
    """
    assert _prose_hits("Risk", {("risk",): 3}) == 3
    assert _prose_hits("RiskDirective", {("risk",): 3}) == 3
    assert _prose_hits("OwnershipRiskDetector", {("risk",): 3}) == 0


def test_ranking_is_unchanged_when_no_vocabulary_was_mined() -> None:
    """Most repositories will mine nothing. They must rank as they always did."""
    assert _rank(house_terms=()) == _rank(house_terms=[_term("Nothing In This Repository")])


def test_a_symbol_that_says_it_is_for_tests_is_demoted() -> None:
    """Demoted, not excluded — the page can still fall back to it."""
    ordinary = _rank()
    assert ordinary[0] == "VectorStore"

    demoted = _rank(scaffold_doc="An in-memory store, primarily tailored for unit tests.")
    assert demoted[-1] == "VectorStore"
    assert "VectorStore" in demoted


def test_scaffolding_outranks_nothing_even_when_the_documents_name_it() -> None:
    """The demotion leads, so a written-about test double still sinks."""
    demoted = _rank(
        house_terms=[_term("Vector store", docs=5)],
        scaffold_doc="A store used in tests and small-scale development.",
    )
    assert demoted[-1] == "VectorStore"


def test_every_concept_still_carries_its_path_for_the_page_to_cite() -> None:
    """The path citations are what ``get_answer`` quotes off this page.

    Asserted as a count against the concepts, not as a presence check: a
    prompt that lost the path on one concept of six still contains the word.
    """
    files, edges = _repo_for_ranking()
    signals = _signals(
        files, _graph_builder(files, edges), house_terms=[_term("Blast radius", docs=3)]
    )
    ctx = onboarding.get_spec(SLOT_KEY_CONCEPTS).build_context(signals)
    assert ctx is not None

    rendered = _render_key_concepts(ctx)
    assert len(ctx.concept_symbols) >= 4
    for concept in ctx.concept_symbols:
        assert f"`{concept.file_path}`" in rendered
    assert rendered.count("`") >= 2 * len(ctx.concept_symbols)
    assert "**Where it lives:**" in rendered


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


def test_qualified_symbol_cannot_borrow_an_unrelated_member() -> None:
    ctx = {"known": "Real.run"}

    cleaned, ungrounded = check_grounding("`Ghost.run` is fabricated.", ctx)

    assert ungrounded == ["Ghost.run"]
    assert "`Ghost.run`" not in cleaned


def test_qualified_symbol_cannot_borrow_a_known_owner() -> None:
    cleaned, ungrounded = check_grounding("`Real.fabricated` is absent.", {"known": "Real.run"})

    assert ungrounded == ["Real.fabricated"]
    assert "`Real.fabricated`" not in cleaned


def test_grounding_accepts_citations_established_only_by_added_evidence() -> None:
    ctx = _ctx_for_grounding()
    content = (
        "The `EvidenceRouter.dispatch` in `docs/runtime_flow.py` selects the worker, "
        "while `FabricatedWorker` is not established."
    )
    evidence = {
        "docs/runtime_flow.py": (
            "EvidenceRouter.dispatch validates the request before selecting a worker."
        )
    }

    cleaned, ungrounded = check_grounding(content, ctx, evidence)

    assert "`EvidenceRouter.dispatch`" in cleaned
    assert "`docs/runtime_flow.py`" in cleaned
    assert "FabricatedWorker" in ungrounded
    assert "`FabricatedWorker`" not in cleaned


def test_evidence_grounding_requires_complete_identifier_and_path() -> None:
    ctx = _ctx_for_grounding()
    evidence = {"src/foo.py": "Existing.run"}
    content = "`Existing.run` is real; `FabricatedType.run` and `other/place/foo.py` are not."

    cleaned, ungrounded = check_grounding(content, ctx, evidence)

    assert "`Existing.run`" in cleaned
    assert ungrounded == ["FabricatedType.run", "other/place/foo.py"]
    assert "`FabricatedType.run`" not in cleaned
    assert "`other/place/foo.py`" not in cleaned


def test_evidence_grounding_requires_qualified_path_member_to_occur() -> None:
    ctx = _ctx_for_grounding()
    evidence = {"src/foo.py": "ExistingWorker handles requests."}
    content = (
        "`src/foo.py` is included, but `src/foo.py::FabricatedWorker` and "
        "`src/foo.py#FabricatedWorker` are not established."
    )

    cleaned, ungrounded = check_grounding(content, ctx, evidence)

    assert "`src/foo.py`" in cleaned
    assert ungrounded == ["src/foo.py::FabricatedWorker", "src/foo.py#FabricatedWorker"]
    assert "`src/foo.py::FabricatedWorker`" not in cleaned
    assert "`src/foo.py#FabricatedWorker`" not in cleaned


def test_evidence_grounding_validates_configured_documentation_paths() -> None:
    ctx = _ctx_for_grounding()
    evidence = {
        "README.md": "Project purpose.",
        "docs/ARCHITECTURE.md": "System boundaries.",
    }
    content = (
        "Read `README.md` and `docs/ARCHITECTURE.md`, not "
        "`docs/FABRICATED.md` or `other/guide.rst`."
    )

    cleaned, ungrounded = check_grounding(content, ctx, evidence)

    assert "`README.md`" in cleaned
    assert "`docs/ARCHITECTURE.md`" in cleaned
    assert ungrounded == ["docs/FABRICATED.md", "other/guide.rst"]
    assert "`docs/FABRICATED.md`" not in cleaned
    assert "`other/guide.rst`" not in cleaned


def test_evidence_grounding_validates_arbitrary_sibling_repository_paths() -> None:
    evidence = {
        "schemas/order.proto": "message Order {}",
        "services/real.py": "def real(): pass",
    }
    content = "Do not cite `schemas/fabricated.proto` or `services/fabricated.py`."

    cleaned, ungrounded = check_grounding(content, _ctx_for_grounding(), evidence)

    assert ungrounded == ["schemas/fabricated.proto", "services/fabricated.py"]
    assert "`schemas/fabricated.proto`" not in cleaned
    assert "`services/fabricated.py`" not in cleaned


def test_evidence_grounding_validates_extensionless_repository_paths() -> None:
    ctx = _ctx_for_grounding()
    evidence = {"deploy/Dockerfile": "Build instructions."}

    cleaned, ungrounded = check_grounding(
        "Use `deploy/Dockerfile`, not `docs/LICENSE` or `deploy/Fakefile`.",
        ctx,
        evidence,
    )

    assert "`deploy/Dockerfile`" in cleaned
    assert ungrounded == ["docs/LICENSE", "deploy/Fakefile"]
    assert "`docs/LICENSE`" not in cleaned
    assert "`deploy/Fakefile`" not in cleaned


def test_grounding_does_not_treat_urls_routes_or_commands_as_repository_paths() -> None:
    content = (
        "Call `https://example.com/docs`, `github.com/org/repo`, `api/v1/users`, "
        "`api/V1/users`, `localhost:3000/api`, `v2/users`, `V2/users`, "
        "`service/v1/users`, `service/v2/schema.yaml`, `api/v1/openapi.json`, "
        "`localhost/openapi.json`, `users/V2/profile`, `users/profile`, "
        "`v1/openapi.json`, `accounts/v1/users.json`, `GET/api`, "
        "`npm:test`, `NPM:test`, `example.com`, `EXAMPLE.COM`, "
        "`example.xyz/path`, `EXAMPLE.XYZ/path`, `Github.COM/org/repo`, "
        "`API/v1/users`, `Service/v1/users`, `GET /health`, or `/health`."
    )

    cleaned, ungrounded = check_grounding(content, _ctx_for_grounding())

    assert cleaned == content
    assert ungrounded == []


def test_grounding_validates_paths_in_versioned_repository_directories() -> None:
    content = (
        "Do not cite `docs/v2/fake.md`, `src/v1/missing.py`, `v1/src/handler`, or `V1/Src/handler`."
    )

    cleaned, ungrounded = check_grounding(content, _ctx_for_grounding())

    assert ungrounded == [
        "docs/v2/fake.md",
        "src/v1/missing.py",
        "v1/src/handler",
        "V1/Src/handler",
    ]
    assert "`docs/v2/fake.md`" not in cleaned
    assert "`src/v1/missing.py`" not in cleaned
    assert "`v1/src/handler`" not in cleaned
    assert "`V1/Src/handler`" not in cleaned


def test_grounding_validates_structured_paths_under_api_directory() -> None:
    cleaned, ungrounded = check_grounding("Do not cite `api/openapi.yaml`.", _ctx_for_grounding())

    assert ungrounded == ["api/openapi.yaml"]
    assert "`api/openapi.yaml`" not in cleaned


def test_qualified_evidence_paths_cannot_borrow_a_structured_bare_path() -> None:
    ctx = {"known_files": ["src/foo.py", "README.md"]}
    evidence = {
        "src/foo.py": "RealWorker handles requests.",
        "README.md": "Documented setup.",
    }
    content = "`src/foo.py::FabricatedWorker` and `README.md#fabricated` are not established."

    cleaned, ungrounded = check_grounding(content, ctx, evidence)

    assert ungrounded == ["src/foo.py::FabricatedWorker", "README.md#fabricated"]
    assert "`src/foo.py::FabricatedWorker`" not in cleaned
    assert "`README.md#fabricated`" not in cleaned


def test_grounding_validates_root_documentation_paths_with_punctuation() -> None:
    content = "Do not cite `MIGRATION-GUIDE.md` or `CODE-OF-CONDUCT.md#policy`."

    cleaned, ungrounded = check_grounding(content, _ctx_for_grounding())

    assert ungrounded == ["MIGRATION-GUIDE.md", "CODE-OF-CONDUCT.md#policy"]
    assert "`MIGRATION-GUIDE.md`" not in cleaned
    assert "`CODE-OF-CONDUCT.md#policy`" not in cleaned


def test_grounding_validates_qualified_root_build_and_config_paths() -> None:
    content = "Do not cite `pom.xml#fake`, `Cargo.lock#fake`, or `justfile#fake`."

    cleaned, ungrounded = check_grounding(content, _ctx_for_grounding())

    assert ungrounded == ["pom.xml#fake", "Cargo.lock#fake", "justfile#fake"]
    for token in ungrounded:
        assert f"`{token}`" not in cleaned


def test_evidence_grounding_preserves_exact_dot_prefixed_paths() -> None:
    ctx = _ctx_for_grounding()
    evidence = {
        ".github/CONTRIBUTING.md": "Contribution workflow.",
        ".env.example": "EXAMPLE=true",
    }

    cleaned, ungrounded = check_grounding(
        (
            "Read `.github/CONTRIBUTING.md` and `.env.example`, not "
            "`.github/FAKE.md` or `.env.production`."
        ),
        ctx,
        evidence,
    )

    assert "`.github/CONTRIBUTING.md`" in cleaned
    assert "`.env.example`" in cleaned
    assert ungrounded == [".github/FAKE.md", ".env.production"]
    assert "`.github/FAKE.md`" not in cleaned
    assert "`.env.production`" not in cleaned


def test_grounding_keeps_documentation_paths_from_structured_context() -> None:
    ctx = {"hot_files": ["docs/guide.md"]}

    cleaned, ungrounded = check_grounding("Read `docs/guide.md` first.", ctx)

    assert cleaned == "Read `docs/guide.md` first."
    assert ungrounded == []


def test_evidence_path_match_uses_path_boundaries() -> None:
    ctx = _ctx_for_grounding()
    evidence = {"docs/notes.md": "old/src/foo.py.bak differs; use src/real.py instead."}
    content = "`src/foo.py` is fabricated; `src/real.py` is established."

    cleaned, ungrounded = check_grounding(content, ctx, evidence)

    assert ungrounded == ["src/foo.py"]
    assert "`src/foo.py`" not in cleaned
    assert "`src/real.py`" in cleaned


def test_evidence_path_match_uses_complete_fragment_boundaries() -> None:
    ctx = _ctx_for_grounding()
    evidence = {
        "docs/notes.md": (
            "README.md#setup#fabricated and src/foo.py#Worker#fabricated are unrelated."
        )
    }
    content = "`README.md#setup` and `src/foo.py#Worker` are not established."

    cleaned, ungrounded = check_grounding(content, ctx, evidence)

    assert ungrounded == ["README.md#setup", "src/foo.py#Worker"]
    assert "`README.md#setup`" not in cleaned
    assert "`src/foo.py#Worker`" not in cleaned


def test_evidence_symbol_match_uses_qualified_identifier_boundaries() -> None:
    ctx = _ctx_for_grounding()
    evidence = {
        "docs/notes.md": (
            "Other.EvidenceRouter.dispatch, EvidenceRouter.dispatch.extra, "
            "Other/EvidenceRouter.dispatch, and Other#EvidenceRouter.dispatch are unrelated."
        )
    }

    cleaned, ungrounded = check_grounding("Use `EvidenceRouter.dispatch`.", ctx, evidence)

    assert ungrounded == ["EvidenceRouter.dispatch"]
    assert "`EvidenceRouter.dispatch`" not in cleaned


def test_grounding_validates_non_dot_qualified_symbols() -> None:
    evidence = {"docs/notes.md": "Router#real Router:real Router/real"}
    content = "`Router#fabricated`, `Router:fabricated`, and `Router/fabricated` are absent."

    cleaned, ungrounded = check_grounding(content, _ctx_for_grounding(), evidence)

    assert ungrounded == ["Router#fabricated", "Router:fabricated", "Router/fabricated"]
    for token in ungrounded:
        assert f"`{token}`" not in cleaned


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
