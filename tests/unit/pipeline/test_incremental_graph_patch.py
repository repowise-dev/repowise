"""A patched graph must equal a full rebuild of the same tree.

``build_repo_graph(seed_edges=...)`` reuses the persisted import, call and
heritage edges and re-resolves only the files that changed, but only when the
edit provably left every changed file's cross-file surface alone. These tests
build a Java tree, persist its edges the way the index does, edit it, and
compare the patched graph with a from-scratch build of the edited tree: nodes
with their attributes, and every edge with the fields the index stores. Edits
that change a file's surface, add a file or touch a language the patcher does
not cover must fall back to the full build.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from repowise.core.ingestion import GraphBuilder
from repowise.core.pipeline.incremental import build_repo_graph
from repowise.core.pipeline.phase_timing import PhaseTimings

_FILES = {
    "src/com/acme/util/Util.java": """package com.acme.util;

public class Util {
    public static String id() { return "id"; }
    public static int twice(int x) { return x * 2; }
}
""",
    "src/com/acme/util/Clock.java": """package com.acme.util;

public class Clock {
    public static long now() { return 1L; }
}
""",
    "src/com/acme/svc/Base.java": """package com.acme.svc;

public class Base {
    public String name() { return "base"; }
}
""",
    "src/com/acme/svc/Service.java": """package com.acme.svc;

import com.acme.util.Util;

public class Service extends Base {
    public String run() {
        return Util.id() + name();
    }
}
""",
    # Two modules declare ``BuySell``. ``Order`` imports the one in ``a_mod`` while
    # its own package holds the other, so the JVM type-reference pass only picks
    # the right target if it still sees the resolved import of this unchanged file.
    "a_mod/src/com/x/BuySell.java": "package com.x;\npublic class BuySell {}\n",
    "z_mod/src/com/y/BuySell.java": "package com.y;\npublic class BuySell {}\n",
    "z_mod/src/com/y/Order.java": """package com.y;

import com.x.BuySell;

public class Order {
    private BuySell side;
}
""",
    # A field initializer calls the class's own method: the call resolves onto
    # the same class -> method pair as the structural ``has_method`` link.
    "src/com/acme/svc/Holder.java": """package com.acme.svc;

public class Holder {
    private final String value = compute();
    String compute() { return "x"; }
}
""",
    "src/com/acme/svc/Derived.java": """package com.acme.svc;

public class Derived extends Base {
    @Override
    public String name() { return "derived"; }
}
""",
    "src/com/acme/svc/Other.java": """package com.acme.svc;

import com.acme.util.Util;

public class Other {
    private final Service service = new Service();
    public String go() {
        return service.run() + Util.twice(2);
    }
}
""",
}
_SERVICE = "src/com/acme/svc/Service.java"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _seed_rows(graph_builder: Any) -> dict[str, list]:
    """Full node/edge snapshot as written by the sealed seed cache."""
    from repowise.core.pipeline.incremental.resolution_cache import save_resolutions

    index_dir = Path(graph_builder._repo_path) / ".repowise"
    index_dir.mkdir(exist_ok=True)
    save_resolutions(index_dir, graph_builder._parsed_files.values())
    graph = graph_builder.graph()
    return {
        "nodes": [(node, dict(attrs)) for node, attrs in graph.nodes(data=True)],
        "edges": [(source, target, dict(attrs)) for source, target, attrs in graph.edges(data=True)],
    }

def _edges(graph_builder: Any) -> set[tuple]:
    return {
        (
            u,
            v,
            d.get("edge_type", "imports"),
            d.get("confidence", 1.0),
            d.get("hint_source"),
            d.get("resolution_origin"),
            tuple(sorted(d.get("imported_names", []))),
            tuple(d.get("call_lines", [])),
            bool(d.get("type_only", False)),
            bool(d.get("deferred", False)),
        )
        for u, v, d in graph_builder.graph().edges(data=True)
    }


def _nodes(graph_builder: Any) -> dict[str, dict]:
    return {n: dict(d) for n, d in graph_builder.graph().nodes(data=True)}


def _full(tree: Path, dest: Path) -> Any:
    """A from-scratch build of *tree*'s current contents, with its own empty cache."""
    shutil.copytree(tree, dest, ignore=shutil.ignore_patterns(".repowise"))
    return build_repo_graph(dest, [], collect_sources=True)[2]


