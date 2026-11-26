"""Calls into packages: kept beside the graph for proven import bindings only."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.external_calls import ExternalCallSite, package_root
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file

_LANG = {".py": "python", ".ts": "typescript", ".js": "javascript"}


def _build(tmp_path: Path, files: dict[str, str]) -> GraphBuilder:
    builder = GraphBuilder(repo_path=tmp_path)
    for rel, src in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src, encoding="utf-8")
        info = FileInfo(
            path=rel,
            abs_path=str(path),
            language=_LANG[path.suffix],  # type: ignore[arg-type]
            size_bytes=path.stat().st_size,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        builder.add_file(parse_file(info, src.encode("utf-8")))
    builder.build()
    return builder


def _sites(builder: GraphBuilder) -> list[tuple]:
    return sorted(
        (s.file_path, s.line, s.caller_symbol_id, s.module, s.symbol_path, s.binding)
        for sites in (builder.external_call_sites or {}).values()
        for s in sites
    )


@pytest.mark.parametrize(
    ("rel", "src", "expected"),
    [
        (
            "a.py",
            "from yaml import safe_load as sl\n\ndef f(x):\n    sl(x)\n",
            ("a.py", 4, "a.py::f", "yaml", "safe_load", "named"),
        ),
        (
            "a.py",
            "import requests\n\ndef f(x):\n    requests.get(x)\n",
            ("a.py", 4, "a.py::f", "requests", "get", "module"),
        ),
        (
            "a.py",
            "import numpy as np\n\ndef f(x):\n    np.linalg.norm(x)\n",
            ("a.py", 4, "a.py::f", "numpy", "linalg.norm", "module"),
        ),
        (
            "a.py",
            "import os.path\n\ndef f(x):\n    os.path.join(x)\n",
            ("a.py", 4, "a.py::f", "os.path", "join", "module"),
        ),
        (
            "b.ts",
            "import { merge } from 'lodash';\nexport function g(x: any) {\n  merge(x);\n}\n",
            ("b.ts", 3, "b.ts::g", "lodash", "merge", "named"),
        ),
        (
            "b.ts",
            "import * as fs from 'fs';\nexport function g(x: any) {\n  fs.readFileSync(x);\n}\n",
            ("b.ts", 3, "b.ts::g", "fs", "readFileSync", "module"),
        ),
        (
            "b.ts",
            "import axios from 'axios';\nexport function g(x: any) {\n  axios.get(x);\n}\n",
            ("b.ts", 3, "b.ts::g", "axios", "default.get", "default"),
        ),
        (
            "c.js",
            "const cp = require('child_process');\nfunction g(x) {\n  cp.exec(x);\n}\n",
            ("c.js", 3, "c.js::g", "child_process", "exec", "module"),
        ),
        (
            "c.js",
            "const { spawn } = require('child_process');\nfunction g(x) {\n  spawn(x);\n}\n",
            ("c.js", 3, "c.js::g", "child_process", "spawn", "named"),
        ),
    ],
)
def test_each_proven_binding_form_is_recorded(
    tmp_path: Path, rel: str, src: str, expected: tuple
) -> None:
    assert _sites(_build(tmp_path, {rel: src})) == [expected]


def test_a_parameter_named_like_the_import_is_not_the_package(tmp_path: Path) -> None:
    src = "import requests\n\ndef f(requests):\n    requests.get(1)\n"
    assert _sites(_build(tmp_path, {"a.py": src})) == []


def test_a_function_local_import_binds_only_its_function(tmp_path: Path) -> None:
    src = (
        "def f(x):\n    import requests\n    requests.get(x)\n\n"
        "def g(requests_like):\n    requests.get(1)\n"
    )
    sites = _sites(_build(tmp_path, {"a.py": src}))
    assert [s[2] for s in sites] == ["a.py::f"]


def test_a_require_in_one_function_does_not_bind_another(tmp_path: Path) -> None:
    src = (
        "function f(x) {\n  const cp = require('child_process');\n  cp.exec(x);\n}\n"
        "function g(x) {\n  cp.exec(x);\n}\n"
    )
    sites = _sites(_build(tmp_path, {"c.js": src}))
    assert [s[2] for s in sites] == ["c.js::f"]


def test_a_repo_barrel_re_exporting_a_package_is_not_followed(tmp_path: Path) -> None:
    files = {
        "lib/index.ts": "export { merge } from 'lodash';\n",
        "app.ts": "import { merge } from './lib';\nexport function g(x: any) {\n  merge(x);\n}\n",
    }
    sites = _sites(_build(tmp_path, files))
    assert [s for s in sites if s[0] == "app.ts"] == []


def test_a_chained_call_records_only_its_head(tmp_path: Path) -> None:
    src = "import requests\n\ndef f(x):\n    requests.Session().get(x)\n"
    assert [s[4] for s in _sites(_build(tmp_path, {"a.py": src}))] == ["Session"]


def test_a_call_into_repo_code_is_an_edge_not_a_package_call(tmp_path: Path) -> None:
    files = {
        "util.py": "def helper(x):\n    return x\n",
        "a.py": "from util import helper\n\ndef f(x):\n    helper(x)\n",
    }
    builder = _build(tmp_path, files)
    assert _sites(builder) == []
    assert builder.graph().has_edge("a.py::f", "util.py::helper")


def test_package_calls_add_no_graph_node_or_edge(tmp_path: Path) -> None:
    src = (
        "import requests\nfrom yaml import safe_load\n\n"
        "def f(x):\n    requests.get(x)\n    safe_load(x)\n"
    )
    builder = _build(tmp_path, {"a.py": src})
    graph = builder.graph()
    assert len(_sites(builder)) == 2
    assert not any(
        d.get("edge_type") in ("calls", "references")
        and (str(u).startswith("external:") or str(v).startswith("external:"))
        for u, v, d in graph.edges(data=True)
    )
    # Only the file's import edges reach the package nodes.
    assert {
        d["edge_type"] for _, v, d in graph.edges(data=True) if str(v).startswith("external:")
    } == {"imports"}


def test_a_full_build_has_no_scope(tmp_path: Path) -> None:
    builder = _build(tmp_path, {"a.py": "import requests\n\ndef f(x):\n    requests.get(x)\n"})
    assert builder.external_call_scope is None
    assert set(builder.external_call_sites or {}) == {"a.py"}


def test_record_round_trips_and_package_roots() -> None:
    site = ExternalCallSite(
        "a.py", 1, "a.py::f", "npm", "@s/p/sub", "@s/p", "external:@s/p/sub", "x", "y", "named"
    )
    assert ExternalCallSite.from_dict(site.as_dict()) == site
    assert package_root("npm", "@s/p/sub") == "@s/p"
    assert package_root("npm", "lodash/merge") == "lodash"
    assert package_root("pypi", "os.path") == "os"


def test_a_path_alias_is_never_a_package(tmp_path: Path) -> None:
    src = "import { cn } from '@/lib/utils';\nexport function g(x: any) {\n  cn(x);\n}\n"
    assert _sites(_build(tmp_path, {"b.ts": src})) == []


def test_an_unresolved_module_under_a_repo_directory_is_repo_code(tmp_path: Path) -> None:
    files = {
        "app/__init__.py": "",
        "a.py": "from app.missing import thing\n\ndef f(x):\n    thing(x)\n",
    }
    assert _sites(_build(tmp_path, files)) == []


def test_a_module_level_rebind_hides_the_import(tmp_path: Path) -> None:
    src = "import requests\nrequests = object()\n\ndef f(x):\n    requests.get(x)\n"
    assert _sites(_build(tmp_path, {"a.py": src})) == []


def test_two_imports_of_one_name_bind_neither_provably(tmp_path: Path) -> None:
    src = (
        "try:\n    import ujson as json\nexcept ImportError:\n    import json\n\n"
        "def f(x):\n    json.dumps(x)\n"
    )
    assert _sites(_build(tmp_path, {"a.py": src})) == []


def test_a_check_that_raises_marks_the_build_incomplete(tmp_path: Path, monkeypatch) -> None:
    from repowise.core.ingestion import call_external

    def boom(self, file_path, call):
        raise IndexError("short source")

    monkeypatch.setattr(call_external.ExternalCallsMixin, "_external_call_site", boom)
    builder = _build(tmp_path, {"a.py": "import requests\n\ndef f(x):\n    requests.get(x)\n"})
    assert builder.external_call_sites == {}
    assert builder.external_calls_complete is False
