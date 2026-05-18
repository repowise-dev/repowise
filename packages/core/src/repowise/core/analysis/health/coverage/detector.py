"""Format auto-detection + test-file heuristic.

The CLI accepts ``--coverage <path>`` without a format flag in the common
case. We sniff the content to decide which parser to dispatch to.

Sniffing rules (cheap, deterministic):

- LCOV files start with ``TN:`` or ``SF:`` on the first non-blank line
  (and the format is line-oriented).
- Cobertura XML has a ``<coverage`` root and a ``<packages>`` child (or
  is referenced by the ``coverage`` DTD).
- Clover XML has a ``<coverage generated=`` root with a ``<project>``
  child.

If detection is ambiguous, callers can pass ``--coverage-format`` to
override.
"""

from __future__ import annotations

import re
from pathlib import Path

from .clover import parse_clover
from .cobertura import parse_cobertura
from .lcov import parse_lcov
from .model import CoverageReport

# Test-file globs / suffixes — checked against POSIX-normalized paths.
_TEST_PATH_FRAGMENTS = (
    "/test/",
    "/tests/",
    "/__tests__/",
    "/spec/",
    "/specs/",
)
_TEST_FILE_SUFFIXES = (
    "_test.py",
    "_test.go",
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".test.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
    ".spec.jsx",
    "_spec.rb",
    "Test.java",
    "Tests.java",
    "Spec.scala",
)
_TEST_FILE_PREFIXES = ("test_",)

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
    return CoverageReport(source_format="unknown")


def is_test_file(rel_path: str, source: str | None = None) -> bool:
    """Heuristic — does *rel_path* look like a test file?

    *source* is an optional file body; when supplied we additionally
    check for test-framework imports for files whose paths don't match
    obvious conventions.
    """
    p = rel_path.replace("\\", "/")
    lower = p.lower()
    base = p.rsplit("/", 1)[-1]
    if any(frag in lower for frag in _TEST_PATH_FRAGMENTS):
        return True
    if any(lower.startswith(frag.lstrip("/")) for frag in _TEST_PATH_FRAGMENTS):
        return True
    if base.startswith(_TEST_FILE_PREFIXES):
        return True
    if any(base.endswith(sfx) for sfx in _TEST_FILE_SUFFIXES):
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
        f"{stem}.spec.ts",
        f"{stem}.spec.js",
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
