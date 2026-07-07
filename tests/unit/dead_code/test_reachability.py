"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from tests.unit.dead_code._helpers import _build_graph


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

    finding = next(f for f in unreachable if f.file_path == "pkg/orphan.py")
    assert finding.start_line is None
    assert finding.end_line is None


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


def test_entry_point_names_never_flagged():
    """Runtime entry points (wWinMain, TEST_METHOD, DllInstall…) are
    never flagged regardless of incoming-edge counts. The Win32 / COM /
    MSTest runners reach them by mechanism, not by a `using` import."""
    g = _build_graph(
        nodes={
            "src/main.cpp": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 3,
                "language": "cpp",
                "symbols": [
                    {
                        "name": "wWinMain",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 1,
                    },
                    {
                        "name": "TEST_METHOD",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 12,
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
    names = [f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
    assert "wWinMain" not in names
    assert "TEST_METHOD" not in names


def test_nested_function_not_flagged():
    """Symbols defined inside another function's body cannot be imported by
    name and must not be flagged as unused exports."""
    g = _build_graph(
        nodes={
            "pkg/server.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "symbols": [
                    {
                        "name": "chat",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 10,
                        "end_line": 80,
                    },
                    # Nested closure / inner generator — line range strictly
                    # contained inside ``chat``.
                    {
                        "name": "event_stream",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 25,
                        "end_line": 60,
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
    assert "event_stream" not in sym_names


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

    zombie_pkgs = [f.package for f in report.findings if f.kind == DeadCodeKind.ZOMBIE_PACKAGE]
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
