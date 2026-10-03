"""Coverage parser registry, format auto-detection, and test-file heuristics.

:data:`PARSERS` is the one list of supported report formats: dispatch, the CLI
``--format`` choice and user-facing format lists all read it, so a new parser
is one module plus one registry entry.

``repowise coverage add <path>`` accepts a report without a format flag in
the common case. We sniff the content (never the filename) to decide which
parser to dispatch to.

Sniffing rules (cheap, deterministic), after stripping a UTF-8 BOM:

- Repowise JSON starts with ``{`` and names ``repowise-coverage`` or
  ``line_coverage_pct``.
- LCOV files start with ``TN:`` or ``SF:`` on the first non-blank line
  (and the format is line-oriented).
- Clover XML has a ``<coverage`` root with a ``<project>`` child.
- Cobertura XML has a ``<coverage`` root and a ``<packages>`` child (or
  is referenced by the ``coverage`` DTD).
- Go cover profiles start with ``mode:`` on the first non-blank line.
- JaCoCo XML declares the ``-//JACOCO//DTD`` doctype or has a ``<report``
  root (and no ``<coverage`` element).

If detection is ambiguous, callers can pass ``coverage add --format`` to
override.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from ....test_paths import is_test_related_path
from .clover import parse_clover
from .cobertura import parse_cobertura
from .goprofile import parse_go_coverprofile
from .jacoco import parse_jacoco
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


#: Report format -> parser. Insertion order is the order formats are listed
#: to users.
PARSERS: dict[str, Callable[[str], CoverageReport]] = {
    "lcov": parse_lcov,
    "cobertura": parse_cobertura,
    "clover": parse_clover,
    "repowise-json": parse_repowise_json,
    "go-coverprofile": parse_go_coverprofile,
    "jacoco": parse_jacoco,
}

#: Accepted spellings that are not canonical format names.
_ALIASES = {"json": "repowise-json"}

#: A UTF-8 byte-order mark decodes to this; some Windows tools prepend it.
_BOM = "\ufeff"


_LCOV_LINE_RE = re.compile(r"^(TN|SF|DA|BRDA|LF|LH|BRF|BRH):", re.MULTILINE)


# Sniffers take the BOM-stripped, left-stripped 2048-char sample. The leading
# character already separates JSON (``{``), XML (``<``) and the line formats,
# so only the XML sniffers depend on their relative order.


def _is_repowise_json(sample: str) -> bool:
    # Tagged by ``format`` or recognizable by its per-file coverage keys.
    return sample.startswith("{") and (
        "repowise-coverage" in sample or "line_coverage_pct" in sample
    )


def _is_lcov(sample: str) -> bool:
    return sample.startswith(("TN:", "SF:")) or _LCOV_LINE_RE.match(sample) is not None


def _is_go_coverprofile(sample: str) -> bool:
    return sample.startswith("mode:")


def _is_jacoco_doctype(sample: str) -> bool:
    return sample.startswith("<") and "-//JACOCO//DTD" in sample


def _is_clover(sample: str) -> bool:
    return sample.startswith("<") and "<coverage" in sample and "<project" in sample


def _is_cobertura(sample: str) -> bool:
    return (
        sample.startswith("<")
        and "<coverage" in sample
        and ("<packages" in sample or "line-rate" in sample)
    )


def _is_jacoco_report(sample: str) -> bool:
    return sample.startswith("<") and "<report" in sample and "<coverage" not in sample


#: ``(format, sniffer)`` in priority order; the first match wins.
_SNIFFERS: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("repowise-json", _is_repowise_json),
    ("lcov", _is_lcov),
    ("go-coverprofile", _is_go_coverprofile),
    ("jacoco", _is_jacoco_doctype),
    ("clover", _is_clover),
    ("cobertura", _is_cobertura),
    ("jacoco", _is_jacoco_report),
)


def detect_format(text: str) -> str | None:
    """Return a :data:`PARSERS` key for *text*, or ``None`` when unrecognised."""
    sample = text.lstrip(_BOM).lstrip()[:2048]
    if not sample:
        return None
    return next((fmt for fmt, sniff in _SNIFFERS if sniff(sample)), None)


def parse(text: str, *, format: str | None = None) -> CoverageReport:
    """Parse coverage text, auto-detecting format when not provided."""
    fmt = format or detect_format(text)
    parser = PARSERS.get(_ALIASES.get(fmt, fmt)) if fmt else None
    if parser is None:
        return CoverageReport(source_format="unknown")
    return parser(text.lstrip(_BOM))


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
