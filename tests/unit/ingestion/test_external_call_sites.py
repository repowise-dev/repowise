"""Calls into packages: kept beside the graph for proven import bindings only."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.external_calls import ExternalCallSite, package_root
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file

_LANG = {
    ".py": "python",
    ".ts": "typescript",
    ".js": "javascript",
    ".java": "java",
    ".kt": "kotlin",
}


def _build(tmp_path: Path, files: dict[str, str]) -> GraphBuilder:
    builder = GraphBuilder(repo_path=tmp_path)
    for rel, src in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, so the file on disk matches what was parsed on every platform.
        path.write_bytes(src.encode("utf-8"))
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


_JAVA_ORDERS = """\
import com.acme.billing.BillingClient;
import com.acme.billing.Api;
import static com.acme.billing.Charges.refund;
public class Orders {
    private BillingClient billing;
    public void place(String id, Api.Inner inner) {
        billing.charge(id);
        refund(id);
        inner
            .go();
    }
}
"""


def test_a_java_call_is_bound_by_its_declared_type_or_a_static_import(tmp_path: Path) -> None:
    builder = _build(tmp_path, {"Orders.java": _JAVA_ORDERS})
    # The number of arguments selects one of several overloads of the called name,
    # and the name may sit on a later line than the call's receiver.
    sites = [s for file_sites in builder.external_call_sites.values() for s in file_sites]
    assert {s.line: (s.argument_count, s.name_line, s.name_column) for s in sites} == {
        7: (1, 7, 16),
        8: (1, 8, 8),
        9: (0, 10, 13),
    }
    assert _sites(builder) == [
        (
            "Orders.java", 7, "Orders.java::Orders::place",
            "com.acme.billing.BillingClient", "BillingClient.charge", "named",
        ),
        (
            "Orders.java", 8, "Orders.java::Orders::place",
            "com.acme.billing.Charges.refund", "Charges.refund", "named",
        ),
        (
            "Orders.java", 9, "Orders.java::Orders::place",
            "com.acme.billing.Api", "Api.Inner.go", "named",
        ),
    ]  # fmt: skip


def test_a_type_the_one_on_demand_import_brings_in_binds_a_java_call(tmp_path: Path) -> None:
    files = {
        # Guards, each killed by mutate.sh: `ThreadLocal` is java.lang's, `Exception` a
        # builtin the receiver typing marks, and `Packet` a class of the file's package.
        # `sftp` is no name the import binds.
        "com/shop/Transfer.java": (
            "package com.shop;\nimport com.acme.sftp.*;\npublic class Transfer {\n"
            "    private Channel channel;\n    private Packet packet;\n"
            "    public void send(Sftp sftp, ThreadLocal<String> local) {\n"
            "        channel.put(local.get());\n        sftp.open();\n        packet.seal();\n"
            "    }\n    public void fail(Exception e) {\n        e.getMessage();\n    }\n}\n"
        ),
        "com/shop/Packet.java": "package com.shop;\npublic class Packet {}\n",
        # A guard, killed by mutate.sh: two on-demand imports leave `Channel` open.
        "Two.java": (
            "import com.acme.sftp.*;\nimport com.acme.ftp.*;\npublic class Two {\n"
            "    private Channel channel;\n    public void send() {\n        channel.put();\n"
            "    }\n}\n"
        ),
    }
    assert [(s[1], s[3], s[4]) for s in _sites(_build(tmp_path, files))] == [
        (7, "com.acme.sftp.*", "Channel.put"),
        (8, "com.acme.sftp.*", "Sftp.open"),
    ]


def test_a_kotlin_call_is_bound_by_a_declared_or_returned_type(tmp_path: Path) -> None:
    files = {
        "Provider.kt": (
            "import com.acme.billing.BillingClient\n"
            "interface Provider {\n    fun client(): BillingClient\n}\n"
        ),
        # `chargeTwice` may be an extension of BillingClient or a member of it.
        "Orders.kt": (
            "import com.acme.billing.BillingClient\nimport com.acme.billing.ext.chargeTwice\n"
            "class Orders(private val billing: BillingClient, private val provider: Provider) {\n"
            "    fun place(id: String) {\n        billing.charge(id)\n"
            "        billing.chargeTwice(id)\n        provider.client().charge(id)\n"
            "        provider.client().chargeTwice(id)\n"
            "    }\n}\n"
        ),
    }
    assert _sites(_build(tmp_path, files)) == [
        (
            "Orders.kt", line, "Orders.kt::Orders::place",
            "com.acme.billing.BillingClient", "BillingClient.charge", "named",
        )
        for line in (5, 7)
    ]  # fmt: skip


def test_a_class_of_a_java_package_of_the_repo_is_repo_code_and_one_below_it_is_not(
    tmp_path: Path,
) -> None:
    files = {
        # DaggerAppComponent is generated at build time in com.me.di.
        "com/me/di/AppComponent.java": "package com.me.di;\npublic interface AppComponent {}\n",
        "com/me/App.java": (
            "package com.me;\nimport com.me.di.DaggerAppComponent;\n"
            "import static com.me.di.DaggerAppComponent.builder;\n"
            "import com.me.billing.BillingClient;\npublic class App {\n"
            "    public void start() {\n        DaggerAppComponent.create();\n        builder();\n"
            "        BillingClient.create();\n    }\n}\n"
        ),
    }
    assert [s[3] for s in _sites(_build(tmp_path, files))] == ["com.me.billing.BillingClient"]


def test_a_java_static_import_binds_no_call_on_a_receiver(tmp_path: Path) -> None:
    # Only a Kotlin extension is called on a receiver.
    src = (
        "import com.acme.billing.BillingClient;\nimport static com.acme.util.Util.charge;\n"
        "public class Orders {\n    private BillingClient billing;\n"
        "    public void place(String id) {\n        billing.charge(id);\n    }\n}\n"
    )
    assert [s[4] for s in _sites(_build(tmp_path, {"Orders.java": src}))] == [
        "BillingClient.charge"
    ]


def test_a_field_an_ancestor_declares_is_typed_by_the_ancestors_import(tmp_path: Path) -> None:
    files = {
        "Base.java": (
            "import com.acme.billing.BillingClient;\n"
            "public class Base {\n    protected BillingClient billing;\n}\n"
        ),
        # A class of this file named like the type is not the type of Base's field.
        "Orders.java": (
            "public class Orders extends Base {\n"
            "    public void place(String id) {\n        billing.charge(id);\n    }\n}\n"
            "class BillingClient {}\n"
        ),
    }
    assert [(s[0], s[3], s[4]) for s in _sites(_build(tmp_path, files))] == [
        ("Orders.java", "com.acme.billing.BillingClient", "BillingClient.charge")
    ]


def test_a_python_annotation_does_not_bind_a_receiver(tmp_path: Path) -> None:
    # A guard: only a type the compiler checks proves the package.
    src = "from requests import Session\n\ndef f(s: Session):\n    s.get(1)\n"
    assert _sites(_build(tmp_path, {"a.py": src})) == []


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


def test_a_java_chain_is_bound_by_the_type_a_repo_method_returns(tmp_path: Path) -> None:
    files = {
        "Provider.java": (
            "import com.acme.offers.Promotions;\n"
            "public interface Provider {\n    Promotions promotions();\n}\n"
        ),
        # `first()` returns a type of the package, which names none here.
        "Offers.java": (
            "public class Offers {\n    private Provider provider;\n"
            "    public void accept(String id) {\n        provider.promotions().accept(id);\n"
            "        provider.promotions().first().take();\n    }\n}\n"
        ),
    }
    assert [(s[1], s[3], s[4]) for s in _sites(_build(tmp_path, files))] == [
        (4, "com.acme.offers.Promotions", "Promotions.accept"),
        (5, "com.acme.offers.Promotions", "Promotions.first"),
    ]


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
        "a.py", 1, "a.py::f", "npm", "@s/p/sub", "@s/p", "external:@s/p/sub", "x", "y", "named", 2,
        2, 4,
    )  # fmt: skip
    assert ExternalCallSite.from_dict(site.as_dict()) == site
    assert package_root("npm", "@s/p/sub") == "@s/p"
    assert package_root("npm", "lodash/merge") == "lodash"
    assert package_root("pypi", "os.path") == "os"
    assert package_root("maven", "java.util.Map.Entry") == ""


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


def test_a_module_jest_mock_replaces_is_not_the_package(tmp_path: Path) -> None:
    src = (
        "const rimraf = require('rimraf');\nconst _ = require('lodash');\n"
        "jest.mock('rimraf');\n"
        "test('x', () => {\n  rimraf.mockResolvedValue(1);\n  _.merge({});\n});\n"
    )
    sites = _sites(_build(tmp_path, {"__tests__/a.test.js": src}))
    assert [s[3] for s in sites] == ["lodash"]


def test_a_mock_in_one_file_leaves_another_files_calls(tmp_path: Path) -> None:
    files = {
        "a.test.js": "const rimraf = require('rimraf');\njest.mock('rimraf');\nrimraf.sync('x');\n",
        "b.js": "const rimraf = require('rimraf');\nfunction g(x) {\n  rimraf.sync(x);\n}\n",
    }
    assert [s[0] for s in _sites(_build(tmp_path, files))] == ["b.js"]


@pytest.mark.parametrize(
    "mock",
    [
        "vi.mock('axios', () => ({ default: { get: vi.fn() } }));",
        'vi.doMock("axios");',
        "jest.unstable_mockModule('axios', () => ({ default: { get: jest.fn() } }));",
        # A factory that reads a hoisted double still swaps the module.
        "const { get } = vi.hoisted(() => ({ get: vi.fn() }));\n"
        "vi.mock('axios', () => ({ default: { get } }));",
    ],
)
def test_vitest_and_esm_mocks_replace_the_package(tmp_path: Path, mock: str) -> None:
    src = (
        "import { vi, test } from 'vitest';\nimport axios from 'axios';\n"
        f"{mock}\n"
        "test('x', () => {\n  axios.get('/u');\n});\n"
    )
    # ``vi.mock`` and ``test`` are real calls into vitest; ``axios.get`` runs the mock.
    assert {s[3] for s in _sites(_build(tmp_path, {"src/a.test.ts": src}))} == {"vitest"}


@pytest.mark.parametrize(
    "mock",
    [
        "jest.mock('axios', () => ({ ...jest.requireActual('axios'), post: jest.fn() }));",
        "vi.mock('axios', async (importOriginal) => ({ ...(await importOriginal()) }));",
        # A factory passed by name may keep the original; it is not read.
        "const factory = () => ({ ...jest.requireActual('axios') });\njest.mock('axios', factory);",
    ],
)
def test_a_partial_mock_keeps_the_package(tmp_path: Path, mock: str) -> None:
    src = f"const axios = require('axios');\n{mock}\naxios.get('/u');\n"
    assert {s[3] for s in _sites(_build(tmp_path, {"a.test.js": src}))} == {"axios"}


def test_a_full_mock_stays_a_mock_when_the_file_reads_the_original_elsewhere(
    tmp_path: Path,
) -> None:
    src = (
        "const axios = require('axios');\n"
        "jest.mock('axios', () => ({ get: jest.fn() }));\n"
        "const real = jest.requireActual('axios');\n"
        "axios.get('/u');\n"
    )
    assert _sites(_build(tmp_path, {"a.test.js": src})) == []


def test_a_manual_mock_directory_replaces_the_package_in_tests_only(tmp_path: Path) -> None:
    files = {
        "commands/__mocks__/@scope/output.js": "module.exports = jest.fn();\n",
        "commands/__tests__/run.test.js": (
            "const output = require('@scope/output');\ntest('x', () => {\n  output.logged();\n});\n"
        ),
        "commands/run.js": (
            "const output = require('@scope/output');\nfunction run(x) {\n  output(x);\n}\n"
        ),
    }
    assert [s[0] for s in _sites(_build(tmp_path, files))] == ["commands/run.js"]


def test_a_check_that_raises_marks_the_build_incomplete(tmp_path: Path, monkeypatch) -> None:
    from repowise.core.ingestion import call_external

    def boom(self, file_path, call):
        raise IndexError("short source")

    monkeypatch.setattr(call_external.ExternalCallsMixin, "_external_call_site", boom)
    builder = _build(tmp_path, {"a.py": "import requests\n\ndef f(x):\n    requests.get(x)\n"})
    assert builder.external_call_sites == {}
    assert builder.external_calls_complete is False
