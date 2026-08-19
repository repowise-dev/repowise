"""Format auto-detection + test-file heuristic.

``repowise coverage add <path>`` accepts a report without a format flag in
the common case. We sniff the content to decide which parser to dispatch to.

Sniffing rules (cheap, deterministic):

- LCOV files start with ``TN:`` or ``SF:`` on the first non-blank line
  (and the format is line-oriented).
- Cobertura XML has a ``<coverage`` root and a ``<packages>`` child (or
  is referenced by the ``coverage`` DTD).
- Clover XML has a ``<coverage generated=`` root with a ``<project>``
  child.

If detection is ambiguous, callers can pass ``coverage add --format`` to
override.
"""

from __future__ import annotations

import re
from pathlib import Path

from ....test_paths import is_test_related_path
from .clover import parse_clover
from .cobertura import parse_cobertura
from .lcov import parse_lcov
from .model import CoverageReport
from .repowise_json import parse_repowise_json

# Test framework import patterns — used when the path heuristic is
# inconclusive. Detected via cheap substring scan (no tree-sitter pass).
_FRAMEWORK_IMPORTS = (
    "import pytest",
    "from pytest",
    "import unittest",
    "from unittest",
    "from django.test",
    "import jest",
    'from "vitest"',
    "from 'vitest'",
    'from "@testing-library/',
    "from '@jest/",
    "testing.T",  # Go testing pkg signature
)


def detect_format(text: str) -> str | None:
    """Return ``"lcov" | "cobertura" | "clover"`` or ``None``."""
    sample = text.lstrip()[:2048]
    if not sample:
        return None
    if sample.startswith("{"):
        # Repowise normalized JSON — tagged by ``format`` or recognizable by its
        # per-file coverage keys. Checked before LCOV/XML since none start with ``{``.
        if "repowise-coverage" in sample or "line_coverage_pct" in sample:
            return "repowise-json"
        return None
    if sample.startswith(("TN:", "SF:")) or _LCOV_LINE_RE.match(sample):
        return "lcov"
    if sample.startswith("<?xml") or sample.startswith("<"):
        if "<coverage" in sample and "<project" in sample[:4096]:
            return "clover"
        if "<coverage" in sample and ("<packages" in sample[:4096] or "line-rate" in sample[:4096]):
            return "cobertura"
    return None


_LCOV_LINE_RE = re.compile(r"^(TN|SF|DA|BRDA|LF|LH|BRF|BRH):", re.MULTILINE)


def parse(text: str, *, format: str | None = None) -> CoverageReport:
    """Parse coverage text, auto-detecting format when not provided."""
    fmt = format or detect_format(text)
    if fmt == "lcov":
        return parse_lcov(text)
    if fmt == "cobertura":
        return parse_cobertura(text)
    if fmt == "clover":
        return parse_clover(text)
    if fmt in ("repowise-json", "json"):
        return parse_repowise_json(text)
    return CoverageReport(source_format="unknown")


def is_test_file(rel_path: str, source: str | None = None) -> bool:
    """Does *rel_path* look like a test file?

    The path rules live in :mod:`repowise.core.test_paths`, the one home for
    them (#1103). What this adds is the *source* branch: given a file body, a
    test-framework import marks a test whose path follows no convention at all,
    which no amount of path matching can tell you.
    """
    if is_test_related_path(rel_path):
        return True
    if source:
        head = source[:4096]
        if any(token in head for token in _FRAMEWORK_IMPORTS):
            return True
    return False


def paired_test_file(rel_path: str, all_paths: set[str]) -> str | None:
    """Return a candidate test-file path for *rel_path*, or ``None``."""
    p = Path(rel_path.replace("\\", "/"))
    stem = p.stem
    candidates = {
        f"test_{stem}.py",
        f"{stem}_test.py",
        f"{stem}.test.ts",
        f"{stem}.test.tsx",
        f"{stem}.test.js",
        f"{stem}.test.jsx",
        f"{stem}.test.mts",
        f"{stem}.test.cts",
        f"{stem}.spec.ts",
        f"{stem}.spec.js",
        f"{stem}.spec.mts",
        f"{stem}.spec.cts",
        f"{stem}_test.go",
        f"{stem}_spec.rb",
        f"{stem}Test.java",
        f"{stem}Tests.java",
    }
    for other in all_paths:
        norm = other.replace("\\", "/")
        base = norm.rsplit("/", 1)[-1]
        if base in candidates:
            return other
    return None
