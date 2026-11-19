"""Google Benchmark JSON (``--benchmark_format=json``): benchmark results, not a profile.

Each benchmark row becomes one series value named by its ``name``
(``BM_Sort/1024``), timed by the row's own ``time_unit``. A plain run
carries ``mean`` from ``real_time`` and its ``iterations``; a run with
``--benchmark_repetitions`` repeats the rows under the same name and adds
``median`` and ``stddev`` aggregate rows, which are folded into the entry
so no series is duplicated. The file carries no source path, so no entry
claims a function: the row names the benchmark and never a production
symbol. ``context`` holds host details the v1 document does not read.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..format_v1 import EVIDENCE_FORMAT, EvidenceFormatError

# Google Benchmark writes ``context`` as the first key of its object.
_FIRST_KEY = re.compile(rb'\{\s*"context"\s*:')

#: The aggregate rows a repetition run writes, and the field each fills.
_AGGREGATES = {"mean": "mean", "median": "median", "stddev": "stddev"}


def sniff(head: bytes) -> bool:
    return _FIRST_KEY.match(head.lstrip(b"\xef\xbb\xbf").lstrip()) is not None


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
            "write it with --benchmark_format=json",
        )
    entries: dict[str, dict[str, Any]] = {}
    for raw in benchmarks:
        if not isinstance(raw, dict):
            raise EvidenceFormatError(
                'the file is not Google Benchmark JSON: a "benchmarks" row is not an object',
                "write it with --benchmark_format=json",
            )
        _row(entries, raw)
    return {
        "format": EVIDENCE_FORMAT,
        "kind": "benchmark",
        "tool": {"name": "google-benchmark"},
        "benchmarks": list(entries.values()),
    }


def _row(entries: dict[str, dict[str, Any]], raw: dict[str, Any]) -> None:
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise EvidenceFormatError(
            'the file is not Google Benchmark JSON: a benchmark row has no "name"',
            "write it with --benchmark_format=json",
        )
    if raw.get("run_type") == "aggregate":
        # The aggregates the v1 entry carries; ``cv`` adds nothing the
        # stddev does not. An aggregate row's name is the benchmark's
        # with the aggregate appended (``BM_Sort/1024_median``), so the
        # suffix names the series to fill. The ``mean`` aggregate is the
        # run's own summary: it wins over any iteration row, and its
        # ``iterations`` is the repetition count.
        aggregate = str(raw.get("aggregate_name"))
        field = _AGGREGATES.get(aggregate)
        value = raw.get("real_time")
        base = name[: len(name) - len(aggregate) - 1] if name.endswith("_" + aggregate) else name
        if field is not None and isinstance(value, (int, float)):
            entry = entries.setdefault(
                base,
                _entry(base, raw),
            )
            entry[field] = value
            if isinstance(raw.get("iterations"), (int, float)):
                entry["iterations"] = raw["iterations"]
        return
    entry = entries.setdefault(name, _entry(name, raw))
    # A plain run carries one timing per name, so its row fills ``mean``; a
    # repetition run's iteration rows never overwrite what an earlier row (or
    # the ``mean`` aggregate below them) already stored.
    if isinstance(raw.get("real_time"), (int, float)) and "mean" not in entry:
        entry["mean"] = raw["real_time"]
    if isinstance(raw.get("iterations"), (int, float)) and "iterations" not in entry:
        entry["iterations"] = raw["iterations"]
    if isinstance(raw.get("time_unit"), str):
        entry["unit"] = raw["time_unit"]


def _entry(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "unit": raw.get("time_unit") if isinstance(raw.get("time_unit"), str) else "ns",
        "lower_is_better": True,
    }


__all__ = ["sniff", "to_v1"]
