"""Criterion ``estimates.json`` (``target/criterion/<group>/<id>/new/``): benchmark estimates, not a profile.

The file holds the estimates Criterion computed for one benchmark — ``mean``,
``median``, ``median_abs_dev``, ``slope`` and ``std_dev``, each a
``{"point_estimate": ..., "confidence_interval": {...}}`` object in
nanoseconds — and nothing else that names the run. Only the point estimates
are read: the confidence intervals are summaries of the same numbers, and
``slope`` may be null when Criterion could not compute one; a null is left
out rather than refused. The benchmark's own name is not in the file: it
lives in the sibling ``benchmark.json`` as ``full_id``, read from the file
path when the caller names one, and a missing or unreadable sibling falls
back to the fixed name ``criterion`` rather than failing the file.

A benchmark id is not a function, so no entry names one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..format_v1 import EVIDENCE_FORMAT, EvidenceFormatError

#: The five estimates Criterion always writes, in the file's key order.
_ESTIMATES = ("mean", "median", "median_abs_dev", "slope", "std_dev")
#: The estimates the v1 document has a field for; ``slope`` is skipped, and
#: ``std_dev`` reaches the v1 document's ``stddev``.
_POINT = (("mean", "mean"), ("median", "median"), ("std_dev", "stddev"))
#: The fixed name used when the sibling ``benchmark.json`` is missing.
_FALLBACK_NAME = "criterion"


def sniff(head: bytes) -> bool:
    try:
        doc = json.loads(head.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return False
    return isinstance(doc, dict) and all(key in doc for key in _ESTIMATES)


def to_v1(data: bytes, name: str | None = None) -> dict[str, Any]:
    """The whole file as a v1 ``benchmark`` document; entries are checked by the v1 parser."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        doc = None
    if not isinstance(doc, dict) or not all(key in doc for key in _ESTIMATES):
        raise EvidenceFormatError(
            "the file is not a Criterion estimates.json: the five estimate keys are missing",
            "point repowise evidence add at target/criterion/<group>/<id>/new/estimates.json",
        )
    return {
        "format": EVIDENCE_FORMAT,
        "kind": "benchmark",
        "tool": {"name": "criterion"},
        "benchmarks": [_entry(doc, name)],
    }


def _entry(doc: dict[str, Any], name: str | None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": _benchmark_name(name),
        "unit": "ns",
        "lower_is_better": True,
    }
    for source, field in _POINT:
        estimate = doc.get(source)
        entry[field] = (
            estimate.get("point_estimate")
            if isinstance(estimate, dict)
            else None
        )
    return entry


def _benchmark_name(name: str | None) -> str:
    """The sibling ``benchmark.json``'s ``full_id``, or the fixed fallback.

    Criterion writes ``benchmark.json`` next to ``estimates.json``; a missing
    or unreadable sibling, or one without a string ``full_id``, falls back.
    """
    if not name:
        return _FALLBACK_NAME
    sibling = Path(name).parent / "benchmark.json"
    try:
        doc = json.loads(sibling.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return _FALLBACK_NAME
    full_id = doc.get("full_id") if isinstance(doc, dict) else None
    return full_id if isinstance(full_id, str) and full_id else _FALLBACK_NAME


__all__ = ["sniff", "to_v1"]
