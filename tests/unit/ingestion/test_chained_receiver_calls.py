"""Chained member calls: ``h.f1…fn.m()`` typed hop by hop.

The head is typed from the calling body, the enclosing class (``this`` /
``self``) or module scope; each field from the declaring class's own fields.
An edge is emitted only when every hop names exactly one class and the last
one declares the method, so each negative below removes one of those proofs.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import CallReceiver, FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _parse_all(tmp_path: Path, files: dict[str, tuple[str, str]]) -> dict[str, ParsedFile]:
    out: dict[str, ParsedFile] = {}
    for rel, (lang, content) in files.items():
        abs_ = tmp_path / rel
        abs_.parent.mkdir(parents=True, exist_ok=True)
        abs_.write_text(content)
        info = FileInfo(
            path=rel,
            abs_path=str(abs_),
            language=lang,  # type: ignore[arg-type]
            size_bytes=abs_.stat().st_size,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        out[rel] = parse_file(info, content.encode("utf-8"))
    return out


def _edges(
    tmp_path: Path,
    files: dict[str, tuple[str, str]],
    links: dict[str, dict[str, str]] | None = None,
) -> list[tuple[str, str, float, str]]:
    """Resolve *files*, with *links* standing in for import resolution."""
    parsed = _parse_all(tmp_path, files)
    for path, module_to_file in (links or {}).items():
        for imp in parsed[path].imports:
            target = module_to_file.get(imp.module_path)
            if target is not None:
                imp.resolved_file = target
    resolver = CallResolver(parsed, {p: set() for p in parsed}, repo_path=str(tmp_path))
    return [
        (rc.caller_id, rc.callee_id, rc.confidence, rc.origin)
        for path, pf in parsed.items()
        for rc in resolver.resolve_file(path, pf.calls)
    ]


def _chained(edges: list[tuple[str, str, float, str]]) -> list[tuple[str, str, float, str]]:
    return [e for e in edges if e[3].startswith("receiver_chain_")]


class TestJavaChainedFieldCalls:
    @pytest.mark.parametrize(
        ("field", "parameter", "local", "receiver"),
        [
            ("", "El el", "", "el.attributes"),
            ("", "", "El el = new El();", "el.attributes"),
            ("El el;", "", "", "el.attributes"),
            ("El el;", "", "", "this.el.attributes"),
            ("", "El el", "", "el . attributes"),
        ],
    )
    def test_a_declared_head_and_each_field_type_the_receiver(
        self, tmp_path: Path, field: str, parameter: str, local: str, receiver: str
    ) -> None:
        source = (
            "class Attrs {\n  String get(String k) { return k; }\n}\n"
            "class El {\n  Attrs attributes;\n}\n"
            "class Other {\n  String get(String k) { return k; }\n}\n"
            f"class U {{\n  {field}\n  String u({parameter}) {{\n"
            f'    {local}\n    return {receiver}.get("x");\n  }}\n}}\n'
        )
        parsed = _parse_all(tmp_path, {"A.java": ("java", source)})
        call = next(
            c for c in parsed["A.java"].calls
            if c.caller_symbol_id == "A.java::U::u" and c.target_name == "get"
        )
        assert call.receiver_name == "".join(receiver.split())
        edges = _edges(tmp_path, {"A.java": ("java", source)})
        assert [e for e in edges if e[0] == "A.java::U::u" and e[1].endswith("::get")] == [
            ("A.java::U::u", "A.java::Attrs::get", 0.93, "receiver_chain_same_file")
        ]

    def test_each_hop_reads_the_imports_of_its_declaring_file(self, tmp_path: Path) -> None:
        files = {
            "use/U.java": (
                "java",
                "package use;\nimport model.El;\nclass U {\n"
                '  String u(El el) { return el.attributes.get("x"); }\n}\n',
            ),
            "model/El.java": (
                "java",
                "package model;\nimport attrs.Attrs;\npublic class El {\n"
                "  public Attrs attributes;\n}\n",
            ),
            "attrs/Attrs.java": (
                "java",
                "package attrs;\npublic class Attrs {\n"
                "  public String get(String k) { return k; }\n}\n",
            ),
        }
        links = {
            "use/U.java": {"model.El": "model/El.java"},
            "model/El.java": {"attrs.Attrs": "attrs/Attrs.java"},
        }
        edges = _edges(tmp_path, files, links)
        assert [e for e in edges if e[0] == "use/U.java::U::u"] == [
            ("use/U.java::U::u", "attrs/Attrs.java::Attrs::get", 0.88, "receiver_chain_import")
        ]

    @pytest.mark.parametrize("type_name", ["External", "T"])
    def test_an_unknown_or_type_parameter_hop_does_not_bind_by_name(
        self, tmp_path: Path, type_name: str
    ) -> None:
        source = (
            "class Other {\n  String get(String k) { return k; }\n}\n"
            f"class El<T> {{\n  {type_name} attributes;\n}}\n"
            "class U {\n"
            '  String u(El el) { return el.attributes.get("x"); }\n}\n'
        )
        edges = _edges(tmp_path, {"A.java": ("java", source)})
        assert not [e for e in edges if e[0] == "A.java::U::u"]

    def test_a_local_does_not_use_the_shadowed_field_type(self, tmp_path: Path) -> None:
        source = (
            "class Attrs {\n  String get(String k) { return k; }\n}\n"
            "class El {\n  Attrs attributes;\n}\n"
            "class U {\n  El el;\n  String u(External el) {\n"
            '    return el.attributes.get("x");\n  }\n}\n'
        )
        assert not _edges(tmp_path, {"A.java": ("java", source)})

    @pytest.mark.parametrize(
        "owner",
        [
            "class U<T> {\n  String u(T el) { return el.attributes.get(\"x\"); }\n}\n",
            "class El<T> {\n  T attributes;\n}\n"
            "class U {\n  String u(El el) { return el.attributes.attributes.get(\"x\"); }\n}\n",
        ],
        ids=["head", "field"],
    )
    def test_a_type_parameter_does_not_mean_a_same_named_class(
        self, tmp_path: Path, owner: str
    ) -> None:
        source = (
            "class Attrs {\n  String get(String k) { return k; }\n}\n"
            "class T {\n  Attrs attributes;\n}\n" + owner
        )
        assert not _edges(tmp_path, {"A.java": ("java", source)})

    @pytest.mark.parametrize(
        "receiver", ["make().el.attributes", "els[0].attributes", "((El) obj).attributes"]
    )
    def test_non_path_receivers_stay_bare(self, tmp_path: Path, receiver: str) -> None:
        source = f'class U {{\n  String u() {{ return {receiver}.get("x"); }}\n}}\n'
        parsed = _parse_all(tmp_path, {"A.java": ("java", source)})
        calls = [c for c in parsed["A.java"].calls if c.target_name == "get"]
        assert len(calls) == 1
        assert calls[0].receiver_name is None
        assert calls[0].receiver_call is None

    def test_single_own_field_and_member_type_receivers_keep_their_shape(self, tmp_path: Path) -> None:
        source = (
            "class Attrs {\n  String get(String k) { return k; }\n}\n"
            "class Outer {\n  static class Inner {\n"
            "    static String get(String k) { return k; }\n  }\n}\n"
            "class U {\n  Attrs attributes;\n  String u() {\n"
            '    this.attributes.get("x");\n    return Outer.Inner.get("x");\n  }\n}\n'
        )
        parsed = _parse_all(tmp_path, {"A.java": ("java", source)})
        assert [c.receiver_name for c in parsed["A.java"].calls] == ["attributes", "Outer.Inner"]
        edges = _edges(tmp_path, {"A.java": ("java", source)})
        assert {e[1] for e in edges} == {"A.java::Attrs::get", "A.java::Inner::get"}
        assert not _chained(edges)


# --- ts_chained_field_calls --------------------------------------------------

_TS_USERS = (
    "typescript",
    "export class Users {\n  get(slug: string): number { return 1; }\n}\n",
)


def _ts_chained_field_calls(app: str) -> dict[str, tuple[str, str]]:
    return {
        "src/users.ts": _TS_USERS,
        "src/store.ts": (
            "typescript",
            "import { Users } from './users';\n"
            "export class Store {\n"
            "  users: Users;\n"
            "  constructor() { this.users = new Users(); }\n"
            "}\n",
        ),
        "src/app.ts": ("typescript", app),
    }


_TS_LINKS = {
    "src/store.ts": {"./users": "src/users.ts"},
    "src/app.ts": {"./store": "src/store.ts"},
}


class TestTsChainedFieldCalls:
    def test_a_parameter_property_and_a_field_type_the_chain(self, tmp_path: Path) -> None:
        app = (
            "import { Store } from './store';\n"
            "export class App {\n"
            "  constructor(private readonly store: Store) {}\n"
            "  run() { return this.store.users.get('x'); }\n"
            "}\n"
        )
        edges = _edges(tmp_path, _ts_chained_field_calls(app), _TS_LINKS)
        assert (
            "src/app.ts::App::run",
            "src/users.ts::Users::get",
            0.88,
            "receiver_chain_import",
        ) in edges

    def test_a_typed_parameter_heads_the_chain(self, tmp_path: Path) -> None:
        app = (
            "import { Store } from './store';\n"
            "export function run(store: Store) {\n"
            "  return store.users.get('x');\n"
            "}\n"
        )
        edges = _edges(tmp_path, _ts_chained_field_calls(app), _TS_LINKS)
        assert ("src/app.ts::run", "src/users.ts::Users::get") in {e[:2] for e in edges}

    def test_a_same_file_chain_is_same_file(self, tmp_path: Path) -> None:
        source = (
            "class Engine { start(): void {} }\n"
            "class Car { engine = new Engine(); }\n"
            "class Garage {\n"
            "  car: Car;\n"
            "  open() { this.car.engine.start(); }\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"garage.ts": ("typescript", source)})
        assert (
            "garage.ts::Garage::open",
            "garage.ts::Engine::start",
            0.93,
            "receiver_chain_same_file",
        ) in edges

    def test_an_untyped_field_yields_no_edge(self, tmp_path: Path) -> None:
        source = (
            "class Engine { start(): void {} }\n"
            "class Car { engine = makeEngine(); }\n"
            "class Garage {\n"
            "  car: Car;\n"
            "  open() { this.car.engine.start(); }\n"
            "}\n"
        )
        assert not _chained(_edges(tmp_path, {"garage.ts": ("typescript", source)}))

    def test_a_union_field_yields_no_edge(self, tmp_path: Path) -> None:
        source = (
            "class Engine { start(): void {} }\n"
            "class Car { engine: Engine | null; }\n"
            "class Garage {\n"
            "  car: Car;\n"
            "  open() { this.car.engine.start(); }\n"
            "}\n"
        )
        assert not _chained(_edges(tmp_path, {"garage.ts": ("typescript", source)}))

    def test_self_is_not_this(self, tmp_path: Path) -> None:
        source = (
            "class Engine {\n  start(): void {}\n}\n"
            "class Car {\n  engine: Engine;\n}\n"
            "class Garage {\n  car: Car;\n"
            "  open() {\n    self.car.engine.start();\n  }\n}\n"
        )
        assert not _chained(_edges(tmp_path, {"garage.ts": ("typescript", source)}))

    def test_a_type_parameter_field_yields_no_edge(self, tmp_path: Path) -> None:
        source = (
            "class Engine {\n  start(): void {}\n}\n"
            "class Car<T> {\n  engine: T;\n}\n"
            "class Garage {\n  car: Car<Engine>;\n"
            "  open() {\n    this.car.engine.start();\n  }\n}\n"
        )
        assert not _chained(_edges(tmp_path, {"garage.ts": ("typescript", source)}))

    def test_a_method_missing_on_the_final_type_yields_no_edge(self, tmp_path: Path) -> None:
        source = (
            "class Engine { start(): void {} }\n"
            "class Wheel { start(): void {} }\n"
            "class Car { engine: Engine; }\n"
            "class Garage {\n"
            "  car: Car;\n"
            "  open() { this.car.engine.stop(); }\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"garage.ts": ("typescript", source)})
        assert not [e for e in edges if e[0] == "garage.ts::Garage::open"]

    def test_a_chain_deeper_than_three_fields_yields_no_edge(self, tmp_path: Path) -> None:
        source = (
            "class Leaf { go(): void {} }\n"
            "class Twig { leaf: Leaf; }\n"
            "class Branch { twig: Twig; }\n"
            "class Bough { branch: Branch; }\n"
            "class Tree {\n"
            "  bough: Bough;\n"
            "  run() { this.bough.branch.twig.leaf.go(); }\n"
            "}\n"
        )
        assert not _chained(_edges(tmp_path, {"deep.ts": ("typescript", source)}))

    def test_three_fields_are_walked(self, tmp_path: Path) -> None:
        source = (
            "class Leaf { go(): void {} }\n"
            "class Twig { leaf: Leaf; }\n"
            "class Branch { twig: Twig; }\n"
            "class Tree {\n"
            "  branch: Branch;\n"
            "  run() { this.branch.twig.leaf.go(); }\n"
            "}\n"
        )
        edges = _chained(_edges(tmp_path, {"deep.ts": ("typescript", source)}))
        assert [e[:2] for e in edges] == [("deep.ts::Tree::run", "deep.ts::Leaf::go")]


# --- ts_module_scope_receiver ------------------------------------------------


class TestTsModuleScopeReceiver:
    def _files(self, app: str) -> dict[str, tuple[str, str]]:
        return _ts_chained_field_calls(app)

    def test_a_module_scope_instance_heads_the_chain(self, tmp_path: Path) -> None:
        app = (
            "import { Store } from './store';\n"
            "const store = new Store();\n"
            "export async function main() {\n"
            "  return store.users.get('x');\n"
            "}\n"
        )
        edges = _edges(tmp_path, self._files(app), _TS_LINKS)
        assert (
            "src/app.ts::main",
            "src/users.ts::Users::get",
            0.88,
            "receiver_chain_import",
        ) in edges

    def test_a_call_at_module_scope_reads_module_scope(self, tmp_path: Path) -> None:
        app = (
            "import { Store } from './store';\n"
            "const store: Store = make();\n"
            "store.users.get('x');\n"
        )
        edges = _edges(tmp_path, self._files(app), _TS_LINKS)
        assert ("src/app.ts::__module__", "src/users.ts::Users::get") in {e[:2] for e in edges}

    def test_a_shadowing_local_yields_no_edge(self, tmp_path: Path) -> None:
        app = (
            "import { Store } from './store';\n"
            "const store = new Store();\n"
            "export function main() {\n"
            "  const store = other();\n"
            "  return store.users.get('x');\n"
            "}\n"
        )
        assert not _chained(_edges(tmp_path, self._files(app), _TS_LINKS))

    def test_a_shadowing_parameter_yields_no_edge(self, tmp_path: Path) -> None:
        app = (
            "import { Store } from './store';\n"
            "const store = new Store();\n"
            "export function main(store) {\n"
            "  return store.users.get('x');\n"
            "}\n"
        )
        assert not _chained(_edges(tmp_path, self._files(app), _TS_LINKS))

    def test_a_module_name_rebound_untyped_yields_no_edge(self, tmp_path: Path) -> None:
        app = (
            "import { Store } from './store';\n"
            "let store = new Store();\n"
            "store = other();\n"
            "export function main() {\n"
            "  return store.users.get('x');\n"
            "}\n"
        )
        assert not _chained(_edges(tmp_path, self._files(app), _TS_LINKS))

    def test_an_object_literal_key_declares_nothing(self, tmp_path: Path) -> None:
        app = (
            "import { Store } from './store';\n"
            "export function main() {\n"
            "  const opts = { store: Store, };\n"
            "  return store.users.get('x');\n"
            "}\n"
        )
        assert not _chained(_edges(tmp_path, self._files(app), _TS_LINKS))


# --- py_chained_field_calls --------------------------------------------------


def _py_chained_field_calls(app: str) -> dict[str, tuple[str, str]]:
    return {
        "pkg/users.py": (
            "python",
            "class Users:\n    def get(self, slug):\n        return slug\n",
        ),
        "pkg/store.py": (
            "python",
            "from pkg.users import Users\n\n"
            "class Store:\n"
            "    def __init__(self):\n"
            "        self.users = Users()\n",
        ),
        "pkg/app.py": ("python", app),
    }


_PY_LINKS = {
    "pkg/store.py": {"pkg.users": "pkg/users.py"},
    "pkg/app.py": {"pkg.store": "pkg/store.py"},
}


class TestPyChainedFieldCalls:
    def test_self_fields_type_the_chain(self, tmp_path: Path) -> None:
        app = (
            "from pkg.store import Store\n\n"
            "class App:\n"
            "    def __init__(self):\n"
            "        self.store = Store()\n\n"
            "    def run(self):\n"
            "        return self.store.users.get('x')\n"
        )
        edges = _edges(tmp_path, _py_chained_field_calls(app), _PY_LINKS)
        assert (
            "pkg/app.py::App::run",
            "pkg/users.py::Users::get",
            0.88,
            "receiver_chain_import",
        ) in edges

    def test_a_class_level_annotation_is_a_field(self, tmp_path: Path) -> None:
        app = (
            "from pkg.store import Store\n\n"
            "class App:\n"
            "    store: Store\n\n"
            "    def run(self):\n"
            "        return self.store.users.get('x')\n"
        )
        edges = _edges(tmp_path, _py_chained_field_calls(app), _PY_LINKS)
        assert ("pkg/app.py::App::run", "pkg/users.py::Users::get") in {e[:2] for e in edges}

    def test_a_module_scope_instance_heads_the_chain(self, tmp_path: Path) -> None:
        app = (
            "from pkg.store import Store\n\n"
            "store = Store()\n\n"
            "def main():\n"
            "    return store.users.get('x')\n"
        )
        edges = _edges(tmp_path, _py_chained_field_calls(app), _PY_LINKS)
        assert ("pkg/app.py::main", "pkg/users.py::Users::get") in {e[:2] for e in edges}

    def test_a_shadowing_parameter_yields_no_edge(self, tmp_path: Path) -> None:
        app = (
            "from pkg.store import Store\n\n"
            "store = Store()\n\n"
            "def main(verbose, store):\n"
            "    return store.users.get('x')\n"
        )
        assert not _chained(_edges(tmp_path, _py_chained_field_calls(app), _PY_LINKS))

    def test_an_untyped_field_yields_no_edge(self, tmp_path: Path) -> None:
        app = (
            "class App:\n"
            "    def __init__(self, store):\n"
            "        self.store = store\n\n"
            "    def run(self):\n"
            "        return self.store.users.get('x')\n"
        )
        assert not _chained(_edges(tmp_path, _py_chained_field_calls(app), _PY_LINKS))

    def test_an_optional_field_yields_no_edge(self, tmp_path: Path) -> None:
        app = (
            "from typing import Optional\n"
            "from pkg.store import Store\n\n"
            "class App:\n"
            "    def __init__(self):\n"
            "        self.store: Optional[Store] = None\n\n"
            "    def run(self):\n"
            "        return self.store.users.get('x')\n"
        )
        assert not _chained(_edges(tmp_path, _py_chained_field_calls(app), _PY_LINKS))

    def test_a_field_typed_twice_yields_no_edge(self, tmp_path: Path) -> None:
        app = (
            "from pkg.store import Store\n"
            "from pkg.users import Users\n\n"
            "class App:\n"
            "    def __init__(self):\n"
            "        self.store = Store()\n\n"
            "    def reset(self):\n"
            "        self.store = Users()\n\n"
            "    def run(self):\n"
            "        return self.store.users.get('x')\n"
        )
        links = {
            **_PY_LINKS,
            "pkg/app.py": {"pkg.store": "pkg/store.py", "pkg.users": "pkg/users.py"},
        }
        assert not _chained(_edges(tmp_path, _py_chained_field_calls(app), links))

    def test_a_method_missing_on_the_final_type_yields_no_edge(self, tmp_path: Path) -> None:
        app = (
            "from pkg.store import Store\n\n"
            "class App:\n"
            "    def __init__(self):\n"
            "        self.store = Store()\n\n"
            "    def run(self):\n"
            "        return self.store.users.post('x')\n"
        )
        assert not _chained(_edges(tmp_path, _py_chained_field_calls(app), _PY_LINKS))

    def test_a_non_path_receiver_mints_no_site(self, tmp_path: Path) -> None:
        """``a[0].b.m()`` and ``f().b.m()`` stay off every tier, bare ones included."""
        parsed = _parse_all(
            tmp_path,
            {
                "m.py": (
                    "python",
                    "def run(items):\n    items[0].users.get(1)\n    make().users.get(2)\n",
                )
            },
        )
        assert [(c.receiver_name, c.target_name) for c in parsed["m.py"].calls] == [(None, "make")]


# --- py_property_and_returned_receivers ---------------------------------------


def _py_returned(store: str, app: str) -> dict[str, tuple[str, str]]:
    return {
        "pkg/users.py": (
            "python",
            "class Users:\n    def get(self, slug):\n        return slug\n",
        ),
        "pkg/store.py": ("python", "from pkg.users import Users\n\n" + store),
        "pkg/app.py": ("python", "from pkg.store import Store\n\n" + app),
    }


_PY_GET = ("pkg/app.py::run", "pkg/users.py::Users::get")


def _typed(edges: list[tuple[str, str, float, str]]) -> set[tuple[str, str, str]]:
    """Edges into ``Users.get`` a receiver type proved, bare-name guesses left out."""
    prefixes = ("receiver_chain_", "receiver_typed_", "return_type_")
    return {
        (e[0], e[1], e[3]) for e in edges if e[1] == _PY_GET[1] and e[3].startswith(prefixes)
    }


class TestPyPropertyAndReturnedReceivers:
    def test_a_property_getter_annotation_types_the_hop(self, tmp_path: Path) -> None:
        store = (
            "class Store:\n"
            "    @property\n"
            "    def users(self) -> Users:\n"
            "        return Users()\n\n"
            "    @users.setter\n"
            "    def users(self, value):\n"
            "        pass\n"
        )
        app = "def run(store: Store):\n    return store.users.get('x')\n"
        edges = _edges(tmp_path, _py_returned(store, app), _PY_LINKS)
        assert (*_PY_GET, "receiver_chain_import") in _typed(edges)

    def test_a_cached_property_types_the_hop(self, tmp_path: Path) -> None:
        store = (
            "import functools\n\n"
            "class Store:\n"
            "    @functools.cached_property\n"
            "    def users(self) -> 'Users':\n"
            "        return Users()\n"
        )
        app = "def run(store: Store):\n    return store.users.get('x')\n"
        edges = _edges(tmp_path, _py_returned(store, app), _PY_LINKS)
        assert (*_PY_GET, "receiver_chain_import") in _typed(edges)

    def test_an_unannotated_or_optional_property_types_nothing(self, tmp_path: Path) -> None:
        for annotation in ("", " -> Optional[Users]", " -> Users | None"):
            store = (
                "class Store:\n"
                "    @property\n"
                f"    def users(self){annotation}:\n"
                "        return Users()\n"
            )
            app = "def run(store: Store):\n    return store.users.get('x')\n"
            assert not _typed(_edges(tmp_path, _py_returned(store, app), _PY_LINKS))

    def test_a_plain_method_is_not_a_property(self, tmp_path: Path) -> None:
        store = "class Store:\n    def users(self) -> Users:\n        return Users()\n"
        app = "def run(store: Store):\n    return store.users.get('x')\n"
        assert not _typed(_edges(tmp_path, _py_returned(store, app), _PY_LINKS))

    def test_an_assigned_call_types_the_local(self, tmp_path: Path) -> None:
        store = "class Store:\n    def lookup(self) -> Users:\n        return Users()\n"
        for value in ("store.lookup()", "await store.lookup()"):
            app = f"async def run(store: Store):\n    u = {value}\n    return u.get('x')\n"
            edges = _edges(tmp_path, _py_returned(store, app), _PY_LINKS)
            assert (*_PY_GET, "receiver_typed_import") in _typed(edges)

    def test_a_chained_call_types_the_outer_receiver(self, tmp_path: Path) -> None:
        store = "class Store:\n    def lookup(self) -> Users:\n        return Users()\n"
        app = "def run(store: Store):\n    return store.lookup().get('x')\n"
        edges = _edges(tmp_path, _py_returned(store, app), _PY_LINKS)
        assert (*_PY_GET, "return_type_import") in _typed(edges)

    def test_an_unannotated_return_types_nothing(self, tmp_path: Path) -> None:
        store = "class Store:\n    def lookup(self):\n        return Users()\n"
        app = (
            "def run(store: Store):\n"
            "    u = store.lookup()\n"
            "    u.get('x')\n"
            "    return store.lookup().get('y')\n"
        )
        assert not _typed(_edges(tmp_path, _py_returned(store, app), _PY_LINKS))

    def test_a_rebound_local_types_nothing(self, tmp_path: Path) -> None:
        store = (
            "class Store:\n"
            "    def lookup(self) -> Users:\n"
            "        return Users()\n\n"
            "    def other(self):\n"
            "        return None\n"
        )
        for rebind in ("u = None", "u = store.other()", "for u in []: pass"):
            app = (
                "def run(store: Store):\n"
                "    u = store.lookup()\n"
                f"    {rebind}\n"
                "    return u.get('x')\n"
            )
            assert not _typed(_edges(tmp_path, _py_returned(store, app), _PY_LINKS))
        same_line = "def run(store: Store):\n    u = store.lookup(); u = 3\n    return u.get('x')\n"
        assert not _typed(_edges(tmp_path, _py_returned(store, same_line), _PY_LINKS))

    def test_an_async_def_types_only_an_awaited_call(self, tmp_path: Path) -> None:
        store = "class Store:\n    async def lookup(self) -> Users:\n        return Users()\n"
        awaited = "async def run(store: Store):\n    u = await store.lookup()\n    u.get('x')\n"
        edges = _edges(tmp_path, _py_returned(store, awaited), _PY_LINKS)
        assert (*_PY_GET, "receiver_typed_import") in _typed(edges)
        for app in (
            "async def run(store: Store):\n    u = store.lookup()\n    u.get('x')\n",
            "async def run(store: Store):\n    store.lookup().get('x')\n",
        ):
            assert not _typed(_edges(tmp_path, _py_returned(store, app), _PY_LINKS))

    def test_a_property_called_as_a_method_types_nothing(self, tmp_path: Path) -> None:
        store = (
            "class Store:\n    @property\n    def users(self) -> Users:\n        return Users()\n"
        )
        for app in (
            "def run(store: Store):\n    return store.users().get('x')\n",
            "def run(store: Store):\n    u = store.users()\n    return u.get('x')\n",
        ):
            assert not _typed(_edges(tmp_path, _py_returned(store, app), _PY_LINKS))

    def test_an_inner_call_keeps_only_a_dotted_receiver(self, tmp_path: Path) -> None:
        source = "def run(items):\n    self.client.get(1).close()\n    items[0].get(2).close()\n"
        parsed = _parse_all(tmp_path, {"m.py": ("python", source)})
        receivers = {c.line: c.receiver_call for c in parsed["m.py"].calls if c.target_name == "close"}
        assert receivers[2] == CallReceiver("get", "self.client", 1)
        assert receivers[3] is None


# --- go_chained_field_calls --------------------------------------------------

_GO_COMMAND = (
    "package cmd\n\n"
    "type GitCmd struct{}\n\n"
    "func (g *GitCmd) DiffFilesCh() {}\n\n"
    "type Base struct{}\n\n"
    "func (b *Base) Close() {}\n\n"
    "type Command struct {\n"
    "\t*Base\n"
    "\tCmd      *GitCmd `json:\"cmd\"`\n"
    "\tcommands []*GitCmd\n"
    "}\n\n"
)


class TestGoChainedFieldCalls:
    """Go names its receiver, so the head is a parameter; each hop a struct field."""

    def _edges(self, tmp_path: Path, body: str) -> list[tuple[str, str, float, str]]:
        return _chained(_edges(tmp_path, {"cmd/command.go": ("go", _GO_COMMAND + body)}))

    def test_a_field_types_the_chain(self, tmp_path: Path) -> None:
        body = "func (c *Command) Run() {\n\tc.Cmd.DiffFilesCh()\n}\n"
        assert self._edges(tmp_path, body) == [
            (
                "cmd/command.go::Command::Run",
                "cmd/command.go::GitCmd::DiffFilesCh",
                0.93,
                "receiver_chain_same_file",
            )
        ]

    def test_an_embedded_field_is_read_by_its_type_name(self, tmp_path: Path) -> None:
        body = "func (c *Command) Run() {\n\tc.Base.Close()\n}\n"
        assert [e[:2] for e in self._edges(tmp_path, body)] == [
            ("cmd/command.go::Command::Run", "cmd/command.go::Base::Close")
        ]

    def test_a_slice_field_yields_no_edge(self, tmp_path: Path) -> None:
        body = "func (c *Command) Run() {\n\tc.commands.DiffFilesCh()\n}\n"
        assert self._edges(tmp_path, body) == []

    def test_a_method_missing_on_the_field_type_yields_no_edge(self, tmp_path: Path) -> None:
        body = "func (c *Command) Run() {\n\tc.Cmd.Close()\n}\n"
        assert self._edges(tmp_path, body) == []

    def test_a_package_qualified_head_yields_no_edge(self, tmp_path: Path) -> None:
        """``pkg.Var.M()`` has the same shape as ``c.field.M()``, but ``pkg`` types nothing."""
        body = "func run() {\n\tconfig.Cmd.DiffFilesCh()\n}\n"
        assert self._edges(tmp_path, body) == []

    def test_a_package_qualified_field_type_is_not_the_local_type(self, tmp_path: Path) -> None:
        """``fd *sftp.File`` wraps a foreign ``File``; its ``Write`` is not this file's."""
        source = (
            "package sftpfs\n\n"
            "type File struct {\n\tfd *sftp.File\n}\n\n"
            "func (f *File) Write(b []byte) {}\n\n"
            "func (f *File) WriteString(s string) {\n\tf.fd.Write([]byte(s))\n}\n"
        )
        assert _chained(_edges(tmp_path, {"sftpfs/file.go": ("go", source)})) == []
