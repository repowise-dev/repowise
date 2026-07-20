"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from tests.unit.dead_code._helpers import _build_graph, _old_date


def test_framework_decorator_excluded():
    """A symbol decorated with pytest.fixture should NOT be flagged."""
    g = _build_graph(
        nodes={
            "pkg/conftest.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "symbols": [
                    {
                        "name": "db_session",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": ["pytest.fixture"],
                        "start_line": 1,
                        "end_line": 15,
                        "complexity_estimate": 1,
                    },
                ],
            },
        },
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
        }
    )

    sym_names = [f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
    assert "db_session" not in sym_names


def test_dynamic_pattern_excluded():
    """A symbol matching '*Handler' dynamic pattern should NOT be flagged as unused."""
    g = _build_graph(
        nodes={
            "pkg/events.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "symbols": [
                    {
                        "name": "EventHandler",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 40,
                        "complexity_estimate": 3,
                    },
                ],
            },
        },
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
        }
    )

    sym_names = [f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
    assert "EventHandler" not in sym_names


def test_at_prefixed_decorator_excluded():
    """Decorators stored with the leading '@' (as the parser emits them)
    must still match the framework-decorator whitelist."""
    g = _build_graph(
        nodes={
            "pkg/routes.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "symbols": [
                    {
                        "name": "list_items",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": ['@router.get("/items")'],
                        "start_line": 1,
                        "end_line": 8,
                    },
                    {
                        "name": "lifespan",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": ["@asynccontextmanager"],
                        "start_line": 10,
                        "end_line": 20,
                    },
                ],
            },
        },
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
        }
    )
    sym_names = [f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
    assert "list_items" not in sym_names
    assert "lifespan" not in sym_names


def test_dynamic_edge_clamps_unreachable_confidence():
    """A file in a package that has dynamic graph edges should have confidence clamped to 0.4."""
    g = _build_graph(
        nodes={
            "pkg/orphan.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
            "pkg/dispatcher.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 3,
                "symbols": [],
            },
            "pkg/handler.py": {
                "is_entry_point": True,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 3,
                "symbols": [],
            },
        },
        edges=[
            ("pkg/dispatcher.py", "pkg/handler.py", {"edge_type": "dynamic"}),
        ],
    )

    git_meta = {
        "pkg/orphan.py": {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=400),
            "age_days": 500,
        },
    }

    analyzer = DeadCodeAnalyzer(g, git_meta_map=git_meta)
    report = analyzer.analyze(
        {
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )

    findings = [f for f in report.findings if f.file_path == "pkg/orphan.py"]
    assert len(findings) == 1
    assert findings[0].confidence == pytest.approx(0.4)
    assert any("dynamic" in e.lower() for e in findings[0].evidence)


def test_find_dynamic_edge_files_handles_subtypes():
    """find_dynamic_edge_files picks up edges with dynamic_* sub-type prefixes."""
    from repowise.core.analysis.dead_code.dynamic_markers import find_dynamic_edge_files

    g = nx.DiGraph()
    g.add_node("a.py")
    g.add_node("b.py")
    g.add_node("c.py")
    g.add_node("d.py")
    g.add_node("external:something")
    g.add_edge("a.py", "b.py", edge_type="dynamic_uses")
    g.add_edge("c.py", "d.py", edge_type="dynamic_imports")
    g.add_edge("a.py", "external:something", edge_type="dynamic")
    # Non-dynamic edges should not contribute
    g.add_edge("b.py", "c.py", edge_type="calls")

    files = find_dynamic_edge_files(g)
    assert {"a.py", "b.py", "c.py", "d.py"} <= files
    assert "external:something" not in files


@pytest.mark.parametrize(
    "suffix,expected_markers",
    [
        (".go", ("plugin.Open(", "reflect.New(", "reflect.TypeOf(", "reflect.ValueOf(")),
        (
            ".rb",
            ("autoload ", "const_get(", "Object.send(", "Kernel.const_get(", ".public_send("),
        ),
        (
            ".php",
            (
                "class_exists(",
                "call_user_func(",
                "call_user_func_array(",
                "new $",
                "ReflectionClass(",
            ),
        ),
        (
            ".kt",
            ("Class.forName(", "ServiceLoader.load(", "KClass.createInstance(", "::class.java"),
        ),
        (
            ".swift",
            ("NSClassFromString(", "Selector(", "#selector(", "NSStringFromClass("),
        ),
        (".scala", ("Class.forName(", "runtimeMirror(", "reflect.runtime")),
    ],
)
def test_dynamic_markers_per_language_coverage(suffix, expected_markers):
    """Each language's marker tuple must contain the documented Phase 2 entries."""
    from repowise.core.analysis.dead_code.dynamic_markers import _DYNAMIC_IMPORT_MARKERS

    markers = _DYNAMIC_IMPORT_MARKERS.get(suffix, ())
    for expected in expected_markers:
        assert expected in markers, f"missing marker {expected!r} for {suffix}"


def test_click_group_command_decorator_excluded():
    """Subcommands registered on a locally-named Click ``Group`` are
    decorated as e.g. ``@my_cli.command("add")``. The receiver name is
    project-local, so the matcher needs to recognise the ``.command``
    suffix rather than a hard-coded ``click.command`` prefix. Same shape
    covers Typer (``.command``/``.callback``) and any user-named
    dispatcher."""
    g = _build_graph(
        nodes={
            "pkg/cli.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 3,
                "symbols": [
                    {
                        "name": "decision_add",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": ["decision_group.command"],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 1,
                    },
                    {
                        "name": "decision_list",
                        "kind": "function",
                        "visibility": "public",
                        # Real-world capture sometimes includes the call
                        # arguments — the matcher must strip them.
                        "decorators": ['my_cli.group("decision")'],
                        "start_line": 12,
                        "end_line": 20,
                        "complexity_estimate": 1,
                    },
                    {
                        "name": "after_cmd",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": ["app.callback"],
                        "start_line": 22,
                        "end_line": 28,
                        "complexity_estimate": 1,
                    },
                ],
            },
        },
        edges=[],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze({"detect_unreachable_files": False, "detect_zombie_packages": False})
    names = {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}
    assert "decision_add" not in names
    assert "decision_list" not in names
    assert "after_cmd" not in names


def test_unrelated_dot_command_function_still_flagged():
    """A decorator suffix-match must not accidentally whitelist symbols
    with unrelated decorators. ``@functools.lru_cache`` does not end with
    ``.command/.group/.callback``, so a plain unused public helper with
    that decorator is still surfaced."""
    g = _build_graph(
        nodes={
            "pkg/helpers.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "expensive_lookup",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": ["functools.lru_cache"],
                        "start_line": 1,
                        "end_line": 20,
                        "complexity_estimate": 1,
                    },
                ],
            },
        },
        edges=[],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze({"detect_unreachable_files": False, "detect_zombie_packages": False})
    names = {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}
    assert "expensive_lookup" in names


@pytest.mark.parametrize(
    "decorator",
    [
        # Route decorator on a receiver the prefix list doesn't know
        # (``_repo_health_router = APIRouter()``): the HTTP-verb suffix match
        # must rescue it regardless of the local variable name.
        '@_repo_health_router.get("/health/coordinator")',
        '@api.websocket("/ws")',
        # Registry registration by side effect: consumers resolve the symbol
        # by string key, never by import.
        '@filter_registry.register("git_status")',
        # ``from pytest import fixture`` leaves a bare, undotted decorator.
        "@fixture",
    ],
)
def test_renamed_receiver_and_registry_decorators_excluded(decorator):
    g = _build_graph(
        nodes={
            "pkg/wired.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "wired_symbol",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [decorator],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 1,
                    },
                ],
            },
        },
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze({"detect_unreachable_files": False, "detect_zombie_packages": False})
    names = {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}
    assert "wired_symbol" not in names