@pytest.fixture
def indexed(tmp_path: Path) -> tuple[Path, dict[str, list]]:
    """A Java tree that has been built once, plus its persisted edges."""
    tree = tmp_path / "repo"
    _write(tree, _FILES)
    first = build_repo_graph(tree, [], collect_sources=True)[2]
    return tree, _seed_rows(first)


def _patched(tree: Path, seed: dict[str, list]) -> Any:
    graph_builder = build_repo_graph(
        tree, [], collect_sources=True, seed_edges=seed, patch_requested=True
    )[2]
    return graph_builder


def _assert_equals_full(tree: Path, patched: Any, tmp_path: Path) -> None:
    full = _full(tree, tmp_path / "full")
    assert _nodes(patched) == _nodes(full)
    assert _edges(patched) == _edges(full)


def test_body_edit_is_patched_and_equals_a_full_rebuild(
    indexed: tuple[Path, dict[str, list]], tmp_path: Path
) -> None:
    tree, seed = indexed
    (tree / _SERVICE).write_text(
        _FILES[_SERVICE].replace(
            "return Util.id() + name();",
            "Holder holder = new Holder();\n        return Util.id() + Util.twice(1) + name() + holder.compute();",
        )
    )

    patched = _patched(tree, seed)

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert patched.patch_plan.scope == (_SERVICE,)
    _assert_equals_full(tree, patched, tmp_path)


def test_phase_timings_identify_the_patched_graph_path(
    indexed: tuple[Path, dict[str, list]]
) -> None:
    tree, seed = indexed
    (tree / _SERVICE).write_text(
        _FILES[_SERVICE].replace(
            "return Util.id() + name();", "return Util.id() + Util.twice(1) + name();"
        )
    )
    timings = PhaseTimings()

    graph_builder = build_repo_graph(
        tree,
        [],
        collect_sources=True,
        seed_edges=seed,
        patch_requested=True,
        changed_paths={_SERVICE},
        timings=timings,
    )[2]

    assert graph_builder.patch_plan.eligible
    assert "rebuild.graph.patch" in timings.totals
    assert "rebuild.graph.full" not in timings.totals
    assert "rebuild.graph" in timings.totals


def test_phase_timings_identify_full_rebuild_after_surface_change(
    indexed: tuple[Path, dict[str, list]]
) -> None:
    tree, seed = indexed
    (tree / _SERVICE).write_text(
        _FILES[_SERVICE].replace("public String run()", "public int run(int value)")
    )
    timings = PhaseTimings()
    messages: list[str] = []

    graph_builder = build_repo_graph(
        tree,
        [],
        collect_sources=True,
        seed_edges=seed,
        patch_requested=True,
        changed_paths={_SERVICE},
        timings=timings,
        log=messages.append,
    )[2]

    assert not graph_builder.patch_plan.eligible
    assert "dependency surface" in (graph_builder.patch_plan.reason or "")
    assert any("Full graph rebuild" in message and "dependency surface" in message for message in messages)
    assert "rebuild.graph.full" in timings.totals
    assert "rebuild.graph.patch" not in timings.totals
    assert "rebuild.graph" in timings.totals


def test_phase_timings_identify_full_path_when_patching_is_not_requested(
    indexed: tuple[Path, dict[str, list]]
) -> None:
    tree, _seed = indexed
    timings = PhaseTimings()

    graph_builder = build_repo_graph(tree, [], collect_sources=True, timings=timings)[2]

    assert not graph_builder.patch_plan.eligible
    assert graph_builder.patch_plan.reason == "feature disabled"
    assert "rebuild.graph.full" in timings.totals
    assert "rebuild.graph.patch" not in timings.totals


def test_removing_an_import_and_call_falls_back_to_full_build(
    indexed: tuple[Path, dict[str, list]], tmp_path: Path
) -> None:
    tree, seed = indexed
    (tree / _SERVICE).write_text(
        _FILES[_SERVICE]
        .replace("import com.acme.util.Util;\n", "")
        .replace("return Util.id() + name();", 'return "x" + name();')
    )

    patched = _patched(tree, seed)

    assert not patched.patch_plan.eligible
    assert "dependency surface" in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path)

