"""Hidden Coupling — files that change together but don't import each other.

Joins two existing signals: ``co_change_partners_json`` (from the git
indexer) and the file-level import edges in the dependency graph. A
high correlation between commits of files A and B that have no static
dependency between them captures behavioral coupling invisible to a
pure type/import analyzer — shared protocols, parallel config, hidden
test fixtures, copy-pasted constants.

Fires when:

- ``commit_count_total`` for both files is at or above the noise floor
- ``co_change_count(A, B) / min(total_A, total_B) >= 0.5``
- there is **no** ``imports`` edge in either direction
- the pair is not a test ↔ production pairing (those are expected to
  co-change)

Tier-aware: when ``co_change_partners_json`` is empty (ESSENTIAL git
tier, plan §1.2.1) the detector short-circuits to zero findings. The
empty short-circuit is explicit so backfill behavior is testable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import Severity
from .base import BiomarkerResult, FileContext

_MIN_COMMITS = 5
_MIN_CORRELATION = 0.5
_HIGH_THRESHOLD = 0.65
_CRITICAL_THRESHOLD = 0.8
_MAX_FINDINGS_PER_FILE = 3

# Same conventions used by engine._has_paired_test_file. Keeping them
# inline (rather than importing) avoids a circular dependency between
# the engine and the biomarker package.
_TEST_BASENAME_PATTERNS: tuple[str, ...] = (
    "test_",  # prefix
)
_TEST_SUFFIXES: tuple[str, ...] = (
    "_test.py",
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".spec.ts",
    ".spec.js",
    "_test.go",
)
_TEST_DIR_FRAGMENTS: tuple[str, ...] = (
    "/test/",
    "/tests/",
    "/__tests__/",
)


def _is_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    if any(frag in f"/{p}" for frag in _TEST_DIR_FRAGMENTS):
        return True
    base = Path(p).name
    if base.startswith(_TEST_BASENAME_PATTERNS):
        return True
    return any(p.endswith(suf) for suf in _TEST_SUFFIXES)


def _parse_partners(meta: dict[str, Any]) -> dict[str, int]:
    raw = meta.get("co_change_partners_json")
    if not raw:
        return {}
    try:
        partners = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    out: dict[str, int] = {}
    for p in partners:
        if not isinstance(p, dict):
            continue
        path = p.get("file_path") or p.get("path")
        count = p.get("co_change_count") or p.get("count") or 0
        if not path:
            continue
        try:
            out[str(path)] = int(count)
        except (TypeError, ValueError):
            continue
    return out


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _severity_for(correlation: float) -> Severity:
    if correlation >= _CRITICAL_THRESHOLD:
        return Severity.CRITICAL
    if correlation >= _HIGH_THRESHOLD:
        return Severity.HIGH
    return Severity.MEDIUM


class HiddenCouplingDetector:
    name = "hidden_coupling"
    category = "organizational"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        meta = ctx.git_meta or {}
        partners = _parse_partners(meta)
        # Explicit ESSENTIAL-tier short-circuit (plan §1.2.1).
        if not partners:
            return []

        total_self = _as_int(meta.get("commit_count_total"))
        if total_self < _MIN_COMMITS:
            return []

        self_is_test = _is_test_path(ctx.file_path)
        graph = ctx.graph_view
        counts = ctx.repo_commit_counts or {}

        candidates: list[tuple[float, str, int]] = []
        for partner_path, co_count in partners.items():
            if partner_path == ctx.file_path:
                continue
            partner_total = counts.get(partner_path, 0)
            if partner_total < _MIN_COMMITS:
                continue
            denom = min(total_self, partner_total)
            if denom <= 0:
                continue
            correlation = co_count / denom
            if correlation < _MIN_CORRELATION:
                continue
            # Skip test ↔ production pairings — expected to co-change.
            if self_is_test ^ _is_test_path(partner_path):
                continue
            # Skip when an explicit import edge already documents the
            # coupling.
            if graph is not None and (
                graph.has_edge(ctx.file_path, partner_path, "imports")
                or graph.has_edge(partner_path, ctx.file_path, "imports")
            ):
                continue
            candidates.append((correlation, partner_path, co_count))

        if not candidates:
            return []

        candidates.sort(key=lambda t: t[0], reverse=True)
        capped = candidates[:_MAX_FINDINGS_PER_FILE]

        findings: list[BiomarkerResult] = []
        for correlation, partner_path, co_count in capped:
            findings.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=_severity_for(correlation),
                    function_name=None,
                    line_start=None,
                    line_end=None,
                    details={
                        "partner": partner_path,
                        "correlation": round(correlation, 3),
                        "co_change_count": co_count,
                        "self_commits": total_self,
                        "partner_commits": counts.get(partner_path, 0),
                    },
                    reason=(
                        f"{partner_path} co-changes with this file "
                        f"{co_count} times ({correlation:.0%} of shared "
                        "commits) but no static dependency exists"
                    ),
                )
            )
        return findings


BIOMARKER = HiddenCouplingDetector()
