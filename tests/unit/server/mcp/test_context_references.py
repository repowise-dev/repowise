"""include=["references"]: every live edit site of a symbol, and whether that is all of them."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.mcp_server import _edit_sites, _meta
from repowise.server.mcp_server._edit_sites import reference_edit_set

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)
_DEF = "pkg/catalog.py"
_SYM = "pkg/catalog.py::backend_of"
_USER = "app/widget.py"

_CATALOG = """def helper():
    return backend_of("x")


def backend_of(name):
    return name
"""
_WIDGET = """from pkg.catalog import (
    MODELS,
    backend_of,
)


class W:
    def __init__(self):
        self.b = backend_of("m")
        self.label = "backend_of"
"""


def _dirty(monkeypatch, paths: frozenset[str]) -> None:
    for module in (_edit_sites, _meta):
        monkeypatch.setattr(module, "_working_tree_dirty_paths", lambda _p: paths)


@pytest.fixture(autouse=True)
def _clean_tree(monkeypatch):
    _dirty(monkeypatch, frozenset())


def _edge(i: int, repo_id: str, src: str, tgt: str, etype: str, **kw) -> GraphEdge:
    return GraphEdge(
        id=f"ref_edge_{i}",
        repository_id=repo_id,
        source_node_id=src,
        target_node_id=tgt,
        edge_type=etype,
        confidence=kw.pop("confidence", 0.95),
        created_at=_NOW,
        **kw,
    )


async def _seed(
    session, repo_id: str, tmp_path, *, widget_call_lines=(9,), extra=(), language="python"
):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / _DEF).write_text(_CATALOG, encoding="utf-8")
    (tmp_path / _USER).write_text(_WIDGET, encoding="utf-8")
    node = GraphNode(
        id="ref_sym",
        repository_id=repo_id,
        node_id=_SYM,
        node_type="symbol",
        name="backend_of",
        file_path=_DEF,
        kind="function",
        language=language,
        start_line=5,
        end_line=6,
        created_at=_NOW,
    )
    rows = [
        node,
        _edge(1, repo_id, _USER, _DEF, "imports", imported_names_json='["MODELS", "backend_of"]'),
        _edge(
            2,
            repo_id,
            f"{_USER}::W::__init__",
            _SYM,
            "calls",
            call_lines_json=json.dumps(list(widget_call_lines)),
        ),
        _edge(3, repo_id, f"{_DEF}::helper", _SYM, "calls", call_lines_json="[2]"),
        *(_edge(10 + i, repo_id, *e[:3], **e[3]) for i, e in enumerate(extra)),
    ]
    session.add_all(rows)
    await session.flush()
    return node


def _sites(out):
    return [(s["path"], s["line"], s["kind"]) for s in out["sites"]]


@pytest.mark.asyncio
async def test_definition_import_call_and_same_file_call(session, repo_id, tmp_path):
    node = await _seed(session, repo_id, tmp_path)

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert _sites(out) == [
        (_DEF, 5, "definition"),
        (_USER, 3, "import"),  # continuation line of a multi-line import
        (_USER, 9, "call"),
        (_USER, 10, "reference"),  # a string naming it
        (_DEF, 2, "call"),  # same-file call
    ]
    assert out["complete"] is True
    assert "reasons" not in out


@pytest.mark.asyncio
async def test_stale_call_line_is_dropped_and_marks_incomplete(session, repo_id, tmp_path):
    node = await _seed(session, repo_id, tmp_path, widget_call_lines=(7, 9))

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert (_USER, 7) not in {(p, n) for p, n, _k in _sites(out)}
    assert out["complete"] is False
    assert any(r.startswith("stale") for r in out["reasons"])


@pytest.mark.asyncio
async def test_dynamic_use_of_the_file_marks_incomplete(session, repo_id, tmp_path):
    node = await _seed(
        session,
        repo_id,
        tmp_path,
        extra=[("app/loader.py", _DEF, "dynamic_uses", {"imported_names_json": "[]"})],
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert out["complete"] is False
    assert any("dynamic" in r for r in out["reasons"])


@pytest.mark.asyncio
async def test_dynamic_use_naming_another_symbol_does_not(session, repo_id, tmp_path):
    node = await _seed(
        session,
        repo_id,
        tmp_path,
        extra=[("app/loader.py", _DEF, "dynamic_uses", {"imported_names_json": '["helper"]'})],
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert out["complete"] is True


@pytest.mark.asyncio
async def test_unindexed_edit_naming_the_symbol_is_listed_and_incomplete(
    session, repo_id, tmp_path, monkeypatch
):
    node = await _seed(session, repo_id, tmp_path)
    (tmp_path / "app" / "new.py").write_text("x = backend_of(1)\n", encoding="utf-8")
    _dirty(monkeypatch, frozenset({"app/new.py"}))

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert ("app/new.py", 1, "reference") in _sites(out)
    assert out["complete"] is False


@pytest.mark.asyncio
async def test_same_package_language_is_never_complete(session, repo_id, tmp_path):
    node = await _seed(session, repo_id, tmp_path, language="java")

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert (_USER, 9, "call") in _sites(out)
    assert out["complete"] is False
    assert "java: same-package use is not scanned" in out["reasons"]


@pytest.mark.asyncio
async def test_renamed_barrel_re_export_marks_incomplete(session, repo_id, tmp_path):
    node = await _seed(
        session,
        repo_id,
        tmp_path,
        extra=[("pkg/__init__.py", _DEF, "imports", {"imported_names_json": '["backend_of"]'})],
    )
    (tmp_path / "pkg" / "__init__.py").write_text(
        "from .catalog import backend_of as pick_backend\n", encoding="utf-8"
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert ("pkg/__init__.py", 1, "import") in _sites(out)
    assert out["complete"] is False
    assert any(r.startswith("renamed on import or export") for r in out["reasons"])


@pytest.mark.asyncio
async def test_over_the_site_cap_is_incomplete(session, repo_id, tmp_path):
    node = await _seed(session, repo_id, tmp_path)
    with (tmp_path / _USER).open("a", encoding="utf-8") as fh:
        fh.write("refs = [\n" + "    backend_of,\n" * 200 + "]\n")

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert len(out["sites"]) == _edit_sites.MAX_SITES
    assert out["sites_omitted"] > 0
    assert out["complete"] is False
    assert f"over {_edit_sites.MAX_SITES} sites" in out["reasons"]


@pytest.mark.asyncio
async def test_module_import_then_attribute_access_is_found(session, repo_id, tmp_path):
    """``from pkg import catalog`` imports the module; ``catalog.backend_of`` is the site."""
    node = await _seed(
        session,
        repo_id,
        tmp_path,
        extra=[("app/mod_user.py", _DEF, "imports", {"imported_names_json": '["catalog"]'})],
    )
    (tmp_path / "app" / "mod_user.py").write_text(
        "from pkg import catalog\n\nvalue = catalog.backend_of(1)\n", encoding="utf-8"
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert ("app/mod_user.py", 3, "reference") in _sites(out)
    assert out["complete"] is True


@pytest.mark.asyncio
async def test_importer_with_unknown_names_is_followed_onward(session, repo_id, tmp_path):
    node = await _seed(
        session,
        repo_id,
        tmp_path,
        extra=[
            ("app/hub.py", _DEF, "imports", {"imported_names_json": "[]"}),
            ("app/far.py", "app/hub.py", "imports", {"imported_names_json": '["thing"]'}),
        ],
    )
    (tmp_path / "app" / "hub.py").write_text("from pkg.catalog import *\n", encoding="utf-8")
    (tmp_path / "app" / "far.py").write_text(
        "from app.hub import thing\n\nthing(backend_of)\n", encoding="utf-8"
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert ("app/far.py", 3, "reference") in _sites(out)


async def _seed_js(session, repo_id: str, tmp_path, files: dict[str, str], names='["pick"]'):
    """``web/a.ts`` defines ``pick`` on line 1; every other file imports it."""
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    node = GraphNode(
        id="js_sym",
        repository_id=repo_id,
        node_id="web/a.ts::pick",
        node_type="symbol",
        name="pick",
        file_path="web/a.ts",
        kind="function",
        language="typescript",
        start_line=1,
        end_line=1,
        created_at=_NOW,
    )
    importers = [
        _edge(40 + i, repo_id, rel, "web/a.ts", "imports", imported_names_json=names)
        for i, rel in enumerate(r for r in files if r != "web/a.ts")
    ]
    session.add_all([node, *importers])
    await session.flush()
    return node


@pytest.mark.asyncio
async def test_plain_named_import_in_typescript_is_complete(session, repo_id, tmp_path):
    node = await _seed_js(
        session,
        repo_id,
        tmp_path,
        {
            "web/a.ts": "export function pick(x: number): number { return x }\n",
            "web/b.tsx": "import { pick } from './a'\nconst v = cond ? pick : other\n",
        },
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert ("web/b.tsx", 2, "reference") in _sites(out)
    assert out["complete"] is True, out.get("reasons")


@pytest.mark.asyncio
async def test_default_export_is_incomplete(session, repo_id, tmp_path):
    """``import anything from './a'`` binds a default export under any name."""
    node = await _seed_js(
        session,
        repo_id,
        tmp_path,
        {
            "web/a.ts": "export default function pick(x: number) { return x }\n",
            "web/b.ts": "import chooser from './a'\nchooser(1)\n",
        },
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert out["complete"] is False
    assert "default export: importers may rename it" in out["reasons"]


@pytest.mark.asyncio
async def test_commonjs_destructure_rename_is_incomplete(session, repo_id, tmp_path):
    """Both the one-line and the multi-line ``{ pick: other } = require(...)``."""
    node = await _seed_js(
        session,
        repo_id,
        tmp_path,
        {
            "web/a.ts": "export function pick(x: number) { return x }\n",
            "web/one.js": "const { pick: choose } = require('./a')\nchoose(1)\n",
            "web/multi.js": "const {\n  pick: choose,\n} = require('./a')\nchoose(2)\n",
        },
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    sites = _sites(out)
    assert ("web/one.js", 2, "reference") in sites  # the alias is scanned for too
    assert ("web/multi.js", 4, "reference") in sites
    assert out["complete"] is False
    renamed = next(r for r in out["reasons"] if r.startswith("renamed on import or export"))
    assert "web/one.js" in renamed and "web/multi.js" in renamed


@pytest.mark.asyncio
async def test_rename_in_a_file_outside_the_re_export_chain_is_incomplete(
    session, repo_id, tmp_path
):
    """A file importing only the module is not followed, yet its rename still counts."""
    node = await _seed_js(
        session,
        repo_id,
        tmp_path,
        {
            "web/a.ts": "export function pick(x: number) { return x }\n",
            "web/c.ts": "import * as a from './a'\nexport const { pick: choose } = a\n",
        },
        names='["a"]',
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert out["complete"] is False
    assert any("web/c.ts" in r for r in out["reasons"])


@pytest.mark.asyncio
async def test_get_context_serves_references_for_a_symbol(setup_mcp, session, tmp_path):
    from repowise.server.mcp_server import get_context

    await _seed(session, setup_mcp, tmp_path)
    await session.commit()

    t = (await get_context([_SYM], include=["references"]))["targets"][_SYM]

    assert t["references"]["complete"] is True
    assert (_USER, 9, "call") in _sites(t["references"])


def _collector(tmp_path):
    from repowise.server.mcp_server._budget import OmissionCollector

    return OmissionCollector("get_context", store_path=tmp_path / "omissions.db")


@pytest.mark.asyncio
async def test_small_list_is_unchanged_by_the_per_file_cap(session, repo_id, tmp_path):
    node = await _seed(session, repo_id, tmp_path)

    bare = await reference_edit_set(session, repo_id, tmp_path, node)
    capped = await reference_edit_set(session, repo_id, tmp_path, node, _collector(tmp_path))

    assert json.dumps(capped) == json.dumps(bare)


@pytest.mark.asyncio
async def test_mock_heavy_python_test_collapses_and_sorts_after_callers(
    session, repo_id, tmp_path
):
    node = await _seed(
        session,
        repo_id,
        tmp_path,
        extra=[("tests/test_catalog.py", _DEF, "imports", {"imported_names_json": '["backend_of"]'})],
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_catalog.py").write_text(
        "from pkg.catalog import backend_of\n"
        + "".join(f"mock.patch('pkg.catalog.backend_of', side_effect={i})\n" for i in range(20)),
        encoding="utf-8",
    )
    collector = _collector(tmp_path)

    out = await reference_edit_set(session, repo_id, tmp_path, node, collector)

    test_rows = [s for s in _sites(out) if s[0] == "tests/test_catalog.py"]
    assert test_rows == [
        ("tests/test_catalog.py", 1, "import"),
        ("tests/test_catalog.py", 2, "reference"),
        ("tests/test_catalog.py", 3, "reference"),
        ("tests/test_catalog.py", 4, "reference"),
    ]
    assert _sites(out)[-len(test_rows) :] == test_rows  # production files first
    assert (_USER, 9, "call") in _sites(out)
    assert out["sites_omitted"] == 17
    assert out["sites_omitted_by_file"] == {"tests/test_catalog.py": 17}
    assert out["sites_reduced_reason"] == "per_file_reference_cap"
    assert out["complete"] is False
    assert any("sites_omitted_by_file" in r for r in out["reasons"])
    response: dict = {}
    collector.attach(response)
    assert response["_meta"]["omitted"]["refs"]


@pytest.mark.asyncio
async def test_mock_heavy_typescript_test_keeps_the_real_caller(session, repo_id, tmp_path):
    spec = "import { pick } from './a'\n" + "vi.mocked(pick).mockReturnValue(1)\n" * 30
    node = await _seed_js(
        session,
        repo_id,
        tmp_path,
        {
            "web/a.ts": "export function pick(x: number): number { return x }\n",
            "web/a.test.ts": spec,
            "web/b.ts": "import { pick } from './a'\nexport const run = () => [pick]\n",
        },
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node, _collector(tmp_path))

    paths = [p for p, _n, _k in _sites(out)]
    assert paths.index("web/b.ts") < paths.index("web/a.test.ts")
    assert ("web/b.ts", 2, "reference") in _sites(out)
    assert paths.count("web/a.test.ts") == 1 + _edit_sites.MAX_REFERENCES_PER_FILE
    assert out["sites_omitted_by_file"] == {"web/a.test.ts": 30 - _edit_sites.MAX_REFERENCES_PER_FILE}
    assert out["complete"] is False


@pytest.mark.asyncio
async def test_call_sites_are_never_collapsed(session, repo_id, tmp_path):
    extra = []
    for i in range(8):
        rel = f"svc/user{i}.py"
        (tmp_path / "svc").mkdir(exist_ok=True)
        (tmp_path / rel).write_text(
            "from pkg.catalog import backend_of\n" + "backend_of(1)\n" * 6, encoding="utf-8"
        )
        extra.append((rel, _DEF, "imports", {"imported_names_json": '["backend_of"]'}))
        extra.append(
            (f"{rel}::run", _SYM, "calls", {"call_lines_json": json.dumps(list(range(2, 8)))})
        )
    node = await _seed(session, repo_id, tmp_path, extra=extra)

    out = await reference_edit_set(session, repo_id, tmp_path, node, _collector(tmp_path))

    calls = [s for s in _sites(out) if s[2] == "call" and s[0].startswith("svc/user")]
    assert len(calls) == 8 * 6
    assert out["complete"] is True
    assert "sites_omitted_by_file" not in out


@pytest.mark.asyncio
async def test_get_context_collapses_a_flood_into_recoverable_counts(setup_mcp, session, tmp_path):
    from repowise.server.mcp_server import get_context

    await _seed(session, setup_mcp, tmp_path)
    await session.commit()
    with (tmp_path / _USER).open("a", encoding="utf-8") as fh:
        fh.write("spies = [\n" + "    backend_of,\n" * 40 + "]\n")

    resp = await get_context([_SYM], include=["references"])
    refs = resp["targets"][_SYM]["references"]

    assert refs["sites_omitted_by_file"] == {_USER: 40 + 1 - _edit_sites.MAX_REFERENCES_PER_FILE}
    assert (_USER, 9, "call") in _sites(refs)
    assert refs["complete"] is False
    assert resp["_meta"]["omitted"]["refs"]
