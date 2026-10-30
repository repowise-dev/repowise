"""include=["references"] also lists barrels, same-name definitions and documents."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.mcp_server import _edit_sites, _edit_sites_wider, _meta
from repowise.server.mcp_server._edit_sites import reference_edit_set

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean_tree(monkeypatch):
    for module in (_edit_sites, _meta):
        monkeypatch.setattr(module, "_working_tree_dirty_paths", lambda _p: frozenset())


def _node(repo_id: str, node_id: str, *, line: int = 1, language: str = "typescript") -> GraphNode:
    path, name = node_id.split("::")
    return GraphNode(
        id=f"wide_{node_id}",
        repository_id=repo_id,
        node_id=node_id,
        node_type="symbol",
        name=name,
        file_path=path,
        kind="function",
        language=language,
        start_line=line,
        end_line=line,
        created_at=_NOW,
    )


def _imports(i: int, repo_id: str, src: str, tgt: str, names: str) -> GraphEdge:
    return GraphEdge(
        id=f"wide_edge_{i}",
        repository_id=repo_id,
        source_node_id=src,
        target_node_id=tgt,
        edge_type="imports",
        imported_names_json=names,
        confidence=0.95,
        created_at=_NOW,
    )


def _write(root, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def _sites(out):
    return [(s["path"], s["line"], s["kind"]) for s in out["sites"]]


@pytest.mark.asyncio
async def test_a_star_barrel_is_listed_as_a_reexport(session, repo_id, tmp_path):
    _write(
        tmp_path,
        {
            "web/table.tsx": "export function Table() { return null }\n",
            "web/index.ts": 'export * from "./table";\nexport * from "./other";\n',
        },
    )
    node = _node(repo_id, "web/table.tsx::Table")
    session.add_all([node, _imports(1, repo_id, "web/index.ts", "web/table.tsx", '["*"]')])
    await session.flush()

    out = await reference_edit_set(session, repo_id, tmp_path, node, wider=True)

    assert ("web/index.ts", 1, "reexport") in _sites(out)
    assert ("web/index.ts", 2, "reexport") not in _sites(out)
    assert out["complete"] is True, out.get("reasons")


@pytest.mark.asyncio
async def test_a_python_star_import_is_listed_as_a_reexport(session, repo_id, tmp_path):
    _write(
        tmp_path,
        {
            "pkg/_models.py": "def build():\n    return 1\n",
            "pkg/__init__.py": "from ._models import *\nfrom ._other import *\n",
        },
    )
    node = _node(repo_id, "pkg/_models.py::build", language="python")
    session.add_all([node, _imports(2, repo_id, "pkg/__init__.py", "pkg/_models.py", '["*"]')])
    await session.flush()

    out = await reference_edit_set(session, repo_id, tmp_path, node, wider=True)

    assert [s for s in _sites(out) if s[2] == "reexport"] == [("pkg/__init__.py", 1, "reexport")]


@pytest.mark.asyncio
async def test_a_mirrored_constant_and_the_docs_naming_it_are_listed(session, repo_id, tmp_path):
    _write(
        tmp_path,
        {
            "srv/blocks.py": "INCLUDE_KEYS = ('a', 'b')\n",
            "cli/blocks.py": "INCLUDE_KEYS = ('a', 'b')\n",
            "docs/tools.md": "# Tools\n\n`INCLUDE_KEYS` lists them.\nSee INCLUDE_KEYS again.\n",
            "CHANGELOG.md": "* Added INCLUDE_KEYS\n",
        },
    )
    node = _node(repo_id, "srv/blocks.py::INCLUDE_KEYS", language="python")
    session.add_all([node, _node(repo_id, "cli/blocks.py::INCLUDE_KEYS", language="python")])
    await session.flush()

    out = await reference_edit_set(session, repo_id, tmp_path, node, wider=True)

    assert _sites(out) == [
        ("srv/blocks.py", 1, "definition"),
        ("cli/blocks.py", 1, "same_name"),
        ("docs/tools.md", 3, "doc"),
    ]
    assert out["sites"][-1]["mentions"] == 2
    assert out["complete"] is True, out.get("reasons")


@pytest.mark.asyncio
async def test_without_wider_only_code_sites_are_listed(session, repo_id, tmp_path):
    _write(
        tmp_path,
        {
            "srv/blocks.py": "INCLUDE_KEYS = ('a', 'b')\n",
            "cli/blocks.py": "INCLUDE_KEYS = ('a', 'b')\n",
            "docs/tools.md": "`INCLUDE_KEYS` lists them.\n",
        },
    )
    node = _node(repo_id, "srv/blocks.py::INCLUDE_KEYS", language="python")
    session.add_all([node, _node(repo_id, "cli/blocks.py::INCLUDE_KEYS", language="python")])
    await session.flush()

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert _sites(out) == [("srv/blocks.py", 1, "definition")]


@pytest.mark.asyncio
async def test_a_common_name_lists_no_same_name_definitions(session, repo_id, tmp_path):
    files = {f"m{i}/run.py": "def run_all():\n    pass\n" for i in range(_edit_sites_wider.MAX_SAME_NAME + 2)}
    _write(tmp_path, files)
    nodes = [_node(repo_id, f"{rel}::run_all", language="python") for rel in files]
    session.add_all(nodes)
    await session.flush()

    out = await reference_edit_set(session, repo_id, tmp_path, nodes[0], wider=True)

    assert not [s for s in _sites(out) if s[2] == "same_name"]


@pytest.mark.asyncio
async def test_over_the_document_cap_is_never_complete(session, repo_id, tmp_path, monkeypatch):
    monkeypatch.setattr(_edit_sites_wider, "MAX_DOC_FILES", 1)
    _write(
        tmp_path,
        {
            "srv/blocks.py": "INCLUDE_KEYS = ('a', 'b')\n",
            "docs/a.md": "INCLUDE_KEYS\n",
            "docs/b.md": "INCLUDE_KEYS\n",
        },
    )
    node = _node(repo_id, "srv/blocks.py::INCLUDE_KEYS", language="python")
    session.add(node)
    await session.flush()

    out = await reference_edit_set(session, repo_id, tmp_path, node, wider=True)

    assert out["complete"] is False
    assert "over 1 documents name it" in out["reasons"]


@pytest.mark.asyncio
async def test_a_type_star_barrel_is_a_reexport(session, repo_id, tmp_path):
    _write(
        tmp_path,
        {
            "web/table.ts": "export type Row = { id: number }\n",
            "web/index.ts": "export type * from './table'\n",
        },
    )
    node = _node(repo_id, "web/table.ts::Row")
    session.add_all([node, _imports(3, repo_id, "web/index.ts", "web/table.ts", '["*"]')])
    await session.flush()

    out = await reference_edit_set(session, repo_id, tmp_path, node, wider=True)

    assert ("web/index.ts", 1, "reexport") in _sites(out)


@pytest.mark.asyncio
async def test_documents_holding_only_a_longer_name_do_not_use_the_cap(
    session, repo_id, tmp_path, monkeypatch
):
    monkeypatch.setattr(_edit_sites_wider, "MAX_DOC_FILES", 1)
    _write(
        tmp_path,
        {
            "srv/blocks.py": "INCLUDE_KEYS = ('a', 'b')\n",
            "docs/a.md": "INCLUDE_KEYS_EXTRA only\n",
            "docs/b.md": "INCLUDE_KEYS\n",
        },
    )
    node = _node(repo_id, "srv/blocks.py::INCLUDE_KEYS", language="python")
    session.add(node)
    await session.flush()

    out = await reference_edit_set(session, repo_id, tmp_path, node, wider=True)

    assert [s for s in _sites(out) if s[2] == "doc"] == [("docs/b.md", 1, "doc")]
    assert out["complete"] is True, out.get("reasons")


def test_a_basename_under_another_folder_is_not_a_mention(tmp_path):
    from repowise.server.mcp_server.tool_context.doc_mentions import doc_mentions

    _write(
        tmp_path,
        {
            "docs/a.md": (
                "See other/table-view.tsx here.\n"
                "See `ui/table-view` there.\n"
                "And ui/table-view.tsx too.\n"
                "Not ui/table-view.test.tsx.\n"
            ),
        },
    )

    rows = doc_mentions(tmp_path, "src/ui/table-view.tsx", set())

    assert [r["line"] for r in rows] == [2, 3]
