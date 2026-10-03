"""Chained member calls: ``h.f1…fn.m()`` typed hop by hop.

The head is typed from the calling body, the enclosing class (``this`` /
``self``) or module scope; each field from the declaring class's own fields.
An edge is emitted only when every hop names exactly one class and the last
one declares the method, so each negative below removes one of those proofs.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
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
