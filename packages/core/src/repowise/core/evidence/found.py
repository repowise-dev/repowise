"""Profiles and benchmark results already on disk, waiting to be connected.

The same bounded walk coverage discovery uses (``discover_artifacts``), over
the names these tools write by default, then each candidate's first bytes
through the same sniffers ``evidence add`` reads them with, so a file listed
here is one ``evidence add`` would take.
"""

from __future__ import annotations

from pathlib import Path

#: Where the supported tools write by default: pytest-benchmark's autosave
#: directory, speedscope and Chrome profiles, pprof output.
EVIDENCE_GLOBS: tuple[str, ...] = (
    ".benchmarks/**/*.json",
    "**/*.speedscope.json",
    "**/*.cpuprofile",
    "**/*.pprof",
    "**/*.pb.gz",
)

#: Candidates sniffed; enough to name what is there, bounded on a large tree.
_MAX_SNIFFED = 20


def discover_evidence(repo_root: Path) -> list[tuple[Path, str]]:
    """``(path, format)`` for each evidence file found under *repo_root*, in glob order."""
    from repowise.core.analysis.health.coverage import discover_artifacts

    from .profile_formats import SNIFF_BYTES, detect_profile_format

    found: list[tuple[Path, str]] = []
    for path in discover_artifacts(repo_root, globs=EVIDENCE_GLOBS)[:_MAX_SNIFFED]:
        try:
            with path.open("rb") as handle:
                head = handle.read(SNIFF_BYTES)
        except OSError:
            continue
        kind = detect_profile_format(head)
        if kind is not None:
            found.append((path, kind))
    return found


__all__ = ["EVIDENCE_GLOBS", "discover_evidence"]
