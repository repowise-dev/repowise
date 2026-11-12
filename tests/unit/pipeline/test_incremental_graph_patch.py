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
    save_resolutions(
        index_dir,
        graph_builder._parsed_files.values(),
        graph_builder.import_resolutions,
        graph_builder._source_map,
    )
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
    assert "pom.xml" in (patched.patch_plan.reason or "")
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
            "file added",
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


# Documentation is the one kind of file a patch may add, remove or edit outside
# PATCHABLE_LANGUAGES: it gives the graph only its own file node. Every case
# below, accepted or refused, must leave the same graph as a full rebuild.
_DOCS_TREE = {
    "util.py": "def helper():\n    return 1\n",
    "main.py": "from util import helper\n\ndef run():\n    return helper() + orphan()\n",
    "web/view.ts": (
        "import { fmt } from './fmt';\n"
        "import guide from './guide.md';\n"
        "import { later } from './later';\n"
        "export function view() { return fmt(guide) + later(); }\n"
    ),
    "web/fmt.ts": "export function fmt(s: string) { return s; }\n",
    "web/guide.md": "# Guide\n",
    "web/tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@w/*": ["./*"]}}}\n',
    "README.md": "# Demo\n\nRun `main.py`.\n",
    "docs/notes.md": "# Notes\n",
    "docs/intro.mdx": "import { fmt } from '../web/fmt'\n\n# Intro\n",
    "docs/README.md": "# Docs\n",
    # Two source files under the ``index`` stem: a bucket of two answers no
    # import-free lookup, so an ``index.md`` may join it.
    "web/index.ts": "export { fmt } from './fmt';\n",
    "lib/index.js": "export const one = 1;\n",
    # ``Shape`` and ``Kind`` are used with no import: the type-reference pass
    # takes the only file of that stem. ``shape`` holds one file, ``kind`` two.
    "web/shape.ts": "export interface Shape { w: number }\n",
    "web/draw.ts": "export function draw(s: Shape) { return s.w; }\n",
    "web/kind.ts": "export interface Kind { k: string }\n",
    "web/pick.ts": "export function pick(k: Kind) { return k.k; }\n",
    "docs/kind.md": "# Kind\n",
}


def _edit(rel: str, text: str) -> Any:
    return lambda t: (t / rel).write_text(text)


def _remove(rel: str) -> Any:
    return lambda t: (t / rel).unlink()


def _add(rel: str, text: str) -> Any:
    return lambda t: _write(t, {rel: text})


def _both(*edits: Any) -> Any:
    def apply(t: Path) -> None:
        for edit in edits:
            edit(t)

    return apply


@pytest.fixture
def docs_indexed(tmp_path: Path) -> tuple[Path, dict[str, list]]:
    tree = tmp_path / "docs-repo"
    _write(tree, _DOCS_TREE)
    first = build_repo_graph(tree, [], collect_sources=True)[2]
    return tree, _seed_rows(first)


