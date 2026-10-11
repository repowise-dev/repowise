"""Dynamic-import package clamp: semantics, and the derived set that drives it.

``_make_unreachable_finding`` caps confidence at 0.4 when a dynamic-import file
lives in the same package as the candidate. That used to be a scan over
``_dynamic_import_files`` run once per candidate, constructing a ``Path`` per
inner iteration: O(candidates x dynamic_files). It is now a precomputed set of
parent directories plus one hash lookup.

These tests pin the clamp's semantics (which package matches, which does not,
and the repo-root case) so the rewrite cannot have moved them.
"""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind

from ._helpers import _build_graph, _old_date

_DETECT_UNREACHABLE_ONLY = {
    "detect_unused_exports": False,
    "detect_unused_internals": False,
    "detect_zombie_packages": False,
    "min_confidence": 0.0,
}


def _orphan_graph() -> nx.DiGraph:
    """Two packages, one carrying a dynamic edge and one not."""
    return _build_graph(
        nodes={
            # pkg_a: orphan sits in the SAME package as a dynamic edge target
            "pkg_a/orphan.py": {"symbol_count": 5, "symbols": []},
            "pkg_a/dispatcher.py": {"symbol_count": 3, "symbols": []},
            "pkg_a/handler.py": {"is_entry_point": True, "symbol_count": 3, "symbols": []},
            # pkg_b: orphan sits in a package with NO dynamic edge at all
            "pkg_b/orphan.py": {"symbol_count": 5, "symbols": []},
        },
        edges=[("pkg_a/dispatcher.py", "pkg_a/handler.py", {"edge_type": "dynamic_uses"})],
    )


def _stale_meta(*paths: str) -> dict[str, dict]:
    """Git metadata that scores an unreferenced file at full confidence."""
    return {
        p: {"commit_count_90d": 0, "last_commit_at": _old_date(days=400), "age_days": 500}
        for p in paths
    }


def _unreachable(report, path: str):
    hits = [
        f for f in report.findings if f.file_path == path and f.kind == DeadCodeKind.UNREACHABLE_FILE
    ]
    assert len(hits) == 1, f"expected exactly one unreachable finding for {path}, got {hits}"
    return hits[0]


def test_same_package_dynamic_import_clamps_confidence():
    """An orphan beside a dynamically-reached file stays a review candidate."""
    analyzer = DeadCodeAnalyzer(
        _orphan_graph(),
        git_meta_map=_stale_meta("pkg_a/orphan.py", "pkg_b/orphan.py"),
    )
    report = analyzer.analyze(dict(_DETECT_UNREACHABLE_ONLY))

    finding = _unreachable(report, "pkg_a/orphan.py")
    assert finding.confidence == pytest.approx(0.4)
    assert not finding.safe_to_delete


def test_other_package_is_not_clamped():
    """The discriminating case: the clamp is per-package, not repo-wide.

    A membership test that dropped the directory would clamp every orphan in
    the repo as soon as one dynamic import existed anywhere.
    """
    analyzer = DeadCodeAnalyzer(
        _orphan_graph(),
        git_meta_map=_stale_meta("pkg_a/orphan.py", "pkg_b/orphan.py"),
    )
    report = analyzer.analyze(dict(_DETECT_UNREACHABLE_ONLY))

    finding = _unreachable(report, "pkg_b/orphan.py")
    # Untouched for 400 days, in a package with no dynamic edge: unclamped.
    assert finding.confidence == pytest.approx(1.0)


def test_dynamic_import_dirs_holds_packages_not_files():
    """The lookup key is the parent directory, and only for packages that have one."""
    analyzer = DeadCodeAnalyzer(_orphan_graph(), git_meta_map={})

    assert analyzer._dynamic_import_dirs == {"pkg_a"}


def test_repo_root_dynamic_import_clamps_other_root_files():
    """A dynamic import at the repo root clamps every other root-level orphan.

    ``Path("main.py").parent`` is ``.``, so the root behaves as one package and
    a single root-level dynamic import caps confidence for all its neighbours.
    Surprising enough to pin: it is the case a future rewrite is most likely to
    get wrong, and bare filenames are real node ids in this codebase.
    """
    graph = _build_graph(
        nodes={
            "loader.py": {"symbol_count": 3, "symbols": []},
            "main.py": {"is_entry_point": True, "symbol_count": 3, "symbols": []},
            "orphan.py": {"symbol_count": 5, "symbols": []},
            "pkg_c/orphan.py": {"symbol_count": 5, "symbols": []},
        },
        edges=[("loader.py", "main.py", {"edge_type": "dynamic_uses"})],
    )
    analyzer = DeadCodeAnalyzer(
        graph, git_meta_map=_stale_meta("orphan.py", "pkg_c/orphan.py")
    )
    assert analyzer._dynamic_import_dirs == {"."}

    report = analyzer.analyze(dict(_DETECT_UNREACHABLE_ONLY))

    # Root-level neighbour of the dynamic import: clamped.
    assert _unreachable(report, "orphan.py").confidence == pytest.approx(0.4)
    # A real subpackage is unaffected by the root's dynamic import.
    assert _unreachable(report, "pkg_c/orphan.py").confidence == pytest.approx(1.0)


def test_clamp_reads_the_derived_set_not_the_file_set():
    """The precomputed directory set is what the clamp consults.

    Pins the derived-state contract documented on ``_dynamic_import_dirs``:
    widening ``_dynamic_import_files`` alone must not move a verdict, because
    the lookup no longer walks that collection. This is what fails if the
    per-candidate scan is ever reintroduced.
    """
    graph = _orphan_graph()
    meta = _stale_meta("pkg_a/orphan.py", "pkg_b/orphan.py")

    analyzer = DeadCodeAnalyzer(graph, git_meta_map=meta)
    # A dynamic-import file in pkg_b, registered ONLY in the file set.
    analyzer._dynamic_import_files = analyzer._dynamic_import_files | {"pkg_b/late.py"}

    report = analyzer.analyze(dict(_DETECT_UNREACHABLE_ONLY))

    assert _unreachable(report, "pkg_b/orphan.py").confidence == pytest.approx(1.0)
