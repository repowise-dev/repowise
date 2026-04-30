"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import networkx as nx
import pytest

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _old_date(days: int = 365) -> datetime:
    """Return a datetime `days` ago (timezone-aware)."""
    return _now() - timedelta(days=days)


def _build_graph(
    nodes: dict[str, dict],
    edges: list[tuple[str, str]] | None = None,
) -> nx.DiGraph:
    """Create a DiGraph with the given node attributes and edges.

    ``nodes`` maps node-id to its attribute dict.  If a node has a
    ``symbols`` list, each entry is promoted to a proper symbol node
    connected to the file via a ``defines`` edge (matching the real
    GraphBuilder layout).
    ``edges`` is a list of (src, dst) pairs; edge data can be
    supplied as a 3-tuple (src, dst, data_dict).
    """
    g = nx.DiGraph()
    for name, attrs in nodes.items():
        attrs.setdefault("language", "python")
        # Extract symbols before adding the file node
        sym_list = attrs.pop("symbols", [])
        g.add_node(name, **attrs)
        # Create symbol nodes + defines edges (mirrors GraphBuilder.add_file)
        for sym in sym_list:
            sym_id = f"{name}::{sym['name']}"
            g.add_node(
                sym_id,
                node_type="symbol",
                file_path=name,
                **sym,
            )
            g.add_edge(name, sym_id, edge_type="defines")
    for edge in edges or []:
        if len(edge) == 3:
            g.add_edge(edge[0], edge[1], **(edge[2]))
        else:
            g.add_edge(edge[0], edge[1])
    return g


# ---------------------------------------------------------------------------
# 1. test_unreachable_file_detected
# ---------------------------------------------------------------------------


def test_unreachable_file_detected():
    """A file with in_degree=0, not an entry point, should be flagged as unreachable."""
    g = _build_graph(
        nodes={
            "pkg/orphan.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
            "pkg/main.py": {
                "is_entry_point": True,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 10,
                "symbols": [],
            },
        },
        edges=[],  # orphan.py has in_degree=0
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze({"detect_unused_exports": False, "detect_zombie_packages": False})

    unreachable = [f for f in report.findings if f.kind == DeadCodeKind.UNREACHABLE_FILE]
    paths = [f.file_path for f in unreachable]
    assert "pkg/orphan.py" in paths


# ---------------------------------------------------------------------------
# 2. test_entry_point_not_flagged
# ---------------------------------------------------------------------------


def test_entry_point_not_flagged():
    """A file marked as is_entry_point=True should NOT be flagged even with in_degree=0."""
    g = _build_graph(
        nodes={
            "pkg/main.py": {
                "is_entry_point": True,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 10,
                "symbols": [],
            },
        },
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze({"detect_unused_exports": False, "detect_zombie_packages": False})

    assert all(f.file_path != "pkg/main.py" for f in report.findings)


# ---------------------------------------------------------------------------
# 3. test_test_files_excluded
# ---------------------------------------------------------------------------


def test_test_files_excluded():
    """A test file (is_test=True) with in_degree=0 should NOT be flagged."""
    g = _build_graph(
        nodes={
            "tests/test_something.py": {
                "is_entry_point": False,
                "is_test": True,
                "is_api_contract": False,
                "symbol_count": 8,
                "symbols": [],
            },
        },
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze({"detect_unused_exports": False, "detect_zombie_packages": False})

    assert all(f.file_path != "tests/test_something.py" for f in report.findings)


# ---------------------------------------------------------------------------
# 4. test_unused_export_detected
# ---------------------------------------------------------------------------


def test_unused_export_detected():
    """A public symbol with no importers should be flagged as unused export."""
    g = _build_graph(
        nodes={
            "pkg/utils.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 3,
                "symbols": [
                    {
                        "name": "helper_func",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 2,
                    },
                ],
            },
            "pkg/main.py": {
                "is_entry_point": True,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 10,
                "symbols": [],
            },
        },
        # main.py imports utils.py but does NOT import helper_func by name
        edges=[("pkg/main.py", "pkg/utils.py", {"imported_names": ["other_func"]})],
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
        }
    )

    unused = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
    sym_names = [f.symbol_name for f in unused]
    assert "helper_func" in sym_names


# ---------------------------------------------------------------------------
# 5. test_framework_decorator_excluded
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 6. test_dynamic_pattern_excluded
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 7. test_confidence_low_for_recent_files
# ---------------------------------------------------------------------------


def test_confidence_low_for_recent_files():
    """Unreachable file with commit_count_90d > 0 should have confidence 0.4."""
    g = _build_graph(
        nodes={
            "pkg/recent.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
        },
    )

    git_meta = {
        "pkg/recent.py": {
            "commit_count_90d": 3,
            "last_commit_at": _now() - timedelta(days=10),
            "age_days": 100,
            "primary_owner_name": "dev@example.com",
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

    findings = [f for f in report.findings if f.file_path == "pkg/recent.py"]
    assert len(findings) == 1
    assert findings[0].confidence == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# 8. test_confidence_high_for_stale_unreachable
# ---------------------------------------------------------------------------


def test_confidence_high_for_stale_unreachable():
    """Unreachable file with no commits in 90d and last commit > 6 months ago -> confidence 1.0."""
    g = _build_graph(
        nodes={
            "pkg/stale.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
        },
    )

    git_meta = {
        "pkg/stale.py": {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=365),
            "age_days": 730,
            "primary_owner_name": "dev@example.com",
        },
    }

    analyzer = DeadCodeAnalyzer(g, git_meta_map=git_meta)
    report = analyzer.analyze(
        {
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
        }
    )

    findings = [f for f in report.findings if f.file_path == "pkg/stale.py"]
    assert len(findings) == 1
    assert findings[0].confidence == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 9. test_zombie_package_detected
# ---------------------------------------------------------------------------


def test_zombie_package_detected():
    """A package with no incoming inter-package imports should be flagged as zombie."""
    g = _build_graph(
        nodes={
            "pkgA/mod1.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
            "pkgA/mod2.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 3,
                "symbols": [],
            },
            "pkgB/mod1.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 7,
                "symbols": [],
            },
        },
        edges=[
            # pkgA/mod1 imports from pkgA/mod2 (intra-package only)
            ("pkgA/mod1.py", "pkgA/mod2.py"),
            # pkgB has no inter-package importers either, but we focus on pkgA
            # having NO imports from pkgB -> pkgA
        ],
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": False,
            "min_confidence": 0.0,
        }
    )

    zombie = [f for f in report.findings if f.kind == DeadCodeKind.ZOMBIE_PACKAGE]
    pkgs = [f.package for f in zombie]
    # Both pkgA and pkgB are zombie since neither has inter-package importers
    assert "pkgA" in pkgs
    assert "pkgB" in pkgs