def test_unchanged_tree_patches_to_the_same_graph(
    indexed: tuple[Path, dict[str, list]], tmp_path: Path
) -> None:
    tree, seed = indexed

    patched = _patched(tree, seed)

    assert patched.patch_plan.eligible
    assert patched.patch_plan.scope == ()
    _assert_equals_full(tree, patched, tmp_path)


def test_detected_unparsed_config_change_forces_full_rebuild(
    indexed: tuple[Path, dict[str, list]], tmp_path: Path
) -> None:
    tree, seed = indexed
    (tree / _SERVICE).write_text(
        _FILES[_SERVICE].replace(
            "return Util.id() + name();", "return Util.id() + Util.twice(1) + name();"
        )
    )

    patched = build_repo_graph(
        tree,
        [],
        collect_sources=True,
        seed_edges=seed,
        patch_requested=True,
        changed_paths={_SERVICE, "pom.xml"},
    )[2]

    assert not patched.patch_plan.eligible
    assert "unparsed files" in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path)


def test_resolution_cache_is_written_only_when_requested(tmp_path: Path) -> None:
    tree = tmp_path / "repo"
    _write(tree, {"main.java": "class Main {}\n"})
    cache = tree / ".repowise" / "resolved_imports.pkl"

    build_repo_graph(tree, [], collect_sources=True)
    assert not cache.exists()

    build_repo_graph(tree, [], collect_sources=True, cache_resolutions=True)
    assert cache.is_file()


@pytest.mark.parametrize(
    ("edit", "reason"),
    [
        pytest.param(
            lambda t: (t / _SERVICE).write_text(
                _FILES[_SERVICE].replace("public String run()", "public int run(int a)")
                .replace("return Util.id() + name();", "return a;")
            ),
            "dependency surface",
            id="signature-change",
        ),
        pytest.param(
            lambda t: (t / _SERVICE).write_text(
                _FILES[_SERVICE].replace(" extends Base", "")
            ),
            "dependency surface",
            id="heritage-change",
        ),
        pytest.param(
            lambda t: _write(
                t,
                {
                    "src/com/acme/svc/Extra.java": (
                        "package com.acme.svc;\npublic class Extra {}\n"
                    )
                },
            ),
            "new file",
            id="added-file",
        ),
        pytest.param(
            lambda t: (t / "src/com/acme/util/Clock.java").unlink(),
            "file removed",
            id="removed-file",
        ),
    ],
)
def test_surface_changes_fall_back_to_a_full_rebuild(
    indexed: tuple[Path, dict[str, list]], tmp_path: Path, edit: Any, reason: str
) -> None:
    tree, seed = indexed
    edit(tree)

    patched = _patched(tree, seed)

    assert not patched.patch_plan.eligible
    assert reason in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path)


def test_unsupported_language_falls_back(tmp_path: Path) -> None:
    tree = tmp_path / "repo"
    helper = "#!/bin/sh\nf() { echo one; }\n"
    _write(tree, {**_FILES, "tools/helper.sh": helper})
    first = build_repo_graph(tree, [], collect_sources=True)[2]
    seed = _seed_rows(first)
    (tree / "tools/helper.sh").write_text(helper.replace("echo one", "echo two"))

    patched = _patched(tree, seed)

    assert not patched.patch_plan.eligible
    assert "shell edits are not patched" in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path)


def test_module_level_call_edit_matches_full_build(tmp_path: Path) -> None:
    tree = tmp_path / "module-calls"
    files = {
        "util.py": "def first():\n    return 1\n\ndef second():\n    return 2\n",
        "service.py": "from util import first, second\nfirst()\n",
    }
    _write(tree, files)
    original = build_repo_graph(tree, [], collect_sources=True)[2]
    original_module_calls = {
        (source, target)
        for source, target, attrs in original.graph().edges(data=True)
        if source == "service.py::__module__" and attrs.get("edge_type") == "calls"
    }
    assert original_module_calls == {("service.py::__module__", "util.py::first")}

    (tree / "service.py").write_text(files["service.py"].replace("\nfirst()", "\nsecond()"))

    patched = _patched(tree, _seed_rows(original))

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    patched_module_calls = {
        (source, target)
        for source, target, attrs in patched.graph().edges(data=True)
        if source == "service.py::__module__" and attrs.get("edge_type") == "calls"
    }
    assert patched_module_calls == {("service.py::__module__", "util.py::second")}
    _assert_equals_full(tree, patched, tmp_path / "module-calls-oracle")


