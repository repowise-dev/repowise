"""Java Microbenchmark Harness (JMH) JSON (``-rf json``) as v1 benchmark evidence.

Each JMH result becomes one series value named by its benchmark method FQN and
mode (``com.acme.CartBench.total:avgt``). Parameters, when present, are
appended in alphabetical order (``com.acme.CartBench.total:avgt(size=100)``).

Throughput mode (``thrpt``) measures operations per time unit and is
higher-is-better (``lower_is_better=False``). Latency modes (``avgt``,
``sample``, ``ss``) measure time per operation and are lower-is-better
(``lower_is_better=True``). Time units strip ``/op`` (e.g. ``ns/op`` -> ``ns``).
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..format_v1 import EVIDENCE_FORMAT, EvidenceFormatError

_FIRST_KEY = re.compile(rb'\[\s*\{\s*"(?:jmhVersion|benchmark)"\s*:')


def sniff(head: bytes) -> bool:
    """Recognize a JMH JSON array whose first item has jmhVersion and benchmark."""
    clean = head.lstrip(b"\xef\xbb\xbf").lstrip()
    if not clean.startswith(b"["):
        return False
    if not _FIRST_KEY.match(clean):
        return False
    first_item = clean[:4096].split(b"}", 1)[0]
    return b'"jmhVersion"' in first_item and b'"benchmark"' in first_item


def to_v1(data: bytes) -> dict[str, Any]:
    """The whole file as a v1 ``benchmark`` document; entries are checked by the v1 parser."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        doc = None
    if not isinstance(doc, list) or not doc:
        raise EvidenceFormatError(
            "the file is not JMH JSON: empty benchmark array",
            "write it with java -jar benchmarks.jar -rf json -rff PATH",
        )
    entries: list[dict[str, Any]] = []
    for raw in doc:
        if not isinstance(raw, dict) or not raw.get("benchmark"):
            raise EvidenceFormatError(
                'the file is not JMH JSON: an array item has no "benchmark"',
                "write it with java -jar benchmarks.jar -rf json -rff PATH",
            )
        entries.append(_entry(raw))
    version = str(doc[0].get("jmhVersion")) if doc and doc[0].get("jmhVersion") else None
    tool: dict[str, Any] = {"name": "jmh"}
    if version:
        tool["version"] = version
    return {
        "format": EVIDENCE_FORMAT,
        "kind": "benchmark",
        "tool": tool,
        "benchmarks": entries,
    }


def _unit(raw_unit: Any) -> str | None:
    if not isinstance(raw_unit, str) or not raw_unit.strip():
        return None
    unit = raw_unit.strip()
    if unit.endswith("/op"):
        unit = unit[:-3]
    return unit


def _entry(raw: dict[str, Any]) -> dict[str, Any]:
    benchmark = str(raw["benchmark"]).strip()
    mode = str(raw.get("mode") or "").strip() or None
    params = raw.get("params") if isinstance(raw.get("params"), dict) else None

    name = f"{benchmark}:{mode}" if mode else benchmark
    if params:
        param_str = ",".join(f"{k}={v}" for k, v in sorted(params.items()))
        name = f"{name}({param_str})"

    metric = raw.get("primaryMetric") if isinstance(raw.get("primaryMetric"), dict) else {}
    score = metric.get("score") if isinstance(metric.get("score"), (int, float)) else None
    percentiles = (
        metric.get("scorePercentiles")
        if isinstance(metric.get("scorePercentiles"), dict)
        else {}
    )

    median = (
        percentiles.get("50.0")
        if isinstance(percentiles.get("50.0"), (int, float))
        else score
    )
    mean = score
    min_val = (
        percentiles.get("0.0")
        if isinstance(percentiles.get("0.0"), (int, float))
        else None
    )
    max_val = (
        percentiles.get("100.0")
        if isinstance(percentiles.get("100.0"), (int, float))
        else None
    )

    unit = _unit(metric.get("scoreUnit"))
    lower_is_better = mode != "thrpt"

    iterations = (
        raw.get("measurementIterations")
        if isinstance(raw.get("measurementIterations"), int)
        else None
    )
    raw_data = metric.get("rawData")
    if isinstance(raw_data, list):
        rounds = sum(len(sub) for sub in raw_data if isinstance(sub, list)) or len(raw_data)
    else:
        forks = raw.get("forks")
        rounds = forks if isinstance(forks, int) else None

    return {
        "name": name,
        "function": benchmark,
        "unit": unit,
        "lower_is_better": lower_is_better,
        "mean": mean,
        "median": median,
        "min": min_val,
        "max": max_val,
        "stddev": None,
        "rounds": rounds,
        "iterations": iterations,
    }


__all__ = ["sniff", "to_v1"]
