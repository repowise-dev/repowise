"""Unit tests for zombie-package detection in DeadCodeAnalyzer.

Tests cover both basic detection (a package with no inter-package importers
is flagged) and git-metadata propagation into zombie-package findings
(commit_count_90d, last_commit_at, primary_owner).

Regression: _detect_zombie_packages previously used ``getattr(gm, ...)``
on plain ``dict`` values from ``git_meta_map``. ``getattr`` on a dict never
resolves string keys and always returns the default, so all git-activity
fields were silently zeroed on every zombie-package finding. The fix
replaces every ``getattr`` with ``dict.get()``, consistent with every other
callsite in analyzer.py.
"""

from __future__ import annotations

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from tests.unit.dead_code._helpers import _build_graph, _old_date


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
    # file_path *is* the package on a zombie finding; the separate `package`
    # column duplicated it and was dropped.
    pkgs = [f.file_path for f in zombie]
    # Both pkgA and pkgB are zombie since neither has inter-package importers
    assert "pkgA" in pkgs
    assert "pkgB" in pkgs


# ---------------------------------------------------------------------------
# Regression tests: git-metadata propagation into zombie-package findings.
#
# Before the fix, _detect_zombie_packages used getattr(gm, "field", default)
# on plain dict values. getattr on a dict never resolves string keys, so
# every git-activity field (commit_count_90d, last_commit_at, primary_owner)
# was silently set to 0 / None regardless of what git_meta_map contained.
# ---------------------------------------------------------------------------


def _zombie_graph() -> object:
    """Minimal graph where pkgA has only intra-package imports (= zombie).

    A second package (pkgB) is required so that ``_detect_zombie_packages``
    sees at least two top-level packages and does not short-circuit at the
    ``len(packages) < 2`` guard.  pkgB has no edges into pkgA, so pkgA
    remains a zombie candidate.
    """
    return _build_graph(
        nodes={
            "pkgA/alpha.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 4,
                "symbols": [],
            },
            "pkgA/beta.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "symbols": [],
            },
            # Sentinel: gives the analyzer a second top-level package so the
            # len(packages) < 2 early-return guard is not triggered.
            "pkgB/sentinel.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [],
            },
        },
        edges=[
            # Intra-package import only — pkgA is still a zombie
            ("pkgA/alpha.py", "pkgA/beta.py"),
        ],
    )



def _run_zombie_only(git_meta: dict) -> list:
    """Run the analyzer with zombie detection only and return pkgA zombie findings.

    Filters to ``file_path == "pkgA"`` because the pkgB sentinel node is also
    flagged as a zombie (it has no inbound inter-package edges either), and
    each regression test wants to inspect only the package under test.
    """
    analyzer = DeadCodeAnalyzer(_zombie_graph(), git_meta_map=git_meta)
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": False,
            "detect_unused_internals": False,
            # zombie_packages defaults to True — the only detector we want
            "min_confidence": 0.0,
        }
    )
    return [
        f
        for f in report.findings
        if f.kind == DeadCodeKind.ZOMBIE_PACKAGE and f.file_path == "pkgA"
    ]


def test_zombie_package_primary_owner_from_git_meta():
    """primary_owner on a zombie finding is taken from git_meta_map.

    The owner with the most contributions across files in the package wins.
    Before the fix, getattr(gm, "primary_owner_name", None) on a dict always
    returned None, so primary_owner was always None.
    """
    git_meta = {
        # alpha.py is owned by Alice (who also has more commits)
        "pkgA/alpha.py": {
            "commit_count_90d": 8,
            "last_commit_at": None,
            "primary_owner_name": "alice@example.com",
        },
        # beta.py is owned by Bob with fewer commits
        "pkgA/beta.py": {
            "commit_count_90d": 2,
            "last_commit_at": None,
            "primary_owner_name": "bob@example.com",
        },
    }

    findings = _run_zombie_only(git_meta)

    assert len(findings) == 1
    finding = findings[0]
    # alice@example.com owns the majority of files -> should win
    assert finding.primary_owner == "alice@example.com", (
        f"Expected primary_owner='alice@example.com', got {finding.primary_owner!r}. "
        "Likely caused by getattr() being used instead of dict.get() on git_meta_map values."
    )


def test_zombie_package_commit_count_summed_from_git_meta():
    """commit_count_90d on a zombie finding is the sum across all files in the package.

    Before the fix, getattr(gm, "commit_count_90d", 0) on a dict always returned
    0, so commit_count_90d was always 0 regardless of git history.
    """
    git_meta = {
        "pkgA/alpha.py": {
            "commit_count_90d": 5,
            "last_commit_at": None,
            "primary_owner_name": None,
        },
        "pkgA/beta.py": {
            "commit_count_90d": 7,
            "last_commit_at": None,
            "primary_owner_name": None,
        },
    }

    findings = _run_zombie_only(git_meta)

    assert len(findings) == 1
    finding = findings[0]
    # 5 + 7 = 12 total commits in last 90 days across the package
    assert finding.commit_count_90d == 12, (
        f"Expected commit_count_90d=12, got {finding.commit_count_90d}. "
        "Likely caused by getattr() being used instead of dict.get() on git_meta_map values."
    )



def test_zombie_package_last_commit_at_from_git_meta():
    """last_commit_at on a zombie finding is the most recent commit across all files.

    Before the fix, getattr(gm, "last_commit_at", None) on a dict always returned
    None, so last_commit_at was always None and age_days was never computed.
    """
    older = _old_date(days=200)
    newer = _old_date(days=30)

    git_meta = {
        "pkgA/alpha.py": {
            "commit_count_90d": 1,
            "last_commit_at": older,
            "primary_owner_name": None,
        },
        "pkgA/beta.py": {
            "commit_count_90d": 1,
            "last_commit_at": newer,
            "primary_owner_name": None,
        },
    }

    findings = _run_zombie_only(git_meta)

    assert len(findings) == 1
    finding = findings[0]
    # The most recent commit (newer) should be selected
    assert finding.last_commit_at == newer, (
        f"Expected last_commit_at={newer!r}, got {finding.last_commit_at!r}. "
        "Likely caused by getattr() being used instead of dict.get() on git_meta_map values."
    )
    # age_days is derived from last_commit_at; it should be ~30 days, not None
    assert finding.age_days is not None, (
        "age_days should not be None when last_commit_at is available."
    )
    assert 25 <= finding.age_days <= 35, (  # 5-day slack for test runtime
        f"Expected age_days ~30, got {finding.age_days}."
    )