def test_go_package_clause_change_falls_back(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "repo"
    _write(
        tree,
        {
            "go.mod": "module example.com/poc\n\ngo 1.22\n",
            "util/util.go": "package util\nfunc First() int { return 1 }\n",
            "service/service.go": (
                "package service\nimport \"example.com/poc/util\"\n"
                "func Run() int { return util.First() }\n"
            ),
        },
    )
    first = build_repo_graph(tree, [], collect_sources=True)[2]
    (tree / "util/util.go").write_text(
        "package utils\nfunc First() int { return 1 }\n"
    )

    patched = _patched(tree, _seed_rows(first))

    assert not patched.patch_plan.eligible
    assert "dependency surface" in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path)


def test_override_dispatch_edges_survive_a_body_patch(tmp_path: Path) -> None:
    tree = tmp_path / "repo"
    files = {
        "Base.java": "class Base { public int value() { return 1; } }\n",
        "Derived.java": (
            "class Derived extends Base { @Override public int value() { return 2; } }\n"
        ),
    }
    _write(tree, files)
    first = build_repo_graph(tree, [], collect_sources=True)[2]
    assert any(data.get("edge_type") == "dispatches_to" for _, _, data in first.graph().edges(data=True))
    (tree / "Derived.java").write_text(
        files["Derived.java"].replace("return 2", "return 3")
    )

    patched = _patched(tree, _seed_rows(first))

    assert patched.patch_plan.eligible
    _assert_equals_full(tree, patched, tmp_path)


def test_go_interface_edges_survive_a_body_patch(tmp_path: Path) -> None:
    tree = tmp_path / "repo"
    files = {
        "go.mod": "module example.com/poc\n\ngo 1.22\n",
        "shape.go": "package poc\ntype Sizer interface { Size() int }\n",
        "widget.go": "package poc\ntype Widget struct{}\nfunc (Widget) Size() int { return 1 }\n",
    }
    _write(tree, files)
    first = build_repo_graph(tree, [], collect_sources=True)[2]
    assert any(data.get("edge_type") == "method_implements" for _, _, data in first.graph().edges(data=True))
    (tree / "widget.go").write_text(files["widget.go"].replace("return 1", "return 2"))

    patched = _patched(tree, _seed_rows(first))

    assert patched.patch_plan.eligible
    _assert_equals_full(tree, patched, tmp_path)


def test_no_seed_means_a_plain_full_build(indexed: tuple[Path, dict[str, list]]) -> None:
    tree, _seed = indexed

    graph_builder = build_repo_graph(tree, [], collect_sources=True)[2]

    assert not graph_builder.patch_plan.eligible
    assert graph_builder.patch_plan.reason == "feature disabled"


def test_patch_resolves_only_the_changed_file(
    indexed: tuple[Path, dict[str, list]], monkeypatch: pytest.MonkeyPatch
) -> None:
    tree, seed = indexed
    (tree / _SERVICE).write_text(_FILES[_SERVICE].replace("Util.id()", "Util.twice(1)"))
    resolved: list[str] = []
    original = GraphBuilder._add_import_edge

    def spy(self: Any, path: str, *args: Any) -> None:
        resolved.append(path)
        original(self, path, *args)

    monkeypatch.setattr(GraphBuilder, "_add_import_edge", spy)

    patched = _patched(tree, seed)

    assert patched.patch_plan.eligible
    assert set(resolved) == {_SERVICE}


