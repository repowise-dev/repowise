"""Coverage parsers + test-file heuristics for the health layer."""

from __future__ import annotations

from .clover import parse_clover
from .cobertura import parse_cobertura
from .contexts import (
    parse_contexts_file,
    parse_coverage_sqlite,
    parse_lcov_contexts,
)
from .decay import (
    DRIFT_MIN_MEASURED,
    DRIFT_PCT,
    CoverageDecay,
    decay_for_file,
    decay_since,
    measurement_ref,
)
from .detector import PARSERS, detect_format, is_test_file, paired_test_file, parse
from .discovery import (
    CoverageConfig,
    CoverageProvenance,
    CoverageScope,
    PathGate,
    ResolvedCoverage,
    ResolvedTestCoverage,
    build_coverage_map,
    configured_coverage,
    discover_artifacts,
    expand_report_patterns,
    normalize_report_path,
    resolve_reports,
    resolve_test_reports,
)
from .freshness import FreshnessStatus, coverage_freshness
from .goprofile import parse_go_coverprofile
from .jacoco import parse_jacoco
from .lcov import parse_lcov
from .model import (
    ContextCoverageReport,
    CoverageReport,
    FileCoverage,
    TestCoverage,
    file_coverage,
)
from .repowise_json import parse_repowise_json

__all__ = [
    "DRIFT_MIN_MEASURED",
    "DRIFT_PCT",
    "PARSERS",
    "ContextCoverageReport",
    "CoverageConfig",
    "CoverageDecay",
    "CoverageProvenance",
    "CoverageReport",
    "CoverageScope",
    "FileCoverage",
    "FreshnessStatus",
    "PathGate",
    "ResolvedCoverage",
    "ResolvedTestCoverage",
    "TestCoverage",
    "build_coverage_map",
    "configured_coverage",
    "coverage_freshness",
    "decay_for_file",
    "decay_since",
    "detect_format",
    "discover_artifacts",
    "expand_report_patterns",
    "file_coverage",
    "is_test_file",
    "measurement_ref",
    "normalize_report_path",
    "paired_test_file",
    "parse",
    "parse_clover",
    "parse_cobertura",
    "parse_contexts_file",
    "parse_coverage_sqlite",
    "parse_go_coverprofile",
    "parse_jacoco",
    "parse_lcov",
    "parse_lcov_contexts",
    "parse_repowise_json",
    "resolve_reports",
    "resolve_test_reports",
]
