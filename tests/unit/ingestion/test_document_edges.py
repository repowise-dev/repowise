"""``documents`` edges: a document links to or names a repository file.

Only a link or path doc drift resolves to exactly one parsed file becomes an
edge, and the edge never reaches the code views (PageRank, cycles,
communities, dead code).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.analysis.dead_code.file_reachability import has_dependency_importer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from tests.unit.ingestion.test_document_extractors import _ingest, _register_spawn_extractor

_CODE = {"src/app.py": "from src import util\n", "src/util.py": "X = 1\n"}


def _build(repo: Path, files: dict[str, str]) -> GraphBuilder:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    builder.build()
    return builder


def _doc_edges(builder: GraphBuilder) -> dict[tuple[str, str], dict]:
    return {
        (u, v): d
        for u, v, d in builder.graph().edges(data=True)
        if d.get("edge_type") == "documents"
    }


def test_link_resolves_relative_to_the_document(tmp_path: Path) -> None:
    b = _build(tmp_path, {**_CODE, "docs/guide.md": "Read [the app](../src/app.py).\n"})
    assert _doc_edges(b) == {
        ("docs/guide.md", "src/app.py"): {
            "edge_type": "documents",
            "hint_source": "doc_link",
            "confidence": 1.0,
        }
    }


def test_backticked_path_is_a_weaker_edge(tmp_path: Path) -> None:
    b = _build(tmp_path, {**_CODE, "README.md": "The helpers live in `src/util.py`.\n"})
    assert _doc_edges(b) == {
        ("README.md", "src/util.py"): {
            "edge_type": "documents",
            "hint_source": "doc_path",
            "confidence": 0.8,
        }
    }


def test_link_beats_path_for_the_same_target(tmp_path: Path) -> None:
    b = _build(tmp_path, {**_CODE, "README.md": "`src/app.py`, or [app](src/app.py).\n"})
    assert _doc_edges(b)["README.md", "src/app.py"]["hint_source"] == "doc_link"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("See [the source](src/).\n", id="directory"),
        pytest.param("See `lib/helpers.py`.\n", id="ambiguous-basename"),
        pytest.param("See [this page](README.md).\n", id="self-link"),
        pytest.param("See https://example.com/src/app.py.\n", id="url"),
        pytest.param("```\n[app](src/app.py)\n```\n", id="fenced-code"),
        pytest.param("<!-- repowise-drift-ignore --> See `src/app.py`.\n", id="drift-ignore"),
    ],
)
def test_unresolved_or_unchecked_references_emit_nothing(tmp_path: Path, text: str) -> None:
    files = {**_CODE, "a/helpers.py": "", "b/helpers.py": "", "lib/x.py": "", "README.md": text}
    assert _doc_edges(_build(tmp_path, files)) == {}


@pytest.mark.parametrize(
    "doc", ["CHANGELOG.md", "vendor/lib/README.md", "tests/fixtures/sample/README.md"]
)
def test_documents_that_are_not_own_prose_emit_nothing(tmp_path: Path, doc: str) -> None:
    b = _build(tmp_path, {**_CODE, doc: "See `src/app.py`.\n"})
    assert _doc_edges(b) == {}


def test_a_document_can_document_another_document(tmp_path: Path) -> None:
    b = _build(
        tmp_path, {"README.md": "See [the guide](docs/guide.md).\n", "docs/guide.md": "# G\n"}
    )
    assert set(_doc_edges(b)) == {("README.md", "docs/guide.md")}


def test_a_sibling_link_beats_the_root_file_of_the_same_name(tmp_path: Path) -> None:
    files = {
        "README.md": "# Root\n",
        "setup.md": "# Root setup\n",
        "docs/setup.md": "# Docs setup\n",
        "docs/README.md": "[x](setup.md) [self](README.md) [root](/setup.md)\n",
        "guide/README.md": "[broken](setup.md)\n",  # no guide/setup.md: no edge to the root one
    }
    assert set(_doc_edges(_build(tmp_path, files))) == {
        ("docs/README.md", "docs/setup.md"),
        ("docs/README.md", "setup.md"),
    }


def test_a_bare_backticked_name_never_reaches_a_root_file(tmp_path: Path) -> None:
    files = {
        **_CODE,
        "package.json": "{}\n",
        "README.md": "Bump `package.json`.\n",  # root doc, root file: still generic prose
        "docs/guide.md": "Bump `package.json`; see `notes.py`.\n",  # nested doc: no root edge
        "docs/notes.py": "",
        "docs/ref.md": "See `/package.json`.\n",
        "docs/setup.md": "Edit `./package.json`; run `./notes.py`.\n",  # `./` is still bare
    }
    assert set(_doc_edges(_build(tmp_path, files))) == {
        ("docs/guide.md", "docs/notes.py"),  # the doc's sibling still resolves
        ("docs/setup.md", "docs/notes.py"),
        ("docs/ref.md", "package.json"),  # an explicit root path is not a bare name
    }


@pytest.mark.usefixtures("extractor_registry")
async def test_a_converted_document_links_from_its_markdown(tmp_path: Path, monkeypatch) -> None:
    _register_spawn_extractor(monkeypatch, tmp_path / "calls.txt")  # echoes its input as Markdown
    files = {**_CODE, "specs/api.xyz": "Served by [the app](../src/app.py).\n"}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    *_, builder, _stats, _tech = await _ingest(tmp_path)
    assert builder._parsed_files["specs/api.xyz"].file_info.language == "markdown"
    assert set(_doc_edges(builder)) == {("specs/api.xyz", "src/app.py")}


def test_documents_edge_displaces_a_seeded_co_change(tmp_path: Path) -> None:
    b = _build(tmp_path, {**_CODE, "README.md": "See `src/app.py`.\n"})
    g = b.graph()
    g.remove_edge("README.md", "src/app.py")
    g.add_edge("src/app.py", "README.md", edge_type="co_changes", weight=3.0)
    b.build(scope=frozenset())  # a patch build keeps seeded co_changes
    assert not g.has_edge("src/app.py", "README.md")
    assert g.edges["README.md", "src/app.py"]["edge_type"] == "documents"


def test_add_co_change_edges_skips_a_documented_pair(tmp_path: Path) -> None:
    b = _build(tmp_path, {**_CODE, "README.md": "See `src/app.py`.\n"})
    partners = [{"file_path": "src/app.py", "co_change_count": 9, "frequency": 9}]
    assert (
        b.add_co_change_edges({"README.md": {"co_change_partners_json": json.dumps(partners)}}) == 0
    )


def test_code_metrics_ignore_documents_edges(tmp_path: Path) -> None:
    code = {"pkg/a.py": "from pkg import b\n", "pkg/b.py": "from pkg import a\n"}
    plain = _build(tmp_path / "plain", {**code, "README.md": "# Readme\n"})
    linked = _build(tmp_path / "linked", {**code, "README.md": "[a](pkg/a.py) [b](pkg/b.py)\n"})
    assert len(_doc_edges(linked)) == 2
    assert linked.pagerank() == plain.pagerank()
    assert linked.strongly_connected_components() == plain.strongly_connected_components()
    assert set(linked.cycle_subgraph().edges) == set(plain.cycle_subgraph().edges)
    assert linked.community_detection() == plain.community_detection()


def test_a_file_only_a_document_names_stays_unreachable(tmp_path: Path) -> None:
    b = _build(
        tmp_path, {**_CODE, "README.md": "See [orphan](src/orphan.py).\n", "src/orphan.py": ""}
    )
    assert ("README.md", "src/orphan.py") in _doc_edges(b)
    assert not has_dependency_importer(b.graph(), "src/orphan.py")