@pytest.mark.parametrize(
    ("edit", "scope", "removed"),
    [
        pytest.param(_edit("README.md", "# Demo\n\nRewritten.\n"), ("README.md",), (), id="edit-md"),
        pytest.param(_add("CHANGELOG.md", "# 1.0\n"), ("CHANGELOG.md",), (), id="add-md"),
        pytest.param(_remove("docs/notes.md"), (), ("docs/notes.md",), id="remove-md"),
        pytest.param(
            _both(_remove("docs/notes.md"), _add("docs/archive/notes-2026.md", "# Notes\n")),
            ("docs/archive/notes-2026.md",),
            ("docs/notes.md",),
            id="rename-md",
        ),
        pytest.param(
            _both(
                _add("CHANGELOG.md", "# 1.0\n"),
                _edit("web/fmt.ts", "export function fmt(s: string) { return s + ''; }\n"),
            ),
            ("CHANGELOG.md", "web/fmt.ts"),
            (),
            id="add-md-with-body-edit",
        ),
        # Code imports it, but an edit keeps the path and its (empty) surface.
        pytest.param(_edit("web/guide.md", "# Guide\n\nMore.\n"), ("web/guide.md",), (), id="edit-imported-md"),
        # Directory-index docs that share a stem only with prose or with 2+ sources.
        pytest.param(_add("web/README.md", "# Web\n"), ("web/README.md",), (), id="add-readme-beside-readmes"),
        pytest.param(_remove("docs/README.md"), (), ("docs/README.md",), id="remove-one-of-two-readmes"),
        pytest.param(_remove("README.md"), (), ("README.md",), id="remove-root-readme"),
        pytest.param(_add("docs/index.md", "# Home\n"), ("docs/index.md",), (), id="add-index-md-beside-sources"),
    ],
)
def test_documentation_changes_are_patched_and_equal_a_full_rebuild(
    docs_indexed: tuple[Path, dict[str, list]],
    tmp_path: Path,
    edit: Any,
    scope: tuple[str, ...],
    removed: tuple[str, ...],
) -> None:
    tree, seed = docs_indexed
    edit(tree)

    patched = _patched(tree, seed)

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert patched.patch_plan.scope == scope
    assert patched.patch_plan.removed == removed
    for path in removed:
        assert path not in patched.graph()
        assert f"{path}::__module__" not in patched.graph()
    _assert_equals_full(tree, patched, tmp_path)


@pytest.mark.parametrize(
    ("edit", "reason"),
    [
        pytest.param(_remove("web/guide.md"), "web/guide.md: an import may resolve to it", id="removed-md-imported-by-code"),
        pytest.param(_add("docs/later.md", "# Later\n"), "docs/later.md: an import may resolve to it", id="added-md-named-by-import"),
        # Same stem as fmt.ts: stem-map lookups would see two candidates.
        pytest.param(_add("docs/fmt.md", "# fmt\n"), "docs/fmt.md: changes what its import stem resolves to", id="added-md-stem-collision"),
        # The only ``shape`` file stops being the only one: draw.ts loses its type edge.
        pytest.param(_add("docs/shape.md", "# Shape\n"), "docs/shape.md: changes what its import stem resolves to", id="added-md-hides-type-target"),
        # kind.ts becomes the only ``kind`` file: pick.ts gains a type edge.
        pytest.param(_remove("docs/kind.md"), "docs/kind.md: changes what its import stem resolves to", id="removed-md-reveals-type-target"),
        pytest.param(
            _edit("web/tsconfig.json", '{"compilerOptions": {"baseUrl": "."}}\n'),
            "json edits are not patched",
            id="tsconfig-edit",
        ),
        pytest.param(_add("web/package.json", '{"name": "web"}\n'), "web/package.json: file added", id="manifest-added"),
        # The TypeScript warmup reads .mdx imports from disk.
        pytest.param(
            _edit("docs/intro.mdx", "import { fmt } from '../web/view'\n\n# Intro\n"),
            "markdown edits are not patched",
            id="mdx-edit",
        ),
        pytest.param(_add("docs/more.mdx", "# More\n"), "docs/more.mdx: file added", id="mdx-added"),
    ],
)
def test_changes_that_can_reach_other_files_fall_back(
    docs_indexed: tuple[Path, dict[str, list]], tmp_path: Path, edit: Any, reason: str
) -> None:
    tree, seed = docs_indexed
    edit(tree)

    patched = _patched(tree, seed)

    assert not patched.patch_plan.eligible
    assert reason in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path)


