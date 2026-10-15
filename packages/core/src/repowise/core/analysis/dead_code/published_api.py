"""Public API a repository ships as a package.

A public symbol of a library that is packaged for others is used by code
outside the repository, so no importer inside it says nothing about whether it
is dead. Such a finding is not removed: it is kept below the default review
floor with a line naming the package, so a reader asking for every candidate
still sees it, labelled as published surface rather than as a cleanup item.

Only a build that declares the package counts, never a guess from layout. The
evidence is read by :mod:`repowise.core.analysis.published_packages`, which the
per-symbol ``public_api`` flag reads too, so the two cannot disagree about what
ships. A file belongs to its innermost project, and only that project's own
build decides: a sample or test project nested in a packaged library's folder
is not part of the package.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..published_packages import PUBLISHED_ROOTS, BuildFacts, package_of
from .models import DeadCodeFindingData, DeadCodeKind

#: Where a published symbol lands: under ``RISK_CAP_CONFIDENCE``, so the
#: default report and the stored index leave it out.
PUBLISHED_API_CONFIDENCE = 0.3


def demote_published_api(
    findings: list[DeadCodeFindingData],
    languages: Mapping[str, str],
    facts: BuildFacts,
) -> list[DeadCodeFindingData]:
    """Cap unused exports and unreachable files of a published package.

    *languages* maps each file that may hold published API to its language;
    the caller leaves out an unreachable file that declares no public type.
    Mutates in place and returns the same list.
    """
    roots: dict[str, Mapping[str, str | None]] = {}
    for finding in _published_candidates(findings):
        language = languages.get(finding.file_path)
        reader = PUBLISHED_ROOTS.get(language or "")
        if reader is None:
            continue
        if language not in roots:
            roots[language] = reader(facts)
        package = package_of(finding.file_path, roots[language])
        if package is None:
            continue
        finding.confidence = min(finding.confidence, PUBLISHED_API_CONFIDENCE)
        finding.safe_to_delete = False
        finding.evidence.append(
            f"Public API of the published package '{package}': "
            "code outside this repository can use it"
        )
    return findings


def _published_candidates(findings: Iterable[DeadCodeFindingData]) -> Iterable[DeadCodeFindingData]:
    return (
        f
        for f in findings
        if f.kind in (DeadCodeKind.UNUSED_EXPORT, DeadCodeKind.UNREACHABLE_FILE)
    )
