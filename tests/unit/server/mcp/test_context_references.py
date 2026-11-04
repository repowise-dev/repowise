"""include=["references"]: every live edit site of a symbol, and whether that is all of them."""

from __future__ import annotations

import json
import subprocess
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

    # Its graph lines are unknown, so a call is read from the text.
    assert ("app/new.py", 1, "call") in _sites(out)
    assert out["complete"] is False


@pytest.mark.asyncio
async def test_swift_module_visibility_is_never_complete(session, repo_id, tmp_path):
    node = await _seed(session, repo_id, tmp_path, language="swift")

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert (_USER, 9, "call") in _sites(out)
    assert out["complete"] is False
    assert "swift: same-package use is not scanned" in out["reasons"]


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
async def test_commonjs_destructure_rename_outside_an_import_line_is_incomplete(
    session, repo_id, tmp_path
):
    """A one-line ``{ pick: other } = require(...)`` is an import; a multi-line one is not seen as one."""
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
    assert "web/one.js" not in renamed and "web/multi.js" in renamed


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


_PICK = "export function pick(x: number) { return x }\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rel", "text", "alias_line"),
    [
        ("web/b.ts", "import { pick as choose } from './a'\n\nexport const f = () => choose(1)\n", 3),
        (
            "web/b.ts",
            "import {\n  other,\n  pick as choose,\n} from './a'\nfunction f() {\n  return choose(1)\n}\n",
            6,
        ),
        ("web/b.js", "const { pick: choose } = require('./a')\nchoose(1)\n", 2),
        ("web/b.ts", "import { pick as choose } from './a'\nconst k = new choose()\n", 2),
        ("web/b.tsx", "import { pick as choose } from './a'\nconst e = <choose a={1}></choose>\n", 2),
        ("web/b.ts", "import { pick as choose } from './a'\nchoose.mockReset()\n", 2),
        ("web/b.ts", "import { pick as choose } from './a'\nconst n = choose?.length\n", 2),
    ],
)
async def test_import_alias_used_only_locally_is_complete(
    session, repo_id, tmp_path, rel, text, alias_line
):
    node = await _seed_js(session, repo_id, tmp_path, {"web/a.ts": _PICK, rel: text})

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert (rel, alias_line, "reference") in _sites(out)
    assert out["complete"] is True, out.get("reasons")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "import { pick as choose } from './a'\nexport { choose }\n",
        "import { pick as choose } from './a'\nexport {\n  choose as chooser,\n}\n",
        "import { pick as choose } from './a'\nexport default choose\n",
        "import { pick as choose } from './a'\nexport const chooser = choose;\n",
        "export { pick as choose } from './a'\n",
        "const { pick: choose } = require('./a')\nmodule.exports = {\n  choose,\n}\n",
        "const { pick: choose } = require('./a')\nexports.chooser = choose\n",
        "import { pick as choose } from './a'\nexport = choose;\n",
        "import { pick as choose } from './a'\nexport const api = { choose }\n",
        "import { pick as choose } from './a'\nexport const api = {\n  choose,\n}\n",
        "import { pick as choose } from './a'\nexport const w = wrap(choose)\n",
        "import { pick as choose } from './a'\nexport const a = choose as T\n",
        "import { pick as choose } from './a'\nexport const a = choose, c = 1\n",
        "import { pick as choose } from './a'\nconst c = choose\nexport { c }\n",
        "import { pick as choose } from './a'\nconst c = choose\nexport default c\n",
        "const { pick: choose } = require('./a')\no.x = choose\nmodule.exports = o\n",
        "import { pick as choose } from './a'\nexport class K extends choose {}\n",
        "import { pick as choose } from './a'\nexport function g() { return choose }\n",
    ],
)
async def test_import_alias_handed_on_is_incomplete(session, repo_id, tmp_path, text):
    node = await _seed_js(session, repo_id, tmp_path, {"web/a.ts": _PICK, "web/b.js": text})

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert out["complete"] is False
    assert "renamed on import or export: web/b.js" in out["reasons"]