@pytest.mark.parametrize(
    ("edit", "reached"),
    [
        # main.py calls orphan() by bare name: a new definer gives it a call edge.
        pytest.param(_add("orphan.py", "def orphan():\n    return 2\n"), "main.py", id="new-definer-of-bare-call"),
        # view.ts imports './later', unresolved until this file exists.
        pytest.param(_add("web/later.ts", "export const later = () => 1;\n"), "web/view.ts", id="new-import-target"),
        pytest.param(_remove("util.py"), "main.py", id="removed-import-target"),
    ],
)
def test_source_adds_and_removes_re_resolve_the_files_they_reach(
    docs_indexed: tuple[Path, dict[str, list]], tmp_path: Path, edit: Any, reached: str
) -> None:
    tree, seed = docs_indexed
    edit(tree)

    patched = _patched(tree, seed)

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert patched.patch_plan.refresh
    assert reached in patched.patch_plan.scope
    _assert_equals_full(tree, patched, tmp_path)


def test_new_definer_of_a_bare_call_changes_an_unchanged_files_edges(
    docs_indexed: tuple[Path, dict[str, list]], tmp_path: Path
) -> None:
    """Why an added source file widens the scope: it rewires a file it never touched."""
    tree, seed = docs_indexed
    before = {(u, v) for u, v, *_ in _edges(_patched(tree, seed)) if u.startswith("main.py")}
    _write(tree, {"orphan.py": "def orphan():\n    return 2\n"})

    full = _full(tree, tmp_path / "full")

    after = {(u, v) for u, v, *_ in _edges(full) if u.startswith("main.py")}
    assert after - before, "the fixture must show an unchanged file gaining an edge"


@pytest.mark.parametrize(
    ("edit", "edge", "present_after"),
    [
        pytest.param(_add("docs/shape.md", "# Shape\n"), ("web/draw.ts", "web/shape.ts"), False, id="hide"),
        pytest.param(_remove("docs/kind.md"), ("web/pick.ts", "web/kind.ts"), True, id="reveal"),
    ],
)
def test_a_doc_can_change_an_unchanged_files_type_edge(
    docs_indexed: tuple[Path, dict[str, list]], tmp_path: Path, edit: Any, edge: tuple, present_after: bool
) -> None:
    """Why a doc that changes a one-file stem bucket must fall back."""
    tree, seed = docs_indexed
    assert (edge in {(u, v) for u, v, _ in seed["edges"]}) is not present_after
    edit(tree)

    full = _full(tree, tmp_path / "full")

    assert (edge in {(u, v) for u, v, *_ in _edges(full)}) is present_after


def test_doc_displacing_a_stem_lookup_the_import_does_not_spell_falls_back(tmp_path: Path) -> None:
    """Ruby looks ``require 'foo-bar'`` up as ``foo_bar``: no import text names the doc."""
    tree = tmp_path / "repo"
    _write(
        tree,
        {
            "app/main.rb": "require 'foo-bar'\n\ndef run\n  FooBar.go\nend\n",
            "lib/a/foo_bar.rb": "class FooBar\n  def self.go; 1; end\nend\n",
            "lib/b/foo_bar.rb": "class FooBar2\n  def self.go; 2; end\nend\n",
        },
    )
    seed = _seed_rows(build_repo_graph(tree, [], collect_sources=True)[2])
    # Fewer path parts: the root doc becomes the stem's best candidate.
    _write(tree, {"foo_bar.md": "# Foo bar\n"})

    patched = _patched(tree, seed)

    assert patched.patch_plan.reason == "foo_bar.md: changes what its import stem resolves to"
    _assert_equals_full(tree, patched, tmp_path)