@pytest.mark.parametrize(
    ("language", "files", "target", "before", "after"),
    [
        pytest.param(
            "python",
            {
                "util.py": "def first():\n    return 1\n\ndef second():\n    return 2\n",
                "service.py": (
                    "from util import first, second\n\ndef run():\n    return first()\n"
                ),
            },
            "service.py",
            "return first()",
            "return first() + second()",
            id="python-body-only",
        ),
        pytest.param(
            "go",
            {
                "go.mod": "module example.com/poc\n\ngo 1.22\n",
                "util/util.go": (
                    "package util\nfunc First() int { return 1 }\n"
                    "func Second() int { return 2 }\n"
                ),
                "service/shape.go": "package service\ntype Sizer interface { Size() int }\n",
                "service/widget.go": (
                    "package service\ntype Widget struct{}\n"
                    "func (Widget) Size() int { return 1 }\n"
                ),
                "service/service.go": (
                    "package service\nimport \"example.com/poc/util\"\n"
                    "func Run() int { return util.First() }\n"
                ),
            },
            "service/service.go",
            "return util.First()",
            "return util.First() + util.Second()",
            id="go-body-only",
        ),
        pytest.param(
            "csharp",
            {
                "Util.cs": (
                    "namespace Poc;\npublic class Util {\n"
                    "    public static int First() => 1;\n"
                    "    public static int Second() => 2;\n}\n"
                ),
                "Service.cs": (
                    "using Poc;\nnamespace Poc;\npublic class Service {\n"
                    "    public int Run() { return Util.First(); }\n}\n"
                ),
            },
            "Service.cs",
            "return Util.First();",
            "return Util.First() + Util.Second();",
            id="csharp-body-only",
        ),
        pytest.param(
            "kotlin",
            {
                "Util.kt": (
                    "package com.acme\nobject Util {\n"
                    "    fun first(): Int = 1\n    fun second(): Int = 2\n}\n"
                ),
                "Service.kt": (
                    "package com.acme\nclass Service {\n"
                    "    fun run(): Int { return Util.first() }\n}\n"
                ),
            },
            "Service.kt",
            "Util.first()",
            "Util.first() + Util.second()",
            id="kotlin-body-only",
        ),
        pytest.param(
            "rust",
            {
                "Cargo.toml": "[package]\nname = \"poc\"\nversion = \"0.1.0\"\n",
                "src/lib.rs": "pub mod util;\npub mod service;\n",
                "src/util.rs": (
                    "pub fn first() -> i32 { 1 }\n"
                    "pub fn second() -> i32 { 2 }\n"
                ),
                "src/service.rs": (
                    "use crate::util;\npub fn run() -> i32 { util::first() }\n"
                ),
            },
            "src/service.rs",
            "util::first()",
            "util::first() + util::second()",
            id="rust-body-only",
        ),
        pytest.param(
            "ruby",
            {
                "util.rb": (
                    "module Util\n  def self.first; 1; end\n"
                    "  def self.second; 2; end\nend\n"
                ),
                "service.rb": (
                    "require_relative 'util'\nclass Service\n"
                    "  def run; Util.first; end\nend\n"
                ),
            },
            "service.rb",
            "Util.first;",
            "Util.first + Util.second;",
            id="ruby-body-only",
        ),
        pytest.param(
            "php",
            {
                "Util.php": (
                    "<?php\nnamespace Poc;\n"
                    "function first(): int { return 1; }\n"
                    "function second(): int { return 2; }\n"
                ),
                "Service.php": (
                    "<?php\nnamespace Poc;\n"
                    "function run(): int { return first(); }\n"
                ),
            },
            "Service.php",
            "return first();",
            "return first() + second();",
            id="php-body-only",
        ),
        pytest.param(
            "scala",
            {
                "Util.scala": (
                    "package poc\nobject Util { def first: Int = 1; def second: Int = 2 }\n"
                ),
                "Service.scala": (
                    "package poc\nobject Service { def run: Int = Util.first }\n"
                ),
            },
            "Service.scala",
            "Util.first }",
            "Util.first + Util.second }",
            id="scala-body-only",
        ),
        pytest.param(
            "swift",
            {
                "Util.swift": (
                    "public enum Util { static func first() -> Int { 1 }; "
                    "static func second() -> Int { 2 } }\n"
                ),
                "Service.swift": (
                    "public enum Service { static func run() -> Int { Util.first() } }\n"
                ),
            },
            "Service.swift",
            "Util.first()",
            "Util.first() + Util.second()",
            id="swift-body-only",
        ),
        pytest.param(
            "typescript",
            {
                "util.ts": (
                    "export function first(): number { return 1; }\n"
                    "export function second(): number { return 2; }\n"
                ),
                "service.ts": (
                    "import { first, second } from './util';\n"
                    "export function run(): number { return first(); }\n"
                ),
            },
            "service.ts",
            "return first();",
            "return first() + second();",
            id="typescript-body-only",
        ),
        pytest.param(
            "javascript",
            {
                "util.js": (
                    "export function first() { return 1; }\n"
                    "export function second() { return 2; }\n"
                ),
                "service.js": (
                    "import { first, second } from './util.js';\n"
                    "export function run() { return first(); }\n"
                ),
            },
            "service.js",
            "return first();",
            "return first() + second();",
            id="javascript-body-only",
        ),
    ],
)
def test_supported_language_body_edit_matches_full_build(
    tmp_path: Path,
    language: str,
    files: dict[str, str],
    target: str,
    before: str,
    after: str,
) -> None:
    tree = tmp_path / language
    _write(tree, files)
    original = build_repo_graph(tree, [], collect_sources=True)[2]
    (tree / target).write_text(files[target].replace(before, after))

    patched = _patched(tree, _seed_rows(original))

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert patched.patch_plan.scope == (target,)
    _assert_equals_full(tree, patched, tmp_path / f"{language}-oracle")