@pytest.mark.asyncio
async def test_aliases_in_two_files_are_both_scanned(session, repo_id, tmp_path):
    node = await _seed_js(
        session,
        repo_id,
        tmp_path,
        {
            "web/a.ts": _PICK,
            "web/b.ts": "import { pick as choose } from './a'\nchoose(1)\n",
            "web/c.ts": "import { pick as select } from './a'\nconst v = select(2)\n",
        },
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert ("web/b.ts", 2, "reference") in _sites(out)
    assert ("web/c.ts", 2, "reference") in _sites(out)
    assert out["complete"] is True, out.get("reasons")


@pytest.mark.asyncio
async def test_alias_matches_on_a_word_boundary_only(session, repo_id, tmp_path):
    node = await _seed_js(
        session,
        repo_id,
        tmp_path,
        {
            "web/a.ts": _PICK,
            "web/b.ts": (
                "import { pick as choose } from './a'\n"
                "const chooser = 1\n"
                "obj.choose_it = $choose\n"
                "choose(chooser)\n"
            ),
        },
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    b_lines = [n for p, n, _k in _sites(out) if p == "web/b.ts"]
    assert b_lines == [1, 4]
    assert out["complete"] is True, out.get("reasons")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("importer_names", "complete"),
    [(None, True), ('["value"]', True), ('["pick_backend"]', False), ("[]", False)],
)
async def test_python_module_alias_is_local_unless_another_module_imports_it(
    session, repo_id, tmp_path, importer_names, complete
):
    extra = [("app/local.py", _DEF, "imports", {"imported_names_json": '["backend_of"]'})]
    if importer_names is not None:
        extra.append(
            ("app/far.py", "app/local.py", "imports", {"imported_names_json": importer_names})
        )
    node = await _seed(session, repo_id, tmp_path, extra=extra)
    (tmp_path / "app" / "local.py").write_text(
        "from pkg.catalog import backend_of as pick_backend\n\nvalue = pick_backend(1)\n",
        encoding="utf-8",
    )

    (tmp_path / "app" / "far.py").write_text(
        "from app.local import pick_backend, value\n", encoding="utf-8"
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert ("app/local.py", 1, "import") in _sites(out)
    assert ("app/local.py", 3, "reference") in _sites(out)
    assert out["complete"] is complete, out.get("reasons")
    if not complete:
        assert "renamed on import or export: app/local.py" in out["reasons"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "complete"),
    [
        ("__all__ = [\n    'pick_backend',\n]\n", False),
        ("chooser = pick_backend\n", False),
        ("register(pick_backend)\n", False),
        ("value = pick_backend(1)\nname = pick_backend.__name__\n", True),
    ],
)
async def test_python_alias_value_use_leaves_the_file(session, repo_id, tmp_path, body, complete):
    node = await _seed(
        session,
        repo_id,
        tmp_path,
        extra=[("app/api.py", _DEF, "imports", {"imported_names_json": '["backend_of"]'})],
    )
    (tmp_path / "app" / "api.py").write_text(
        "from pkg.catalog import backend_of as pick_backend\n\n" + body, encoding="utf-8"
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert out["complete"] is complete, out.get("reasons")
    if not complete:
        assert "renamed on import or export: app/api.py" in out["reasons"]


@pytest.mark.asyncio
async def test_local_alias_references_share_the_per_file_cap(session, repo_id, tmp_path):
    spec = "import { pick as choose } from './a'\n" + "choose.mockReturnValue(1)\n" * 30
    node = await _seed_js(
        session, repo_id, tmp_path, {"web/a.ts": _PICK, "web/a.test.ts": spec}
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node, _collector(tmp_path))

    assert out["sites_omitted_by_file"] == {
        "web/a.test.ts": 30 - _edit_sites.MAX_REFERENCES_PER_FILE
    }
    assert not any(r.startswith("renamed") for r in out["reasons"])


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
async def test_a_small_set_keeps_every_reference_in_one_file(
    session, repo_id, tmp_path, monkeypatch
):
    node = await _seed(session, repo_id, tmp_path)
    with (tmp_path / _USER).open("a", encoding="utf-8") as fh:
        fh.write("spies = [\n" + "    backend_of,\n" * 6 + "]\n")

    bare = await reference_edit_set(session, repo_id, tmp_path, node)
    size = len(bare["sites"])
    assert size <= _edit_sites.SMALL_SET_LINES
    whole = await reference_edit_set(session, repo_id, tmp_path, node, _collector(tmp_path))
    assert json.dumps(whole) == json.dumps(bare)
    assert whole["complete"] is True

    # One row over the bound and the per-file cap applies again.
    monkeypatch.setattr(_edit_sites, "SMALL_SET_LINES", size - 1)
    capped = await reference_edit_set(session, repo_id, tmp_path, node, _collector(tmp_path))
    assert capped["sites_omitted_by_file"]
    assert capped["complete"] is False


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


@pytest.mark.parametrize(
    "line",
    [
        "export const g = choose.bind(null);",
        "export const f = choose.call;",
        "module.exports.x = choose.y;",
    ],
)
def test_alias_member_access_on_an_export_line_is_incomplete(line: str) -> None:
    lines = ['import { pick as choose } from "./d";', line]
    assert _edit_sites._aliases_leave("u.ts", lines, {"choose"}, {1})


async def _seed_package(
    session, repo_id, tmp_path, files: dict[str, str], language: str, symbol, git: bool = True
):
    """Write *files* and index *symbol* ``(path, name, start, end)``; files are not indexed,
    as only the live tree decides which share its package."""
    if git:
        # JVM package files are found by git grep.
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    path, name, start, end = symbol
    node = GraphNode(
        id="pkg_sym",
        repository_id=repo_id,
        node_id=f"{path}::{name}",
        node_type="symbol",
        name=name,
        file_path=path,
        kind="function" if language == "go" else "class",
        language=language,
        start_line=start,
        end_line=end,
        created_at=_NOW,
    )
    session.add(node)
    await session.flush()
    return node


_MODEL = "src/main/java/org/sp/petclinic/model"
_BASE_ENTITY = "package org.sp.petclinic.model;\n\npublic class BaseEntity {\n}\n"


def _model(cls: str, parent: str, package: str = "org.sp.petclinic.model") -> str:
    return f"package {package};\n\npublic class {cls} extends {parent} {{\n}}\n"


@pytest.mark.asyncio
async def test_java_same_package_subclasses_are_listed_and_complete(session, repo_id, tmp_path):
    node = await _seed_package(
        session,
        repo_id,
        tmp_path,
        {
            f"{_MODEL}/BaseEntity.java": _BASE_ENTITY,
            f"{_MODEL}/NamedEntity.java": _model("NamedEntity", "BaseEntity"),
            f"{_MODEL}/Person.java": _model("Person", "BaseEntity"),
            # Same package under the test root: visible without an import.
            "src/test/java/org/sp/petclinic/model/EntityTests.java": (
                "package org.sp.petclinic.model;\n\nclass EntityTests {\n"
                "    BaseEntity e = new BaseEntity();\n}\n"
            ),
            # Another package naming it fully qualified, with no import.
            "src/main/java/org/sp/petclinic/vet/Vet.java": (
                "package org.sp.petclinic.vet;\n\n"
                "class Vet extends org.sp.petclinic.model.BaseEntity {\n}\n"
            ),
            # A subpackage holding another BaseEntity is not a use.
            f"{_MODEL}/sub/Other.java": _model("BaseEntity", "Object", "org.sp.petclinic.model.sub"),
        },
        "java",
        (f"{_MODEL}/BaseEntity.java", "BaseEntity", 3, 4),
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    sites = _sites(out)
    assert (f"{_MODEL}/BaseEntity.java", 3, "definition") in sites
    assert (f"{_MODEL}/NamedEntity.java", 3, "reference") in sites
    assert (f"{_MODEL}/Person.java", 3, "reference") in sites
    assert ("src/test/java/org/sp/petclinic/model/EntityTests.java", 4, "reference") in sites
    assert ("src/main/java/org/sp/petclinic/vet/Vet.java", 3, "reference") in sites
    assert out["complete"] is True, out.get("reasons")


@pytest.mark.asyncio
async def test_java_package_without_git_to_search_is_incomplete(session, repo_id, tmp_path):
    node = await _seed_package(
        session,
        repo_id,
        tmp_path,
        {f"{_MODEL}/BaseEntity.java": _BASE_ENTITY},
        "java",
        (f"{_MODEL}/BaseEntity.java", "BaseEntity", 3, 4),
        git=False,
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert out["complete"] is False
    assert "java: could not search for package org.sp.petclinic.model" in out["reasons"]


_LEGACY = "package cobra\n\nfunc legacyArgs(args []string) error {\n\treturn nil\n}\n"


@pytest.mark.asyncio
async def test_go_package_is_its_live_directory(session, repo_id, tmp_path):
    node = await _seed_package(
        session,
        repo_id,
        tmp_path,
        {
            "args.go": _LEGACY,
            "command.go": "package cobra\n\nfunc run(a []string) error {\n\treturn legacyArgs(a)\n}\n",
            # A file the index skipped (generated, build-tagged) still shares the package.
            "zz_generated.go": "package cobra\n\nvar f = legacyArgs\n",
            # The external test package beside it imports it like any other.
            "args_test.go": 'package cobra_test\n\nimport "testing"\n',
            "doc/gen.go": "package doc\n\nfunc legacyArgs() {}\n",
        },
        "go",
        ("args.go", "legacyArgs", 3, 5),
        git=False,
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert ("command.go", 4, "reference") in _sites(out)
    assert ("zz_generated.go", 3, "reference") in _sites(out)
    assert not any(p == "doc/gen.go" for p, _n, _k in _sites(out))
    assert out["complete"] is True, out.get("reasons")


@pytest.mark.asyncio
async def test_go_directory_holding_another_package_is_incomplete(session, repo_id, tmp_path):
    node = await _seed_package(
        session,
        repo_id,
        tmp_path,
        {"args.go": _LEGACY, "gen.go": "//go:build ignore\n\npackage main\n"},
        "go",
        ("args.go", "legacyArgs", 3, 5),
        git=False,
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert out["complete"] is False
    assert "go: another package shares its directory: gen.go" in out["reasons"]


@pytest.mark.asyncio
async def test_kotlin_same_package_in_another_directory_is_listed(session, repo_id, tmp_path):
    node = await _seed_package(
        session,
        repo_id,
        tmp_path,
        {
            "src/com/ex/Util.kt": "package com.ex\n\nclass Helper {\n}\n",
            "src/com/ex/Main.kt": "package com.ex\n\nval h = Helper()\n",
            # Kotlin does not tie a package to its directory.
            "app/misc/Extra.kt": "package com.ex\n\nval e = Helper()\n",
        },
        "kotlin",
        ("src/com/ex/Util.kt", "Helper", 3, 4),
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert ("src/com/ex/Main.kt", 3, "reference") in _sites(out)
    assert ("app/misc/Extra.kt", 3, "reference") in _sites(out)
    assert out["complete"] is True, out.get("reasons")


_FIND = (
    "package cobra\n\nfunc run(args []string) error {\n\treturn Find(args)\n}\n\n"
    "func Find(args []string) error {\n\treturn nil\n}\n"
)


@pytest.mark.asyncio
async def test_dirty_file_labels_a_shifted_call_as_a_call(session, repo_id, tmp_path, monkeypatch):
    node = await _seed_package(
        session, repo_id, tmp_path, {"find.go": _FIND}, "go", ("find.go", "Find", 7, 9), git=False
    )
    session.add(_edge(1, repo_id, "find.go::run", "find.go::Find", "calls", call_lines_json="[4]"))
    await session.flush()
    # Three lines inserted above both: the call lands on Find's indexed line.
    edited = _FIND.replace("\n\nfunc run", "\n\n// a\n// b\n// c\nfunc run", 1)
    (tmp_path / "find.go").write_text(edited, encoding="utf-8")
    _dirty(monkeypatch, frozenset({"find.go"}))

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert _sites(out) == [("find.go", 10, "definition"), ("find.go", 7, "call")]
    assert not any(r.startswith("stale") for r in out.get("reasons", []))


_OP = """package p;

enum Op {
    PLUS {
        int apply(int a) { return a; }
    };
    abstract int apply(int a);
}

class Box {
    void put(int x) {}
    void put(String s) {}
}
"""


@pytest.mark.parametrize(
    ("symbol_id", "parent", "line"),
    [("p/Op.java::Box::put#1(String)", "Box", 13), ("p/Op.java::Op::PLUS::apply", "Op", 6)],
)
def test_live_definition_relocates_overloads_and_enum_constant_members(
    tmp_path, symbol_id, parent, line
):
    (tmp_path / "p").mkdir()
    # One line inserted at the top moves every symbol down by one.
    (tmp_path / "p" / "Op.java").write_text("// edited\n" + _OP, encoding="utf-8")
    node = GraphNode(
        node_id=symbol_id, file_path="p/Op.java", start_line=1, end_line=1, parent_symbol_id=parent
    )

    name = _edit_sites._bare_name(node)

    assert _edit_sites._live_definition(tmp_path, node, name, "java") == (line, line)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "facade"), [("", "UtilKt"), ('@file:JvmName("Tools")\n', "Tools")]
)
async def test_java_callers_of_a_kotlin_top_level_function_are_listed(
    session, repo_id, tmp_path, header, facade
):
    node = await _seed_package(
        session,
        repo_id,
        tmp_path,
        {
            "src/com/ex/Util.kt": f"{header}package com.ex\n\nfun foo() = 1\n",
            "src/app/A.java": (
                f"package app;\n\nimport com.ex.{facade};\n\n"
                f"class A {{ int a = {facade}.foo(); }}\n"
            ),
            "src/app/B.java": (
                f"package app;\n\nimport static com.ex.{facade}.foo;\n\nclass B {{ int b = foo(); }}\n"
            ),
        },
        "kotlin",
        ("src/com/ex/Util.kt", "foo", 3 + bool(header), 3 + bool(header)),
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    sites = _sites(out)
    assert ("src/app/A.java", 5, "reference") in sites
    assert ("src/app/B.java", 3, "import") in sites
    assert ("src/app/B.java", 5, "reference") in sites
    assert out["complete"] is True, out.get("reasons")


@pytest.mark.asyncio
async def test_oversize_package_file_is_incomplete(session, repo_id, tmp_path):
    node = await _seed_package(
        session,
        repo_id,
        tmp_path,
        {"args.go": _LEGACY, "big.go": "package cobra\n" + "// x\n" * 500_000},
        "go",
        ("args.go", "legacyArgs", 3, 5),
        git=False,
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert out["complete"] is False
    assert "unreadable: big.go" in out["reasons"]


@pytest.mark.asyncio
async def test_same_name_re_export_is_not_a_rename(session, repo_id, tmp_path):
    node = await _seed(
        session,
        repo_id,
        tmp_path,
        extra=[("pkg/__init__.py", _DEF, "imports", {"imported_names_json": '["backend_of"]'})],
    )
    (tmp_path / "pkg" / "__init__.py").write_text(
        "from .catalog import backend_of as backend_of\n", encoding="utf-8"
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert ("pkg/__init__.py", 1, "import") in _sites(out)
    assert out["complete"] is True, out.get("reasons")


async def _seed_method(session, repo_id, tmp_path, outside: str):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "pkg" / "store.py").write_text(
        "class Store:\n    def fetch_rows(self):\n        return []\n", encoding="utf-8"
    )
    (tmp_path / "app" / "use.py").write_text(
        "from pkg.store import Store\n\nrows = Store().fetch_rows()\n", encoding="utf-8"
    )
    (tmp_path / "app" / "other.py").write_text(outside, encoding="utf-8")
    sym = "pkg/store.py::Store::fetch_rows"
    node = GraphNode(
        id="ref_method",
        repository_id=repo_id,
        node_id=sym,
        node_type="symbol",
        name="fetch_rows",
        file_path="pkg/store.py",
        kind="method",
        language="python",
        start_line=2,
        end_line=3,
        created_at=_NOW,
    )
    session.add_all(
        [
            node,
            _edge(1, repo_id, "app/use.py", "pkg/store.py", "imports", imported_names_json='["Store"]'),
            _edge(2, repo_id, "app/use.py::__module__", sym, "calls", call_lines_json="[3]"),
        ]
    )
    await session.flush()
    return node


@pytest.mark.asyncio
async def test_member_with_no_untyped_access_elsewhere_is_complete(session, repo_id, tmp_path):
    node = await _seed_method(session, repo_id, tmp_path, "fetch_rows_total = 0\n")

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert ("app/use.py", 3, "call") in _sites(out)
    assert out["complete"] is True, out.get("reasons")


@pytest.mark.asyncio
async def test_member_accessed_on_an_untyped_receiver_names_the_file(session, repo_id, tmp_path):
    node = await _seed_method(
        session, repo_id, tmp_path, "def load(store):\n    return store.fetch_rows()\n"
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert out["complete"] is False
    assert "member: an unscanned file names it: app/other.py" in (
        out["reasons"]
    )


@pytest.mark.asyncio
async def test_member_named_by_an_inherited_bare_use_elsewhere_is_incomplete(
    session, repo_id, tmp_path
):
    # No receiver is written, yet the file may still reach the member.
    node = await _seed_method(
        session, repo_id, tmp_path, "class Sub(Base):\n    run = fetch_rows\n"
    )

    out = await reference_edit_set(session, repo_id, tmp_path, node)

    assert out["complete"] is False
    assert "member: an unscanned file names it: app/other.py" in out["reasons"]
