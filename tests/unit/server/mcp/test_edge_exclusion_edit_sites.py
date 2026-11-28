"""Reference edit sets hide graph rows for files the repo excludes.

The index can keep rows for a file excluded after it was built, so the edit
set scanner drops them at read time: an excluded file is never listed, never
named in a reason, never a hop of the import or heritage walk, and never
decides completeness.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pathspec
import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.mcp_server import _edit_sites, _edit_sites_twins, _meta
from repowise.server.mcp_server._edit_sites import edit_set_block, reference_edit_set
from repowise.server.mcp_server._helpers import _get_exclude_spec

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)
_DEF = "pkg/catalog.py"
_SYM = "pkg/catalog.py::backend_of"
_SPEC = pathspec.PathSpec.from_lines("gitwildmatch", ["archive/**"])


@pytest.fixture(autouse=True)
def _clean_tree(monkeypatch):
    for module in (_edit_sites, _meta):
        monkeypatch.setattr(module, "_working_tree_dirty_paths", lambda _p: frozenset())


def _edge(i: int, repo_id: str, src: str, tgt: str, etype: str, **kw) -> GraphEdge:
    return GraphEdge(
        id=f"excl_edge_{i}",
        repository_id=repo_id,
        source_node_id=src,
        target_node_id=tgt,
        edge_type=etype,
        confidence=kw.pop("confidence", 0.95),
        created_at=_NOW,
        **kw,
    )


def _write(tmp_path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")


async def _seed(session, repo_id, tmp_path, files: dict[str, str], edges: list[tuple]):
    _write(tmp_path, {_DEF: "def backend_of(name):\n    return name\n", **files})
    node = GraphNode(
        id="excl_sym",
        repository_id=repo_id,
        node_id=_SYM,
        node_type="symbol",
        name="backend_of",
        file_path=_DEF,
        kind="function",
        language="python",
        start_line=1,
        end_line=2,
        created_at=_NOW,
    )
    session.add_all([node, *(_edge(i, repo_id, *e[:3], **e[3]) for i, e in enumerate(edges))])
    await session.flush()
    return node


def _paths(out) -> set[str]:
    return {s["path"] for s in out["sites"]}


def _no_archive(out) -> None:
    assert not any(p.startswith("archive/") for p in _paths(out))
    assert not any("archive/" in r for r in out.get("reasons", []))


_USE = {"app/use.py": "from pkg.catalog import backend_of\n\nbackend_of(1)\n"}
_USE_EDGES = [
    ("app/use.py", _DEF, "imports", {"imported_names_json": '["backend_of"]'}),
    ("app/use.py::__module__", _SYM, "calls", {"call_lines_json": json.dumps([3])}),
]


@pytest.mark.asyncio
async def test_an_excluded_caller_and_importer_are_not_listed(session, repo_id, tmp_path):
    node = await _seed(
        session,
        repo_id,
        tmp_path,
        {**_USE, "archive/old.py": "from pkg.catalog import backend_of\n\nbackend_of(2)\n"},
        [
            *_USE_EDGES,
            ("archive/old.py", _DEF, "imports", {"imported_names_json": '["backend_of"]'}),
            ("archive/old.py::__module__", _SYM, "calls", {"call_lines_json": "[3]"}),
        ],
    )

    before = await reference_edit_set(session, repo_id, tmp_path, node)
    out = await reference_edit_set(session, repo_id, tmp_path, node, exclude_spec=_SPEC)

    assert "archive/old.py" in _paths(before)
    _no_archive(out)
    allowed = [s for s in before["sites"] if not s["path"].startswith("archive/")]
    assert out["sites"] == allowed
    assert out["complete"] is True


@pytest.mark.asyncio
async def test_the_import_walk_does_not_pass_through_an_excluded_barrel(session, repo_id, tmp_path):
    node = await _seed(
        session,
        repo_id,
        tmp_path,
        {
            "archive/barrel.py": "from pkg.catalog import *\n",
            "app/star.py": "from archive.barrel import *\n\nthing(backend_of)\n",
        },
        [
            ("archive/barrel.py", _DEF, "imports", {"imported_names_json": "[]"}),
            ("app/star.py", "archive/barrel.py", "imports", {"imported_names_json": '["*"]'}),
        ],
    )

    before = await reference_edit_set(session, repo_id, tmp_path, node)
    out = await reference_edit_set(session, repo_id, tmp_path, node, exclude_spec=_SPEC)

    assert "app/star.py" in _paths(before)
    assert "app/star.py" not in _paths(out)
    _no_archive(out)


@pytest.mark.asyncio
async def test_an_excluded_base_method_is_not_a_reason(session, repo_id, tmp_path):
    node = await _seed(session, repo_id, tmp_path, _USE, _USE_EDGES)
    base = "archive/base.py::Base::backend_of"
    session.add(_edge(90, repo_id, base, _SYM, "dispatches_to", confidence=0.75))
    await session.flush()

    before = await reference_edit_set(session, repo_id, tmp_path, node)
    out = await reference_edit_set(session, repo_id, tmp_path, node, exclude_spec=_SPEC)

    assert f"calls through the base method are not listed: {base}" in before["reasons"]
    _no_archive(out)
    assert out["complete"] is True


@pytest.mark.asyncio
async def test_an_excluded_importer_does_not_export_an_alias(session, repo_id, tmp_path):
    node = await _seed(
        session,
        repo_id,
        tmp_path,
        {
            "app/local.py": "from pkg.catalog import backend_of as pick\n\nvalue = pick(1)\n",
            "archive/far.py": "from app.local import pick\n",
        },
        [
            ("app/local.py", _DEF, "imports", {"imported_names_json": '["backend_of"]'}),
            ("archive/far.py", "app/local.py", "imports", {"imported_names_json": '["pick"]'}),
        ],
    )

    before = await reference_edit_set(session, repo_id, tmp_path, node)
    out = await reference_edit_set(session, repo_id, tmp_path, node, exclude_spec=_SPEC)

    assert "renamed on import or export: app/local.py" in before["reasons"]
    assert out["complete"] is True, out.get("reasons")
    assert "app/local.py" in _paths(out)


@pytest.mark.asyncio
async def test_the_block_reads_the_repo_config_rules(session, repo_id, tmp_path):
    _write(tmp_path, {".repowise/config.yaml": "exclude_patterns:\n- archive/**\n"})
    node = await _seed(
        session,
        repo_id,
        tmp_path,
        {**_USE, "archive/old.py": "from pkg.catalog import backend_of\n\nbackend_of(2)\n"},
        [
            *_USE_EDGES,
            ("archive/old.py::__module__", _SYM, "calls", {"call_lines_json": "[3]"}),
        ],
    )
    session.add(_edge(91, repo_id, "archive/b.py::B::backend_of", _SYM, "dispatches_to"))
    await session.flush()

    spec = _get_exclude_spec(tmp_path)
    out = await edit_set_block(session, repo_id, tmp_path, node, exclude_spec=spec)

    assert set(out["files"]) == {_DEF, "app/use.py"}
    assert not any("archive/" in r for r in out.get("reasons", []))


@pytest.mark.asyncio
async def test_the_heritage_walk_skips_excluded_classes(session, repo_id):
    session.add_all(
        [
            _edge(1, repo_id, "pkg/a.py::A", "archive/b.py::B", "extends"),
            _edge(2, repo_id, "archive/b.py::B", "pkg/c.py::C", "extends"),
            _edge(3, repo_id, "pkg/a.py::A", "pkg/d.py::D", "extends"),
            _edge(4, repo_id, "pkg/a.py::E", "archive/b.py::B", "extends"),
        ]
    )
    await session.flush()

    before = await _edit_sites_twins._walk(session, repo_id, "pkg/a.py::A", up=True)
    out = await _edit_sites_twins._walk(session, repo_id, "pkg/a.py::A", up=True, spec=_SPEC)
    based = await _edit_sites_twins._based(session, repo_id, {"pkg/a.py::A", "pkg/a.py::E"}, _SPEC)

    assert before == {"archive/b.py::B", "pkg/c.py::C", "pkg/d.py::D"}
    assert out == {"pkg/d.py::D"}
    assert based == {"pkg/a.py::A"}
