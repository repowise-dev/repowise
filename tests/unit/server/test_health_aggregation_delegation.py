"""REST and MCP read the health folds from one owner, and the wire is unchanged.

Both surfaces used to carry their own copy of the module rollup, and the REST
package its own copy of the breakdown, the record adapter and — in a second
router — the severity fold. The copies drifted in ways nothing could see: the
two rollups disagreed on what to do with a zero-weight bucket, and neither broke
a tie the same way twice.

The key sets below are the pre-existing payload contract. They are asserted
literally because a delegating alias makes a dropped or renamed key invisible at
the call site — the route builds a plain dict, so nothing else would catch it.
"""

from __future__ import annotations

from repowise.core.analysis.health import aggregation as core
from repowise.server.mcp_server.tool_health import _module_rollups as mcp_module_rollups
from repowise.server.routers.code_health import (
    _finding_base_deduction,
    _score_breakdown_from_findings,
)
from repowise.server.routers.code_health.overview_routes import (
    biomarker_breakdown as _biomarker_breakdown,
)
from repowise.server.routers.code_health.overview_routes import (
    module_rollups as _module_rollups,
)
from repowise.server.routers.code_health.overview_routes import (
    severity_breakdown as _severity_breakdown,
)

_METRICS = [
    {"file_path": "api/a.py", "score": 3.0, "nloc": 100, "module": "api"},
    {"file_path": "api/b.py", "score": 7.0, "nloc": 100, "module": "api"},
    {"file_path": "web/c.py", "score": 9.0, "nloc": 40, "module": "web"},
]
_FINDINGS = [
    {"biomarker_type": "god_class", "severity": "critical", "health_impact": 1.5, "details": {}},
    {"biomarker_type": "god_class", "severity": "low", "health_impact": 0.5, "details": {}},
]


def test_the_repo_overview_shares_the_severity_fold_too() -> None:
    """A fourth copy of this lived in ``routers/overview.py``."""
    from repowise.server.routers.overview import health_severity_breakdown

    assert health_severity_breakdown is core.severity_breakdown


def test_both_surfaces_resolve_to_the_one_owner() -> None:
    assert _module_rollups is mcp_module_rollups is core.module_rollups
    assert _severity_breakdown is core.severity_breakdown
    assert _biomarker_breakdown is core.biomarker_breakdown
    assert _score_breakdown_from_findings is core.score_breakdown
    assert _finding_base_deduction is core.finding_base_deduction


def test_the_module_rollup_row_keys_are_unchanged() -> None:
    rows = _module_rollups(_METRICS)
    assert [set(row) for row in rows] == [
        {
            "module",
            "file_count",
            "nloc",
            "average_health",
            "worst_performer_path",
            "worst_performer_score",
        }
    ] * len(rows)


def test_the_severity_breakdown_keys_are_unchanged() -> None:
    assert set(_severity_breakdown(_FINDINGS)) == {"critical", "high", "medium", "low"}


def test_the_biomarker_breakdown_row_keys_are_unchanged() -> None:
    row = _biomarker_breakdown(_FINDINGS)[0]
    assert set(row) == {"biomarker_type", "critical", "high", "medium", "low", "total"}


def test_the_score_breakdown_keys_are_unchanged() -> None:
    out = _score_breakdown_from_findings(_FINDINGS)
    assert set(out) == {"score", "total_deduction", "categories"}
    category = out["categories"][0]
    assert set(category) == {
        "category",
        "cap",
        "raw_deduction",
        "applied_deduction",
        "capped",
        "finding_count",
        "findings",
    }
    assert set(category["findings"][0]) == {
        "id",
        "biomarker_type",
        "severity",
        "raw_impact",
        "applied_impact",
        "function_name",
        "reason",
    }