def test_unparsed_previously_indexed_file_is_not_treated_as_removed(
    docs_indexed: tuple[Path, dict[str, list]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.pipeline.incremental import graph as graph_mod

    tree, seed = docs_indexed
    (tree / "docs/notes.md").write_text("# Notes\n\nEdited.\n")
    real = graph_mod._parse_misses

    def drop_notes(cached_hits, to_parse, *args, **kwargs):
        merged = real(cached_hits, to_parse, *args, **kwargs)
        for idx, (fi, _src), _hash in to_parse:
            if fi.path == "docs/notes.md":
                merged.pop(idx, None)
        return merged

    monkeypatch.setattr(graph_mod, "_parse_misses", drop_notes)

    patched = _patched(tree, seed)

    assert not patched.patch_plan.eligible
    assert patched.patch_plan.reason == "docs/notes.md: could not be parsed"


@pytest.mark.parametrize(
    ("edit", "doc"),
    [
        # Shares its stem only with prose, so the stem rule alone would patch.
        pytest.param(_remove("pkg/README.md"), "pkg/README.md", id="remove-named-readme"),
        # A unique stem the import never spells (``#notes``).
        pytest.param(_add("pkg/CHANGES.md", "# Changes\n"), "pkg/CHANGES.md", id="add-named-doc"),
    ],
)
def test_doc_a_manifest_names_falls_back(tmp_path: Path, edit: Any, doc: str) -> None:
    """package.json maps ``#docs`` and ``#notes`` onto docs no import's text spells."""
    tree = tmp_path / "repo"
    _write(
        tree,
        {
            "README.md": "# Root\n",
            "pkg/package.json": (
                '{"name": "pkg", "imports": {"#docs": "./README.md", "#notes": "./CHANGES.md"}}\n'
            ),
            "pkg/README.md": "# Pkg\n",
            "pkg/a.ts": "import docs from '#docs';\nimport notes from '#notes';\nexport const a = docs + notes;\n",
        },
    )
    seed = _seed_rows(build_repo_graph(tree, [], collect_sources=True)[2])
    assert ("pkg/a.ts", "pkg/README.md") in {(u, v) for u, v, _ in seed["edges"]}
    edit(tree)

    patched = _patched(tree, seed)

    assert patched.patch_plan.reason == f"{doc}: a manifest names it"
    _assert_equals_full(tree, patched, tmp_path)




# C and C++: headers, out-of-line method definitions that calls resolve onto,
# header/implementation pairs (``util.c`` does not include ``util.h``), a
# function-like macro, a registration marker the warmup reads from source, and
# the facts the resolver reads from another file's text (a pure virtual, a
# class template's parameters, an alias).
_CPP_TREE = {
    "src/base.h": "#pragma once\nnamespace acme {\nusing Count = int;\n}\n",
    "src/widget.h": (
        "#pragma once\n"
        '#include "base.h"\n'
        "namespace acme {\n"
        "class Base { public: virtual int size() const; };\n"
        "class Widget : public Base {\n"
        "public:\n"
        "  int size() const override;\n"
        "  int twice() const;\n"
        "};\n"
        "inline int helper(int x) { return x + 1; }\n"
        "#define SQUARE(x) ((x) * (x))\n"
        "}\n"
    ),
    "src/widget.cpp": (
        '#include "widget.h"\n'
        "namespace acme {\n"
        "int Base::size() const { return 0; }\n"
        "int Widget::size() const { return helper(1); }\n"
        "int Widget::twice() const { return size() * 2; }\n"
        "}\n"
    ),
    "src/main.cpp": (
        '#include "widget.h"\n'
        '#include "shape.h"\n'
        "int run() {\n"
        "  acme::Widget w;\n"
        "  return w.twice() + SQUARE(2);\n"
        "}\n"
        "int measure(const Shape& s) { return s.area(); }\n"
    ),
    "src/shape.h": (
        "#pragma once\n"
        "class Shape { public: virtual int area() const = 0; };\n"
        "template <typename T>\n"
        "class Box : public T {\n"
        "public:\n"
        "  int area() const { return 1; }\n"
        "};\n"
    ),
    "src/util.h": "int c_util(int a);\nint c_other(int a);\n",
    "src/util.c": (
        "int c_util(int a) { return a; }\n"
        "int c_other(int a) { return a + 1; }\n"
    ),
    "src/cmain.c": '#include "util.h"\nint c_main(void) { return c_util(1); }\n',
    "src/plugin.cpp": (
        '#include "widget.h"\n'
        "// PYBIND11_MODULE registers this file at load time.\n"
        "int plugin() { return acme::helper(2); }\n"
    ),
}


@pytest.fixture
def cpp_indexed(tmp_path: Path) -> tuple[Path, dict[str, list]]:
    tree = tmp_path / "cpp"
    _write(tree, _CPP_TREE)
    first = build_repo_graph(tree, [], collect_sources=True)[2]
    return tree, _seed_rows(first)


def _replace(rel: str, old: str, new: str) -> Any:
    def apply(t: Path) -> None:
        text = _CPP_TREE[rel]
        assert old in text
        (t / rel).write_text(text.replace(old, new))

    return apply


@pytest.mark.parametrize(
    ("edit", "scope"),
    [
        pytest.param(
            _replace("src/widget.cpp", "return size() * 2;", "return size() * 2 + helper(3);"),
            ("src/widget.cpp",),
            id="cpp-method-body",
        ),
        pytest.param(
            _replace("src/main.cpp", "w.twice() + SQUARE(2)", "w.size() + SQUARE(2)"),
            ("src/main.cpp",),
            id="cpp-call-retargeted",
        ),
        pytest.param(
            _replace("src/widget.h", "return x + 1;", "return x + 2;"),
            ("src/widget.h",),
            id="header-inline-body",
        ),
        pytest.param(
            _replace("src/widget.h", "((x) * (x))", "((x) * (x) * 1)"),
            ("src/widget.h",),
            id="header-macro-body",
        ),
        pytest.param(
            _replace("src/util.c", "return a;", "return c_other(a);"),
            ("src/util.c",),
            id="c-body-with-header-pair",
        ),
        pytest.param(
            _replace("src/plugin.cpp", "// PYBIND11_MODULE registers", "// Registers"),
            ("src/plugin.cpp",),
            id="registration-marker-removed",
        ),
        pytest.param(
            _both(
                _replace("src/widget.cpp", "return helper(1);", "return helper(2);"),
                _replace("src/util.c", "return a + 1;", "return a + 2;"),
            ),
            ("src/util.c", "src/widget.cpp"),
            id="c-and-cpp-bodies",
        ),
    ],
)
def test_cpp_body_edits_are_patched_and_equal_a_full_rebuild(
    cpp_indexed: tuple[Path, dict[str, list]],
    tmp_path: Path,
    edit: Any,
    scope: tuple[str, ...],
) -> None:
    tree, seed = cpp_indexed
    edit(tree)

    patched = _patched(tree, seed)

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert not patched.patch_plan.refresh
    assert patched.patch_plan.scope == scope
    _assert_equals_full(tree, patched, tmp_path / "cpp-oracle")


def test_cpp_header_pair_edges_survive_a_patch_of_either_side(
    cpp_indexed: tuple[Path, dict[str, list]], tmp_path: Path
) -> None:
    """The pair edges come from paths, not resolution: a patch must keep both."""
    tree, seed = cpp_indexed
    pairs = {("src/util.c", "src/util.h"), ("src/util.h", "src/util.c")}
    assert pairs <= {(u, v) for u, v, _ in seed["edges"]}
    _replace("src/util.c", "return a;", "return a * 2;")(tree)
    (tree / "src/util.h").write_text(_CPP_TREE["src/util.h"] + "/* declarations */\n")

    patched = _patched(tree, seed)

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert pairs <= {(u, v) for u, v in patched.graph().edges()}
    _assert_equals_full(tree, patched, tmp_path / "pairs-oracle")


@pytest.mark.parametrize(
    ("edit", "reached"),
    [
        pytest.param(
            _both(
                _replace("src/widget.h", "  int twice() const;\n", "  int twice() const;\n  int thrice() const;\n"),
                _replace("src/main.cpp", "w.twice()", "w.thrice()"),
            ),
            "src/main.cpp",
            id="header-declaration-added",
        ),
        pytest.param(
            _replace("src/widget.cpp", "int Widget::twice() const", "long Widget::twice() const"),
            "src/main.cpp",
            id="definition-signature-changed",
        ),
        # main.cpp already reaches base.h through widget.h: its reach is unchanged.
        pytest.param(
            _replace("src/main.cpp", '#include "shape.h"\n', '#include "shape.h"\n#include "base.h"\n'),
            "src/main.cpp",
            id="include-of-a-reached-header",
        ),
        pytest.param(
            _add("src/extra.cpp", "int extra() { return c_util(2); }\n"), "src/extra.cpp", id="cpp-file-added"
        ),
        # Facts the parse does not record, read off the header's text.
        pytest.param(
            _replace("src/shape.h", "area() const = 0;", "area() const;"),
            "src/main.cpp",
            id="pure-virtual-dropped",
        ),
        pytest.param(
            _replace("src/shape.h", "template <typename T>\nclass Box : public T", "template <typename U>\nclass Box : public U"),
            "src/shape.h",
            id="template-parameter-renamed",
        ),
        pytest.param(
            _replace("src/base.h", "using Count = int;", "using Count = long;"),
            "src/base.h",
            id="alias-target-changed",
        ),
        # util.h loses its pair edge to util.c, whose definitions go by name.
        pytest.param(_remove("src/util.c"), "src/cmain.c", id="c-file-removed"),
    ],
)
def test_cpp_edits_other_files_see_re_resolve_the_files_they_reach(
    cpp_indexed: tuple[Path, dict[str, list]], tmp_path: Path, edit: Any, reached: str
) -> None:
    tree, seed = cpp_indexed
    edit(tree)

    patched = _patched(tree, seed)

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert patched.patch_plan.refresh
    assert reached in patched.patch_plan.scope
    _assert_equals_full(tree, patched, tmp_path / "cpp-oracle")


@pytest.mark.parametrize(
    ("edit", "reason"),
    [
        # Every file that includes widget.h now reaches util.h and util.c.
        pytest.param(
            _replace("src/widget.h", '#include "base.h"\n', '#include "base.h"\n#include "util.h"\n'),
            "src/widget.h: the files its includes reach changed",
            id="header-include-added",
        ),
        pytest.param(
            _replace("src/widget.h", '#include "base.h"\n', ""),
            "src/widget.h: the files its includes reach changed",
            id="header-include-removed",
        ),
    ],
)
def test_cpp_edits_that_move_include_reach_fall_back(
    cpp_indexed: tuple[Path, dict[str, list]], tmp_path: Path, edit: Any, reason: str
) -> None:
    tree, seed = cpp_indexed
    edit(tree)
    timings = PhaseTimings()

    patched = build_repo_graph(
        tree, [], collect_sources=True, seed_edges=seed, patch_requested=True, timings=timings
    )[2]

    assert not patched.patch_plan.eligible
    assert patched.patch_plan.reason == reason
    assert {"rebuild.graph.patch", "rebuild.graph.full"} <= set(timings.totals)
    _assert_equals_full(tree, patched, tmp_path / "cpp-oracle")


def test_a_cpp_name_reaches_another_language_through_the_sites_that_name_it(
    cpp_indexed: tuple[Path, dict[str, list]], tmp_path: Path
) -> None:
    """A second ``twice`` makes the Python call's repo-wide unique match ambiguous."""
    tree, _ = cpp_indexed
    _write(
        tree,
        {
            "py/tools.py": "def twice_py():\n    return 2\n",
            "py/run.py": "def go():\n    return twice_py()\n",
            "py/notes.py": "# twice_py is documented here\nVALUE = 1\n",
        },
    )
    seed = _seed_rows(build_repo_graph(tree, [], collect_sources=True)[2])
    (tree / "src/extra.cpp").write_text("int twice_py() { return 3; }\n")

    patched = _patched(tree, seed)

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert "py/run.py" in patched.patch_plan.scope
    assert "py/notes.py" not in patched.patch_plan.scope
    _assert_equals_full(tree, patched, tmp_path / "cross-oracle")
