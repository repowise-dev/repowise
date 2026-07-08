"""Performance-coverage accounting — how much of a repo the perf pass actually ran on.

The performance pillar is silent for any language with no registered
``PerfDialect`` (Kotlin, C++, Ruby, PHP, Swift, Scala, C, plain SQL). Such a file
still gets a complexity/NLOC score, so it counts toward the health headline, but
**no perf detector ever runs on it** — the score is a mechanical 10.0 that means
"we never looked," not "your code is fast." On a repo that is mostly an
unsupported language the aggregate perf score is therefore meaningless.

This module turns that blind spot into an honest, surfaced number: given the
analyzed code files and their languages, it reports what fraction of files / LOC
a perf detector was actually *able* to run on, and which unsupported languages
account for the gap. Pure and dependency-free (a language string set in, a
:class:`PerfCoverage` out) so it is trivially unit-testable; the DB loader that
feeds it lives in the persistence layer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from ..complexity.languages import LANGUAGE_MAPS
from .dialects import PERF_DIALECTS


def supported_perf_languages() -> frozenset[str]:
    """The ``LanguageTag`` set the perf pass has a dialect for (its full scope)."""
    return frozenset(PERF_DIALECTS)


class _FileMetricRow(Protocol):
    """The two fields coverage needs off a per-file metric row (duck-typed)."""

    file_path: str
    nloc: int


@dataclass(frozen=True)
class PerfCoverage:
    """What fraction of the analyzed code the performance pass covered.

    ``covered`` = files in a language with a registered perf dialect; ``skipped``
    = analyzed code files in an unsupported language (a silent 10.0). ``pct_loc``
    is the honest headline: perf ran on this share of the analyzed lines.
    """

    analyzed_files: int
    covered_files: int
    covered_nloc: int
    total_nloc: int
    # Unsupported (language, file_count) pairs, most files first.
    unsupported_languages: list[tuple[str, int]] = field(default_factory=list)

    @property
    def skipped_files(self) -> int:
        return self.analyzed_files - self.covered_files

    @property
    def pct_loc(self) -> float:
        """Percent of analyzed NLOC a perf detector ran on (0-100)."""
        if self.total_nloc <= 0:
            return 100.0 if self.analyzed_files == 0 else 0.0
        return round(100.0 * self.covered_nloc / self.total_nloc, 1)

    @property
    def is_partial(self) -> bool:
        """True when some analyzed code was skipped — the score is incomplete."""
        return self.skipped_files > 0


def compute_perf_coverage(
    files: Iterable[tuple[str, int]],
    *,
    supported: frozenset[str] | None = None,
) -> PerfCoverage:
    """Bucket analyzed code files into perf-covered vs skipped.

    *files* is an iterable of ``(language_tag, nloc)`` for every analyzed code
    file (one row per file). *supported* defaults to the registered dialect set;
    pass an explicit set only in tests. Non-code artifacts (markdown, json, …)
    must be filtered out by the caller — this counts every row it is given as an
    analyzed code file.
    """
    tags = supported if supported is not None else supported_perf_languages()
    analyzed = 0
    covered = 0
    covered_nloc = 0
    total_nloc = 0
    unsupported: Counter[str] = Counter()
    for language, nloc in files:
        analyzed += 1
        weight = max(int(nloc), 0)
        total_nloc += weight
        if language in tags:
            covered += 1
            covered_nloc += weight
        else:
            unsupported[language or "unknown"] += 1
    return PerfCoverage(
        analyzed_files=analyzed,
        covered_files=covered,
        covered_nloc=covered_nloc,
        total_nloc=total_nloc,
        unsupported_languages=unsupported.most_common(),
    )


def coverage_for_metrics(
    metrics: Iterable[_FileMetricRow],
    lang_by_path: dict[str, str],
    *,
    supported: frozenset[str] | None = None,
) -> PerfCoverage:
    """Perf coverage over per-file metric rows, restricted to real code.

    The denominator is every metric row whose language the complexity walker
    actually walks (``LANGUAGE_MAPS`` — real code, including the perf-unsupported
    Kotlin/C++), so docs/config rows (markdown/json/yaml) in the metrics table
    never dilute the coverage math. *lang_by_path* maps ``file_path`` → language
    tag (from the graph's file nodes).
    """
    code_rows = [
        (lang_by_path.get(m.file_path, ""), m.nloc)
        for m in metrics
        if lang_by_path.get(m.file_path, "") in LANGUAGE_MAPS
    ]
    return compute_perf_coverage(code_rows, supported=supported)
