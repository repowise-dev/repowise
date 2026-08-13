"""Unit tests for runtime-load risk factors and effective deletion-readiness.

Covers the issue's core ask: config / bootstrap / database / environment /
script files that escape the never-flag allowlist must be presented as review
candidates, never as safe-to-delete, regardless of git age / confidence.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.analysis.dead_code.risk_factors import (
    RISK_CAP_CONFIDENCE,
    SAFE_CONFIDENCE_THRESHOLD,
    effective_safe_to_delete,
    path_risk_factors,
    risk_evidence,
)
from tests.unit.dead_code._helpers import _build_graph, _old_date


@pytest.mark.parametrize(
    "path,expected",
    [
        # The issue's concrete example.
        ("src/database/environment.db.js", {"database", "environment"}),
        # Filename-token signals.
        ("app/config.py", {"config"}),
        ("lib/settings.ts", {"config"}),
        ("pkg/bootstrap.go", {"bootstrap"}),
        ("server/environment.ts", {"environment"}),
        ("a/b/schema.rb", {"database"}),
        # Directory-segment signals (generic filename).
        ("config/loader.py", {"config"}),
        ("scripts/cleanup.py", {"script"}),
        ("db/connection.py", {"database"}),
        # Runtime-loaded web assets: fetched by URL, never imported. Each
        # filename case sits outside a served directory, so it exercises the
        # filename token rather than inheriting the tag from its parent.
        ("src/sw.js", {"asset"}),
        ("src/firebase-messaging-sw.js", {"asset"}),
        ("src/service-worker.ts", {"asset"}),
        ("src/service_worker.js", {"asset"}),
        # A generic name inside a served directory inherits the tag.
        ("public/analytics.js", {"asset"}),
        ("static/legacy.js", {"asset"}),
        # ...but an ordinary worker module is not a web asset. A bare "worker"
        # token would cap every queue and thread-pool module in the repo.
        ("src/queue_worker.py", set()),
        ("app/worker.ts", set()),
        # src/assets is the bundled-source convention, not a served directory.
        ("src/assets/theme.ts", set()),
        # Ordinary modules — no risk factor.
        ("src/services/user_service.py", set()),
        ("pkg/old_unused.py", set()),
        ("components/Button.tsx", set()),
        ("", set()),
    ],
)
def test_path_risk_factors(path: str, expected: set[str]) -> None:
    assert set(path_risk_factors(path)) == expected


def test_effective_safe_downgrades_risk_file() -> None:
    """A high-confidence config/db file is never effectively safe."""
    # Ordinary high-confidence file → safe.
    assert effective_safe_to_delete(0.9, "src/dead_module.py", stored_safe=True) is True
    # Same confidence, but a database/environment file → not safe.
    assert (
        effective_safe_to_delete(0.9, "src/database/environment.db.js", stored_safe=True) is False
    )


def test_effective_safe_is_monotonic() -> None:
    """Never upgrades: already-unsafe or low-confidence stays unsafe."""
    assert effective_safe_to_delete(0.9, "src/foo.py", stored_safe=False) is False
    assert (
        effective_safe_to_delete(SAFE_CONFIDENCE_THRESHOLD - 0.01, "src/foo.py", stored_safe=True)
        is False
    )
    assert (
        effective_safe_to_delete(SAFE_CONFIDENCE_THRESHOLD, "src/foo.py", stored_safe=True) is True
    )


def test_risk_evidence_text() -> None:
    assert risk_evidence(()) is None
    line = risk_evidence(("database", "environment"))
    assert line is not None
    assert "review before deleting" in line


def test_analyzer_caps_risk_file_even_when_old() -> None:
    """An unreachable database/environment bootstrap file that hasn't been
    touched in a year still surfaces, but capped and not safe-to-delete."""
    g = _build_graph(
        nodes={
            "src/database/environment.db.js": {
                "language": "javascript",
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 8,
                "symbols": [],
            },
            "src/old_module.js": {
                "language": "javascript",
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 8,
                "symbols": [],
            },
        },
    )
    git_meta = {
        "src/database/environment.db.js": {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=400),
            "age_days": 400,
        },
        "src/old_module.js": {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=400),
            "age_days": 400,
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

    risky = by_path["src/database/environment.db.js"]
    assert risky.safe_to_delete is False
    assert risky.confidence <= RISK_CAP_CONFIDENCE
    assert set(risky.risk_factors) == {"database", "environment"}
    assert any("review before deleting" in e for e in risky.evidence)

    # The ordinary file with identical git age is still confidently safe.
    ordinary = by_path["src/old_module.js"]
    assert ordinary.safe_to_delete is True
    assert ordinary.risk_factors == []


def test_analyzer_caps_untouched_service_worker() -> None:
    """A service worker is the worst case for a staleness-driven confidence.

    It is registered by URL, so nothing imports it, and it is written once and
    then correct forever, so it ages into the 1.0 tier. Both halves of the
    evidence point at "delete me" for a file that must not be deleted.
    """
    node = {
        "language": "javascript",
        "is_entry_point": False,
        "is_test": False,
        "is_api_contract": False,
        "symbol_count": 4,
        "symbols": [],
    }
    g = _build_graph(
        nodes={
            "src/sw.js": dict(node),
            "src/old_module.js": dict(node),
        },
    )
    git_meta = {
        path: {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=400),
            "age_days": 400,
        }
        for path in ("src/sw.js", "src/old_module.js")
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

    sw = by_path["src/sw.js"]
    assert sw.safe_to_delete is False
    assert set(sw.risk_factors) == {"asset"}
    # Capped to exactly the cap, which is also the default min_confidence
    # floor across the CLI, REST router and MCP tool, so the finding is
    # demoted to a review candidate but still surfaces.
    assert sw.confidence == RISK_CAP_CONFIDENCE

    assert by_path["src/old_module.js"].safe_to_delete is True


def test_effective_safe_downgrades_persisted_service_worker() -> None:
    """Read-time re-derivation, so an index built before this fix is corrected
    without a re-index."""
    assert effective_safe_to_delete(1.0, "src/sw.js", stored_safe=True) is False
