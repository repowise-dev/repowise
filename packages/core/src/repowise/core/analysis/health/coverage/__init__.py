"""Coverage parsers + test-file heuristics for the health layer."""

from __future__ import annotations

from .clover import parse_clover
from .cobertura import parse_cobertura
from .detector import detect_format, is_test_file, paired_test_file, parse
from .discovery import (
    CoverageConfig,
    ResolvedCoverage,
    build_coverage_map,
    discover_artifacts,
    normalize_report_path,
    resolve_reports,
)
from .lcov import parse_lcov
from .model import CoverageReport, FileCoverage
from .repowise_json import parse_repowise_json

__all__ = [
    "CoverageConfig",
    "CoverageReport",
    "FileCoverage",
    "ResolvedCoverage",
    "build_coverage_map",
    "detect_format",
    "discover_artifacts",
    "is_test_file",
    "normalize_report_path",
    "paired_test_file",
    "parse",
    "parse_clover",
    "parse_cobertura",
    "parse_lcov",
    "parse_repowise_json",
    "resolve_reports",
]