def test_framework_anchor_counts_as_cross_package_importer():
    """``framework:`` synthetic predecessors should rescue a package from zombie status.

    ``external:`` predecessors don't count (third-party imports), but
    ``framework:`` anchors model real framework-mediated loading
    (e.g. TYPO3 core loading every ``Configuration/*.php``) and should.
    """
    g = _build_graph(
        nodes={
            "Configuration/foo.php": {"is_entry_point": False, "symbol_count": 1, "symbols": []},
            "src/main.php": {"is_entry_point": False, "symbol_count": 1, "symbols": []},
        },
    )
    g.add_node("framework:typo3-core", language="external")
    g.add_edge("framework:typo3-core", "Configuration/foo.php", edge_type="framework")

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": False,
            "min_confidence": 0.0,
        }
    )

    zombie_pkgs = [
        f.package for f in report.findings if f.kind == DeadCodeKind.ZOMBIE_PACKAGE
    ]
    assert "Configuration" not in zombie_pkgs, (
        "framework: predecessors should count as cross-package importers"
    )


def test_framework_anchor_node_not_flagged_as_unreachable():
    """A ``framework:`` synthetic node must not itself be flagged as dead."""
    g = _build_graph(
        nodes={
            "ext_localconf.php": {"is_entry_point": False, "symbol_count": 1, "symbols": []},
        },
    )
    g.add_node("framework:typo3-core", language="external")
    g.add_edge("framework:typo3-core", "ext_localconf.php", edge_type="framework")

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze({"min_confidence": 0.0})

    paths = [f.file_path for f in report.findings]
    assert "framework:typo3-core" not in paths
    # ext_localconf.php should also not be flagged: it has a framework: predecessor.
    assert "ext_localconf.php" not in paths


# ---------------------------------------------------------------------------
# 10. test_whitelist_respected
# ---------------------------------------------------------------------------


