"""Coverage parsers + test-file heuristics for the health layer."""

from __future__ import annotations

from .clover import parse_clover
from .cobertura import parse_cobertura
from .contexts import (
    parse_contexts_file,
    parse_coverage_sqlite,
    parse_lcov_contexts,
)
from .detector import detect_format, is_test_file, paired_test_file, parse
from .discovery import (
    CoverageConfig,
    ResolvedCoverage,
    ResolvedTestCoverage,
    build_coverage_map,
    discover_artifacts,
    normalize_report_path,
    resolve_reports,
    resolve_test_reports,
)
from .lcov import parse_lcov
from .model import (
    ContextCoverageReport,
    CoverageReport,
    FileCoverage,
    TestCoverage,
)
from .repowise_json import parse_repowise_json

__all__ = [
    "ContextCoverageReport",
    "CoverageConfig",
    "CoverageReport",
    "FileCoverage",
    "ResolvedCoverage",
    "ResolvedTestCoverage",
    "TestCoverage",
    "build_coverage_map",
    "detect_format",
    "discover_artifacts",
    "is_test_file",
    "normalize_report_path",
    "paired_test_file",
    "parse",
    "parse_clover",
    "parse_cobertura",
    "parse_contexts_file",
    "parse_coverage_sqlite",
    "parse_lcov",
    "parse_lcov_contexts",
    "parse_repowise_json",
    "resolve_reports",
    "resolve_test_reports",
]
