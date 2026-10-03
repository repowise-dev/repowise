"""A committed file of accepted documentation drift.

The envelope (version, sorted entries, deterministic bytes, refusals) is the
shared one in :mod:`repowise.core.ci.baseline`; this module supplies the
entries. They are keyed on the line-independent fingerprint, so an accepted
finding stays accepted when an edit above it shifts its line.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from repowise.core.ci.baseline import (
    BASELINE_VERSION,
    BaselineError,
    build_document,
    read_baseline,
    write_document,
)

from .serialize import fingerprint_of

__all__ = [
    "BASELINE_BASIS",
    "BASELINE_VERSION",
    "BaselineError",
    "build_baseline",
    "read_baseline",
    "write_baseline",
]

BASELINE_BASIS = (
    "Documentation drift accepted as known. A listed finding does not fail the "
    "gate; it is keyed on document, kind and target, not line."
)


def build_baseline(findings: Iterable[Mapping[str, Any]]) -> dict:
    """The baseline document for *findings*, one entry per fingerprint."""
    entries = (
        {
            "fingerprint": fingerprint_of(finding),
            "file_path": finding["file_path"],
            "kind": str(finding["kind"]),
            "target": finding["target"],
        }
        for finding in findings
    )
    return build_document(
        entries,
        basis=BASELINE_BASIS,
        sort_key=lambda e: (e["file_path"], e["kind"], e["target"]),
    )


def write_baseline(path: Path | str, findings: Iterable[Mapping[str, Any]]) -> int:
    """Write the baseline for *findings* to *path*; returns entries written."""
    return write_document(path, build_baseline(findings))
