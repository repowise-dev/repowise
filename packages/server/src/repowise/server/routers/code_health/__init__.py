"""/api/repos/{repo_id}/health/* — code-health endpoints.

Distinct from ``routers/health.py`` (liveness / Prometheus). All routes
here require API-key auth and operate on the ``health_findings`` /
``health_file_metrics`` tables.

This package is a pure structural split of the former ``code_health.py``
module: the shared ``router`` lives in ``_router``, route handlers are grouped
by endpoint family, and pure/DB helpers live in leaf modules. The public import
path ``repowise.server.routers.code_health`` still resolves ``router`` plus
every cross-module helper unchanged.
"""

from __future__ import annotations

# import route modules for their decorator side-effects (attach routes to the shared router)
from . import (
    badge,  # noqa: F401
    churn_routes,  # noqa: F401
    coverage_routes,  # noqa: F401
    files_routes,  # noqa: F401
    findings_routes,  # noqa: F401
    overview_routes,  # noqa: F401
    refactoring_routes,  # noqa: F401
    trends_routes,  # noqa: F401
)
from ._router import router
from .badge import _badge_fields, _render_badge_svg  # noqa: F401
from .breakdown import _finding_base_deduction, _score_breakdown_from_findings  # noqa: F401
from .overview_routes import _resolve_last_indexed_at  # noqa: F401

# re-export helpers imported by routers/files.py + tests (keep old import path working)
from .serializers import (  # noqa: F401
    _churn_complexity_to_dict,
    _file_signals_to_dict,
    _file_trend_to_dict,
    _finding_to_dict,
    _leads_by_file,
    _metric_to_dict,
    _primary_and_magnitude,
)

__all__ = ["router"]
