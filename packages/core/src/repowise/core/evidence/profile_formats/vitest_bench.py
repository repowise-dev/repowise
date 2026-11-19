"""Vitest JSON (``vitest bench --outputJson=PATH``) as v1 benchmark evidence.

Vitest's full group name includes nested suites. Together with the benchmark
label it names a series in milliseconds. The label does not identify a function.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from ..format_v1 import EVIDENCE_FORMAT, EvidenceFormatError

# The reporter writes "files" first and "filepath" first in each file.
_FIRST_FILE = re.compile(rb'\{\s*"files"\s*:\s*\[\s*\{\s*"filepath"\s*:')
_STATS = ("mean", "median", "min", "max")


def sniff(head: bytes) -> bool:
    """Recognize the opening of a Vitest benchmark report."""
    return _FIRST_FILE.match(head.removeprefix(b"\xef\xbb\xbf").lstrip()) is not None


def to_v1(data: bytes) -> dict[str, Any]:
    """Convert the report to a v1 benchmark document."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except ValueError:
        doc = None
    files = doc.get("files") if isinstance(doc, dict) else None
    if not isinstance(files, list):
        raise EvidenceFormatError(
            'the file is not Vitest benchmark JSON: no "files" list',
            "write it with vitest bench --outputJson=PATH",
        )
    return {
        "format": EVIDENCE_FORMAT,
        "kind": "benchmark",
        "tool": {"name": "vitest-bench"},
        "benchmarks": [entry for file in files for entry in _entries(file)],
    }


def _items(parent: Any, key: str) -> list[Any]:
    children = parent.get(key) if isinstance(parent, dict) else None
    return children if isinstance(children, list) else []


def _entries(file: Any) -> Iterator[Any]:
    for group in _items(file, "groups"):
        for benchmark in _items(group, "benchmarks"):
            yield _entry(group.get("fullName"), benchmark) if isinstance(benchmark, dict) else benchmark


def _entry(group_name: Any, benchmark: dict[str, Any]) -> dict[str, Any]:
    name = benchmark.get("name")
    return {
        "name": f"{group_name} > {name}"
        if isinstance(group_name, str) and group_name.strip() and isinstance(name, str) and name.strip()
        else None,
        "unit": "ms",
        "lower_is_better": True,
        **{stat: benchmark.get(stat) for stat in _STATS},
        "rounds": benchmark.get("sampleCount"),
    }


__all__ = ["sniff", "to_v1"]