def test_ruby_spec_mirror_edge_survives_a_body_patch(tmp_path: Path) -> None:
    tree = tmp_path / "ruby"
    spec_path = "spec/lib/rack/protection/base_spec.rb"
    subject_path = "lib/rack/protection/base.rb"
    _write(
        tree,
        {
            subject_path: (
                "module Rack\n  module Protection\n    class Base\n    end\n"
                "  end\nend\n"
            ),
            spec_path: (
                "RSpec.describe Rack::Protection::Base do\n"
                "  value = 1\nend\n"
            ),
        },
    )
    original = build_repo_graph(tree, [], collect_sources=True)[2]
    original_edges = _edges(original)
    assert any(
        source == spec_path and target == subject_path and kind == "imports"
        for source, target, kind, *_ in original_edges
    )
    (tree / spec_path).write_text(
        (tree / spec_path).read_text().replace("value = 1", "value = 2")
    )

    patched = _patched(tree, _seed_rows(original))

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert patched.patch_plan.scope == (spec_path,)
    assert any(
        source == spec_path
        and target == subject_path
        and kind == "imports"
        and hint_source == "spec_mirror"
        for source, target, kind, _confidence, hint_source, *_ in _edges(patched)
    )
    _assert_equals_full(tree, patched, tmp_path / "ruby-oracle")


def test_registration_edge_survives_a_registrar_body_patch(tmp_path: Path) -> None:
    tree = tmp_path / "registrars"
    registrar_path = "registry.py"
    handler_path = "handlers.py"
    _write(
        tree,
        {
            registrar_path: (
                "_HANDLERS = []\n"
                "def register(fn):\n"
                "    _HANDLERS.append(fn)\n"
                "    marker = 1\n"
                "    return fn\n"
            ),
            handler_path: (
                "from registry import register\n\n"
                "@register\n"
                "def handle():\n"
                "    return 1\n"
            ),
        },
    )
    original = build_repo_graph(tree, [], collect_sources=True)[2]
    def has_registration_edge(graph_builder: Any) -> bool:
        return any(
            source == registrar_path + "::register"
            and target == handler_path + "::handle"
            and edge_type == "references"
            and hint_source == "decorator_registration"
            for source, target, edge_type, _confidence, hint_source, *_ in _edges(graph_builder)
        )

    assert has_registration_edge(original)
    (tree / registrar_path).write_text(
        (tree / registrar_path).read_text().replace("marker = 1", "marker = 2")
    )

    patched = _patched(tree, _seed_rows(original))

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert patched.patch_plan.scope == (registrar_path,)
    assert has_registration_edge(patched)
    _assert_equals_full(tree, patched, tmp_path / "registrars-oracle")