def test_whitelist_respected():
    """A file in the whitelist should NOT be flagged even if it is unreachable."""
    g = _build_graph(
        nodes={
            "pkg/legacy.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 20,
                "symbols": [],
            },
        },
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "whitelist": ["pkg/legacy.py"],
        }
    )

    assert all(f.file_path != "pkg/legacy.py" for f in report.findings)


# ---------------------------------------------------------------------------
# 11. test_safe_to_delete_conservative
# ---------------------------------------------------------------------------


def test_safe_to_delete_conservative():
    """safe_to_delete is True only when confidence >= 0.7 AND file does not match dynamic patterns."""
    g = _build_graph(
        nodes={
            # High confidence, no dynamic pattern match -> safe
            "pkg/old_unused.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
            # High confidence, but file stem matches *Handler -> NOT safe
            "pkg/RequestHandler.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
            # Low confidence (recently touched) -> NOT safe
            "pkg/fresh.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
        },
    )

    git_meta = {
        "pkg/old_unused.py": {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=365),
            "age_days": 500,
        },
        "pkg/RequestHandler.py": {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=365),
            "age_days": 500,
        },
        "pkg/fresh.py": {
            "commit_count_90d": 5,
            "last_commit_at": _now() - timedelta(days=3),
            "age_days": 60,
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

    by_path = {f.file_path: f for f in report.findings}
    # High confidence + no dynamic pattern -> safe
    assert by_path["pkg/old_unused.py"].safe_to_delete is True
    # High confidence but matches *Handler -> not safe
    assert by_path["pkg/RequestHandler.py"].safe_to_delete is False
    # Low confidence (0.4) -> not safe
    assert by_path["pkg/fresh.py"].safe_to_delete is False


# ---------------------------------------------------------------------------
# 12. test_report_deletable_lines_sum
# ---------------------------------------------------------------------------


def test_report_deletable_lines_sum():
    """report.deletable_lines should equal the sum of lines for safe_to_delete findings."""
    g = _build_graph(
        nodes={
            "pkg/dead1.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 10,  # lines = 10 * 10 = 100
                "symbols": [],
            },
            "pkg/dead2.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 20,  # lines = 20 * 10 = 200
                "symbols": [],
            },
            "pkg/alive.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 15,  # lines = 15 * 10 = 150, but NOT safe
                "symbols": [],
            },
        },
    )

    git_meta = {
        "pkg/dead1.py": {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=365),
            "age_days": 400,
        },
        "pkg/dead2.py": {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=365),
            "age_days": 400,
        },
        # Recently touched -> confidence 0.4, safe_to_delete=False
        "pkg/alive.py": {
            "commit_count_90d": 5,
            "last_commit_at": _now() - timedelta(days=2),
            "age_days": 60,
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

    safe_findings = [f for f in report.findings if f.safe_to_delete]
    expected_lines = sum(f.lines for f in safe_findings)
    assert report.deletable_lines == expected_lines
    # Verify that the safe findings include the two stale files
    safe_paths = {f.file_path for f in safe_findings}
    assert "pkg/dead1.py" in safe_paths
    assert "pkg/dead2.py" in safe_paths
    assert "pkg/alive.py" not in safe_paths
    # Verify the actual sum
    assert report.deletable_lines == 100 + 200


# ---------------------------------------------------------------------------
# 13. test_dynamic_edge_clamps_unreachable_confidence
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 14. test_find_dynamic_edge_files_handles_subtypes
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 15. test_dynamic_markers_per_language_coverage
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 16. test_unused_internal_default_on
# ---------------------------------------------------------------------------


def test_unused_internal_default_on():
    """detect_unused_internals defaults to True; private symbols with no callers are flagged."""
    g = _build_graph(
        nodes={
            "pkg/utils.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "_helper",
                        "kind": "function",
                        "visibility": "private",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 12,
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
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )

    internals = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL]
    assert any(f.symbol_name == "_helper" for f in internals)
    assert all(f.safe_to_delete is False for f in internals)


# ---------------------------------------------------------------------------
# 17. test_unused_internal_explicit_opt_out
# ---------------------------------------------------------------------------


def test_unused_internal_explicit_opt_out():
    """Setting detect_unused_internals=False disables the detector."""
    g = _build_graph(
        nodes={
            "pkg/utils.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "_helper",
                        "kind": "function",
                        "visibility": "private",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 12,
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
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "detect_unused_internals": False,
            "min_confidence": 0.0,
        }
    )

    internals = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL]
    assert internals == []
