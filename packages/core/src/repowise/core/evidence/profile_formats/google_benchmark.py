"""Google Benchmark JSON (``--benchmark_format=json``): benchmark results, not a profile.

Each benchmark becomes one series value: its ``name`` (the benchmark and its
arguments, ``BM_Sort/1024``) names the series, and each row carries its own
``time_unit``, which is kept as the series' unit (``ns``, ``us``, ``ms``,
``s``) rather than converted. A plain run's iteration row is the value:
``mean`` is its ``real_time`` and ``iterations`` its repetition count. With
``--benchmark_repetitions`` the tool writes one iteration row per repetition
plus ``aggregate`` rows; the ``median`` and ``stddev`` aggregates become those
fields of the same series, and every other aggregate (``mean``, ``cv``) is
skipped, so a benchmark stays one series however many repetitions ran.

The file carries no source path — a benchmark name is not a function symbol —
so no entry names a function. ``context`` (host and library details) is not
read: the v1 document carries no labels.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..format_v1 import EVIDENCE_FORMAT, EvidenceFormatError

# Google Benchmark writes ``context`` as the first key; the rows follow under
# ``benchmarks``. Only that opening is read: ``context`` is host detail of no
# fixed size, so the match crosses it with a bound and stops at the list.
_OPENING = re.compile(rb'\{\s*"context"\s*:[\s\S]{0,3000}?"benchmarks"\s*:\s*\[')

# The units the tool writes; anything else is left for the v1 parser to refuse.
_UNITS = ("ns", "us", "ms", "s")
# Aggregates the v1 document has a field for; ``mean`` and ``cv`` are skipped.
_AGGREGATES = ("median", "stddev")


def sniff(head: bytes) -> bool:
    return _OPENING.match(head.lstrip(b"\xef\xbb\xbf").lstrip()) is not None


def to_v1(data: bytes) -> dict[str, Any]:
    """The whole file as a v1 ``benchmark`` document; entries are checked by the v1 parser."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        doc = None
    benchmarks = doc.get("benchmarks") if isinstance(doc, dict) else None
    if not isinstance(benchmarks, list):
        raise EvidenceFormatError(
            'the file is not Google Benchmark JSON: no "benchmarks" list',
            "write it with <benchmark binary> --benchmark_format=json",
        )
    entries: dict[str, dict[str, Any]] = {}
    for raw in benchmarks:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            continue
        if raw.get("run_type") == "aggregate":
            aggregate = raw.get("aggregate_name")
            if aggregate not in _AGGREGATES:
                continue
            # An aggregate row is named "<benchmark>/<aggregate>"; the series
            # is the benchmark, the same one its iteration rows named.
            if name.endswith("/" + aggregate):
                name = name[: -(len(aggregate) + 1)]
            _entry(name, entries)[aggregate] = raw.get("real_time")
        else:
            entry = _entry(name, entries)
            unit = raw.get("time_unit")
            if unit in _UNITS:
                entry["unit"] = unit
            entry["mean"] = raw.get("real_time")
            entry["iterations"] = raw.get("iterations")
    return {
        "format": EVIDENCE_FORMAT,
        "kind": "benchmark",
        "tool": {"name": "google-benchmark"},
        "benchmarks": list(entries.values()),
    }


def _entry(name: str, entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The series for *name*, created on first sight in the file's row order."""
    if name not in entries:
        entries[name] = {"name": name, "lower_is_better": True}
    return entries[name]


__all__ = ["sniff", "to_v1"]
