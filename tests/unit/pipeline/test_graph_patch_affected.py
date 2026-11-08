"""Patching the graph through adds, removes and surface changes must equal a full build.

A refreshing patch widens its scope to every file the edit can reach
(``pipeline.incremental.affected``) and re-runs the whole-graph passes. Each
case below edits a small Python, TypeScript or JavaScript tree, patches the
graph from the persisted snapshot, and compares nodes with every attribute
and edges with every attribute against a from-scratch build of the same
tree. A Java tree shows the languages outside the set still fall back.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from repowise.core.pipeline.incremental import build_repo_graph
from repowise.core.pipeline.incremental.resolution_cache import save_resolutions


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _norm(value: Any) -> Any:
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(sorted((_norm(v) for v in value), key=repr))
    if isinstance(value, dict):
        return tuple(sorted((k, _norm(v)) for k, v in value.items()))
    return value


def _graph(graph_builder: Any) -> tuple[dict, dict]:
    g = graph_builder.graph()
    return (
        {n: _norm(d) for n, d in g.nodes(data=True)},
        {(u, v): _norm(d) for u, v, d in g.edges(data=True)},
    )


def _seed(graph_builder: Any) -> dict[str, list]:
    save_resolutions(
        Path(graph_builder._repo_path) / ".repowise",
        graph_builder._parsed_files.values(),
        graph_builder.import_resolutions,
    )
    g = graph_builder.graph()
    return {
        "nodes": [(n, dict(d)) for n, d in g.nodes(data=True)],
        "edges": [(u, v, dict(d)) for u, v, d in g.edges(data=True)],
    }


def _index(tmp_path: Path, files: dict[str, str]) -> tuple[Path, dict[str, list]]:
    tree = tmp_path / "repo"
    _write(tree, files)
    return tree, _seed(build_repo_graph(tree, [], collect_sources=True)[2])


def _patch(tree: Path, seed: dict[str, list]) -> Any:
    return build_repo_graph(
        tree, [], collect_sources=True, seed_edges=seed, patch_requested=True
    )[2]


def _assert_equals_full(tree: Path, patched: Any, tmp_path: Path) -> None:
    oracle = tmp_path / "oracle"
    shutil.copytree(tree, oracle, ignore=shutil.ignore_patterns(".repowise"))
    full = build_repo_graph(oracle, [], collect_sources=True)[2]
    patched_nodes, patched_edges = _graph(patched)
    full_nodes, full_edges = _graph(full)
    assert patched_nodes.keys() == full_nodes.keys()
    assert patched_edges.keys() == full_edges.keys()
    assert patched_nodes == full_nodes
    assert patched_edges == full_edges


def _edit(**changes: str | None) -> Any:
    """Write each ``path=text`` (``__`` stands for ``/``); ``None`` deletes the file."""

    def apply(tree: Path) -> None:
        for key, text in changes.items():
            path = tree / key.replace("__", "/").replace("_DOT_", ".")
            if text is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)

    return apply


_PY = {
    "pkg/__init__.py": "from .models import Base\n",
    "pkg/models.py": (
        "class Base:\n"
        "    def save(self):\n        return 1\n\n"
        "class User(Base):\n"
        "    def name(self):\n        return 'u'\n"
    ),
    "pkg/util.py": "def slug(text):\n    return text.lower()\n\ndef clean(text):\n    return slug(text)\n",
    "app/service.py": (
        "from pkg.models import User\nfrom pkg.util import clean\n\n"
        "def handle(raw):\n    user = User()\n    user.save()\n    return clean(raw) + user.name()\n"
    ),
    "app/views.py": "from app.service import handle\n\ndef index():\n    return handle('x')\n",
    "app/unrelated.py": "def ping():\n    return 'pong'\n",
}

_TS = {
    "src/index.ts": "export * from './util';\nexport { Store as Repo } from './store';\n",
    "src/util.ts": "export function slug(s: string) { return s.toLowerCase(); }\nexport function trim(s: string) { return s.trim(); }\n",
    "src/store.ts": (
        "import { slug } from './util';\n"
        "export class Base { save() { return 1; } }\n"
        "export class Store extends Base { key(s: string) { return slug(s); } }\n"
    ),
    "src/app.ts": (
        "import { Repo, trim } from './index';\n"
        "export function run(s: string) { const r = new Repo(); r.save(); return r.key(trim(s)); }\n"
    ),
    "src/other.ts": "export function ping() { return 'pong'; }\n",
}

_JS = {
    "lib/math.js": "export function add(a, b) { return a + b; }\nexport function mul(a, b) { return a * b; }\n",
    "lib/calc.js": "import { add } from './math.js';\nexport function total(xs) { return xs.reduce(add, 0); }\n",
    "lib/main.js": "import { total } from './calc.js';\nexport default function main() { return total([1, 2]); }\n",
    "lib/entry.js": "import main from './main.js';\nmain();\n",
    "lib/noise.js": "export function noise() { return 0; }\n",
}

_CASES = [
    # Python
    pytest.param(_PY, _edit(pkg__helpers_DOT_py="def slug(text):\n    return text\n"), id="py-add-competing-definer"),
    pytest.param(_PY, _edit(pkg__util_DOT_py=None), id="py-remove-imported-module"),
    pytest.param(
        _PY,
        _edit(pkg__util_DOT_py="def slugify(text):\n    return text.lower()\n\ndef clean(text):\n    return slugify(text)\n"),
        id="py-rename-function",
    ),
    pytest.param(
        _PY,
        _edit(pkg__models_DOT_py=_PY["pkg/models.py"].replace("class User(Base):", "class User:")),
        id="py-drop-base-class",
    ),
    pytest.param(
        _PY,
        _edit(pkg__models_DOT_py=_PY["pkg/models.py"] + "\nclass Admin(User):\n    def save(self):\n        return 2\n"),
        id="py-add-override",
    ),
    pytest.param(
        _PY,
        _edit(app__service_DOT_py=None, app__core__service_DOT_py=_PY["app/service.py"]),
        id="py-move-file",
    ),
    pytest.param(
        _PY,
        _edit(pkg__models_DOT_py=_PY["pkg/models.py"].replace("    def save(self):\n        return 1\n", "    def __init__(self):\n        self.x = 1\n\n    def save(self):\n        return 1\n")),
        id="py-add-constructor",
    ),
    # TypeScript
    pytest.param(_TS, _edit(src__extra_DOT_ts="export function trim(s: string) { return s; }\n"), id="ts-add-competing-definer"),
    pytest.param(_TS, _edit(src__util_DOT_ts=None), id="ts-remove-barrel-target"),
    pytest.param(
        _TS,
        _edit(src__index_DOT_ts="export * from './util';\nexport { Base as Repo } from './store';\n"),
        id="ts-retarget-reexport-alias",
    ),
    pytest.param(
        _TS,
        _edit(src__util_DOT_ts=_TS["src/util.ts"].replace("trim(s", "strip(s")),
        id="ts-rename-export",
    ),
    pytest.param(
        _TS,
        _edit(src__store_DOT_ts=_TS["src/store.ts"].replace("class Store extends Base", "class Store")),
        id="ts-drop-extends",
    ),
    pytest.param(_TS, _edit(src__util__index_DOT_ts="export function slug(s: string) { return s; }\n"), id="ts-add-directory-index"),
    # JavaScript
    pytest.param(_JS, _edit(lib__main_DOT_js="import { total } from './calc.js';\nexport default function start() { return total([3]); }\n"), id="js-change-default-export"),
    pytest.param(_JS, _edit(lib__math_DOT_js=None), id="js-remove-module"),
    pytest.param(_JS, _edit(lib__more_DOT_js="export function add(a, b) { return a - b; }\n"), id="js-add-competing-definer"),
]


@pytest.mark.parametrize(("files", "edit"), _CASES)
def test_refreshing_patch_equals_a_full_rebuild(
    tmp_path: Path, files: dict[str, str], edit: Any
) -> None:
    tree, seed = _index(tmp_path, files)
    edit(tree)

    patched = _patch(tree, seed)

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert patched.patch_plan.refresh
    _assert_equals_full(tree, patched, tmp_path)


def test_unreached_files_stay_out_of_the_scope(tmp_path: Path) -> None:
    tree, seed = _index(tmp_path, _PY)
    _edit(pkg__util_DOT_py=_PY["pkg/util.py"] + "\ndef shout(text):\n    return text.upper()\n")(tree)

    patched = _patch(tree, seed)

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert "app/unrelated.py" not in patched.patch_plan.scope
    assert "app/views.py" not in patched.patch_plan.scope
    _assert_equals_full(tree, patched, tmp_path)


def test_chained_patches_stay_equal_to_full_builds(tmp_path: Path) -> None:
    tree, seed = _index(tmp_path, _TS)
    edits = [
        _edit(src__extra_DOT_ts="export function trim(s: string) { return s; }\n"),
        _edit(src__util_DOT_ts=_TS["src/util.ts"].replace("trim(s", "strip(s")),
        _edit(src__extra_DOT_ts=None),
    ]
    for step, edit in enumerate(edits):
        edit(tree)
        patched = _patch(tree, seed)
        assert patched.patch_plan.eligible, (step, patched.patch_plan.reason)
        _assert_equals_full(tree, patched, tmp_path / f"step{step}")
        seed = _seed(patched)


_JAVA = {
    "src/com/a/Util.java": "package com.a;\npublic class Util { public static int one() { return 1; } }\n",
    "src/com/a/Svc.java": "package com.a;\npublic class Svc { int go() { return Util.one(); } }\n",
}


@pytest.mark.parametrize(
    ("edit", "reason"),
    [
        pytest.param(_edit(src__com__a__Extra_DOT_java="package com.a;\npublic class Extra {}\n"), "file added", id="java-add"),
        pytest.param(_edit(src__com__a__Util_DOT_java=None), "file removed", id="java-remove"),
        pytest.param(
            _edit(src__com__a__Util_DOT_java=_JAVA["src/com/a/Util.java"].replace("one()", "uno()")),
            "dependency surface changed",
            id="java-surface",
        ),
    ],
)
def test_languages_outside_the_set_still_fall_back(
    tmp_path: Path, edit: Any, reason: str
) -> None:
    tree, seed = _index(tmp_path, _JAVA)
    edit(tree)

    patched = _patch(tree, seed)

    assert not patched.patch_plan.eligible
    assert reason in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path)


def test_a_reached_file_outside_the_patchable_languages_falls_back(tmp_path: Path) -> None:
    files = {**_PY, "scripts/build.sh": "#!/bin/sh\nslug() { echo x; }\nslug\n"}
    tree, seed = _index(tmp_path, files)
    _edit(pkg__helpers_DOT_py="def slug(text):\n    return text\n")(tree)

    patched = _patch(tree, seed)

    if patched.patch_plan.eligible:
        assert "scripts/build.sh" not in patched.patch_plan.scope
    else:
        assert "shell" in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path)


# --- the planner's pieces -------------------------------------------------


def _sym(name: str, parent: str | None = None, kind: str = "function") -> Any:
    return SimpleNamespace(
        id=f"f.py::{name}", name=name, qualified_name=name, kind=kind, signature=f"{name}()",
        visibility="public", decorators=[], modifiers=[], parent_name=parent,
        is_declaration=False, is_exported_symbol=True, is_async=False,
        type_parameter_count=0, extension_receiver=None, written_receiver=None,
        language="python",
    )


def _parsed(path: str, symbols=(), heritage=(), exports=(), imports=(), language="python") -> Any:
    return SimpleNamespace(
        file_info=SimpleNamespace(path=path, language=language),
        symbols=list(symbols), heritage=list(heritage), exports=list(exports),
        export_aliases={}, registrars=frozenset(), module_name=None, imports=list(imports),
    )


def test_surface_keys_name_what_changed_and_map_constructors_to_their_class() -> None:
    from repowise.core.pipeline.incremental.surface_names import surface_keys

    before = _parsed("m.py", [_sym("Model", kind="class"), _sym("keep")])
    after = _parsed(
        "m.py", [_sym("Model", kind="class"), _sym("keep"), _sym("__init__", "Model", "method"), _sym("fresh")]
    )

    keys = surface_keys({"m.py": (before, after)}, [after])

    assert {"Model", "fresh"} <= keys.names
    assert "keep" not in keys.names
    assert "__init__" not in keys.names
    assert keys.stems == frozenset()


def test_surface_keys_follow_inherited_members_and_moved_file_stems() -> None:
    from repowise.core.pipeline.incremental.surface_names import surface_keys

    base = _parsed(
        "base.py",
        [_sym("Base", kind="class"), _sym("save", "Base", "method")],
        heritage=[SimpleNamespace(child_name="Base", parent_name="Root", kind="extends")],
    )
    root = _parsed("root.py", [_sym("Root", kind="class"), _sym("load", "Root", "method")])
    added = _parsed(
        "pkg/__init__.py",
        [_sym("Child", kind="class")],
        heritage=[SimpleNamespace(child_name="Child", parent_name="Base", kind="extends")],
    )

    keys = surface_keys({"pkg/__init__.py": (None, added)}, [base, root, added])

    assert {"Child", "Base", "save", "load"} <= keys.names
    assert {"__init__", "pkg"} <= keys.stems


def test_a_changed_default_export_reaches_through_importers() -> None:
    from repowise.core.pipeline.incremental.surface_names import surface_keys

    before = _parsed("a.ts", exports=["default", "x"], language="typescript")
    after = _parsed("a.ts", exports=["x"], language="typescript")

    keys = surface_keys({"a.ts": (before, after)}, [after])

    assert keys.importers_of == ("a.ts",)


def test_path_set_changes_that_re_key_every_file_are_refused() -> None:
    from repowise.core.pipeline.incremental.surface_names import path_set_reason

    py = _parsed("pkg/mod.py")
    init = _parsed("pkg/__init__.py")
    ts = _parsed("web/a.ts", language="typescript")

    assert "language" in path_set_reason({"pkg/mod.py"}, [py, ts], {"web/a.ts": (None, ts)})
    assert "module path" in path_set_reason({"pkg/mod.py"}, [py, init], {"pkg/__init__.py": (None, init)})
    assert path_set_reason({"pkg/mod.py"}, [py], {}) is None


def test_files_spelling_matches_whole_words_and_folds_stems() -> None:
    from repowise.core.pipeline.incremental.affected import _files_spelling

    def pf(path: str) -> Any:
        return SimpleNamespace(
            imports=[1], calls=[], heritage=[], type_refs=[], references=[], registrars=(), schema_refs=[],
            file_info=SimpleNamespace(path=path, language="typescript"),
        )

    files = {p: pf(p) for p in ("a.ts", "b.ts", "c.ts", "d.ts")}
    source = {
        "a.ts": b"call(slugify)",
        "b.ts": b"call(slug)",
        "c.ts": b"type T = Button",
        "d.ts": b"export default 1",
    }

    found = _files_spelling(files, source, {"slug", "default"}, {"button"}, set())

    assert found == {"b.ts", "c.ts"}


def test_too_large_a_reach_is_refused() -> None:
    from repowise.core.pipeline.incremental.affected import _scope_refusal

    files = {
        f"f{i}.py": SimpleNamespace(
            imports=[1], calls=[], heritage=[], type_refs=[], references=[], registrars=(), schema_refs=[],
            file_info=SimpleNamespace(path=f"f{i}.py", language="python"), symbols=[],
        )
        for i in range(100)
    }

    assert _scope_refusal({f"f{i}.py" for i in range(20)}, files) is None
    assert "reaches 21 of 100" in _scope_refusal({f"f{i}.py" for i in range(21)}, files)
    assert _scope_refusal({f"f{i}.py" for i in range(8)}, dict(list(files.items())[:10])) is None
