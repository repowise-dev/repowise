"""Rust Criterion estimates: one benchmark series per ``estimates.json``.

Criterion writes point estimates in nanoseconds. The optional sibling
``benchmark.json`` names the series when a local source path is available.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..format_v1 import EVIDENCE_FORMAT, EvidenceFormatError, _name

if TYPE_CHECKING:
    from pathlib import Path

_ESTIMATE_KEYS = {"mean", "median", "median_abs_dev", "slope", "std_dev"}
_FALLBACK_NAME = "criterion benchmark"
_MAX_METADATA_BYTES = 64 * 1024


def _json_object(data: bytes) -> dict[str, Any]:
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return {}
    return doc if isinstance(doc, dict) else {}


def sniff(head: bytes) -> bool:
    """Recognize Criterion's five top-level estimate keys, even with a null slope."""
    return _ESTIMATE_KEYS <= _json_object(head).keys()


def to_v1(data: bytes, source_path: Path | None = None) -> dict[str, Any]:
    """Convert one estimates file, taking the series name from local metadata when available."""
    doc = _json_object(data)
    if not _ESTIMATE_KEYS <= doc.keys():
        raise EvidenceFormatError(
            "the file is not Criterion JSON: missing estimate keys",
            "pass the estimates.json written by cargo bench",
        )
    return {
        "format": EVIDENCE_FORMAT,
        "kind": "benchmark",
        "tool": {"name": "criterion"},
        "benchmarks": [{
            "name": _benchmark_name(source_path),
            "unit": "ns",
            "lower_is_better": True,
            "mean": _point_estimate(doc["mean"]),
            "median": _point_estimate(doc["median"]),
            "stddev": _point_estimate(doc["std_dev"]),
        }],
    }


def _point_estimate(estimate: Any) -> Any:
    return estimate.get("point_estimate") if isinstance(estimate, dict) else None


def _benchmark_name(source_path: Path | None) -> str:
    if source_path is None:
        return _FALLBACK_NAME
    metadata = source_path.parent / "benchmark.json"
    try:
        if not metadata.is_file():
            return _FALLBACK_NAME
        with metadata.open("rb") as stream:
            data = stream.read(_MAX_METADATA_BYTES + 1)
    except OSError:
        return _FALLBACK_NAME
    if len(data) > _MAX_METADATA_BYTES:
        return _FALLBACK_NAME
    name, _ = _name(_json_object(data).get("full_id"))
    return name or _FALLBACK_NAME
