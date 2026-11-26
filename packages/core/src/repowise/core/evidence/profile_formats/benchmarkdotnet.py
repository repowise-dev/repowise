"""BenchmarkDotNet JSON (``*-report-full.json``) as v1 benchmark evidence.

Each BenchmarkDotNet benchmark becomes one series value named by its
``FullName`` (parameters included) in nanoseconds, lower is better.

The benchmark method's fully-qualified name (``Namespace.Type.Method``) is
stored in ``function`` so it can be resolved to a symbol without parameters.
When ``[MemoryDiagnoser]`` is active, ``BytesAllocatedPerOperation`` is stored
as exact ``bytes_per_op`` metric.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..format_v1 import EVIDENCE_FORMAT, EvidenceFormatError

_FIRST_KEY = re.compile(rb'\{\s*"(?:Title|HostEnvironmentInfo|Benchmarks)"\s*:')


def sniff(head: bytes) -> bool:
    """Recognize a BenchmarkDotNet JSON export with HostEnvironmentInfo and Benchmarks."""
    clean = head.lstrip(b"\xef\xbb\xbf").lstrip()
    if not clean.startswith(b"{"):
        return False
    if not _FIRST_KEY.match(clean):
        return False
    sample = clean[:4096]
    return b'"HostEnvironmentInfo"' in sample and b'"Benchmarks"' in sample


def to_v1(data: bytes) -> dict[str, Any]:
    """The whole file as a v1 ``benchmark`` document; entries are checked by the v1 parser."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        doc = None
    if not isinstance(doc, dict):
        raise EvidenceFormatError(
            "the file is not BenchmarkDotNet JSON: expected a JSON object",
            "write it with BenchmarkDotNet's JsonExporter (e.g. --exporters json)",
        )
    benchmarks = doc.get("Benchmarks")
    if not isinstance(benchmarks, list) or not benchmarks:
        raise EvidenceFormatError(
            'the file is not BenchmarkDotNet JSON: no "Benchmarks" list',
            "write it with BenchmarkDotNet's JsonExporter (e.g. --exporters json)",
        )
    entries: list[dict[str, Any]] = []
    for raw in benchmarks:
        if not isinstance(raw, dict) or not raw.get("Method"):
            raise EvidenceFormatError(
                'the file is not BenchmarkDotNet JSON: a benchmark entry has no "Method"',
                "write it with BenchmarkDotNet's JsonExporter (e.g. --exporters json)",
            )
        entries.append(_entry(raw))

    host = doc.get("HostEnvironmentInfo") if isinstance(doc.get("HostEnvironmentInfo"), dict) else {}
    version = host.get("BenchmarkDotNetVersion")
    tool: dict[str, Any] = {"name": "benchmarkdotnet"}
    if version:
        tool["version"] = str(version)

    return {
        "format": EVIDENCE_FORMAT,
        "kind": "benchmark",
        "tool": tool,
        "benchmarks": entries,
    }


def _function(raw: dict[str, Any]) -> str | None:
    ns = str(raw.get("Namespace") or "").strip()
    tp = str(raw.get("Type") or "").strip()
    m = str(raw.get("Method") or "").strip()
    parts = [p for p in (ns, tp, m) if p]
    return ".".join(parts) if parts else None


def _name(raw: dict[str, Any]) -> str:
    full = raw.get("FullName")
    if isinstance(full, str) and full.strip():
        return full.strip()
    fn = _function(raw)
    params = raw.get("Parameters")
    if isinstance(params, str) and params.strip():
        return f"{fn}({params.strip()})" if fn else params.strip()
    return fn or "benchmark"


def _entry(raw: dict[str, Any]) -> dict[str, Any]:
    stats = raw.get("Statistics") if isinstance(raw.get("Statistics"), dict) else {}
    memory = raw.get("Memory") if isinstance(raw.get("Memory"), dict) else {}

    median = stats.get("Median") if isinstance(stats.get("Median"), (int, float)) else None
    mean = stats.get("Mean") if isinstance(stats.get("Mean"), (int, float)) else None
    min_val = stats.get("Min") if isinstance(stats.get("Min"), (int, float)) else None
    max_val = stats.get("Max") if isinstance(stats.get("Max"), (int, float)) else None
    stddev = (
        stats.get("StandardDeviation")
        if isinstance(stats.get("StandardDeviation"), (int, float))
        else None
    )
    rounds = stats.get("N") if isinstance(stats.get("N"), int) else None

    bytes_allocated = memory.get("BytesAllocatedPerOperation")
    bytes_per_op = (
        bytes_allocated
        if isinstance(bytes_allocated, (int, float))
        else None
    )

    name = _name(raw)
    function = _function(raw)

    return {
        "name": name,
        "function": function,
        "unit": "ns",
        "lower_is_better": True,
        "median": median,
        "mean": mean,
        "min": min_val,
        "max": max_val,
        "stddev": stddev,
        "rounds": rounds,
        "iterations": None,
        "bytes_per_op": bytes_per_op,
    }


__all__ = ["sniff", "to_v1"]
