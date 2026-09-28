"""The coverage map over the wire, with its summary typed.

The summary mirrors ``crud.get_coverage_summary`` field for field, strict so a
key the aggregate gains fails here instead of vanishing from the API. The
per-file, per-module and inferred blocks stay loosely typed: their TypeScript
is hand-written beside the UI that reads them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from repowise.core.analysis.health.coverage.freshness import FreshnessStatus


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoverageReportPaths(_Strict):
    """How the report's own file entries mapped to the repository at ingest."""

    total: int
    matched: int
    #: Named no file in the repository.
    unmatched: int
    #: Tied between several files, so none was chosen.
    ambiguous: int
    #: A capped sample of the unmatched, then ambiguous, paths as the report wrote them.
    unmatched_sample: list[str]


class CoverageSummaryFreshness(_Strict):
    """Whether the coverage was measured at the commit the index describes."""

    status: FreshnessStatus
    #: The indexed commit the measurement is compared against.
    indexed_commit: str | None


class CoverageSummary(_Strict):
    """The repository's stored coverage, aggregated. Zero counts and nulls when none is stored."""

    file_count: int
    covered_lines: int
    total_lines: int
    line_coverage_pct: float | None
    branch_coverage_pct: float | None
    source_format: str | None
    #: Every report format the ingest merged, in report order.
    source_formats: list[str]
    #: Fewer than half the report's paths matched: the figures cover a fragment.
    mapping_partial: bool | None
    ingested_at: str | None
    ingested_commit_sha: str | None
    #: Null when nothing is stored or the ingest predates the record.
    report_paths: CoverageReportPaths | None
    #: Null when nothing is stored.
    freshness: CoverageSummaryFreshness | None


class CoverageResponse(BaseModel):
    """``GET /health/coverage``. ``basis`` is absent when the graph was not consulted."""

    summary: CoverageSummary
    files: list[dict[str, Any]]
    modules: list[dict[str, Any]]
    modules_total: int
    basis: Literal["measured", "inferred", "none"] | None = None
    inferred: dict[str, Any] | None = None
