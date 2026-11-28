"""Vitest JSON (``vitest bench --outputJson=PATH``) as v1 benchmark evidence.

Vitest's full group name includes nested suites. Together with the benchmark
label it names a series in milliseconds. Repeated names receive a suffix
in report order. The label does not identify a function.
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
    entries = [entry for file in files for entry in _entries(file)]
    _disambiguate_names(entries)
    return {
        "format": EVIDENCE_FORMAT,
        "kind": "benchmark",
        "tool": {"name": "vitest-bench"},
        "benchmarks": entries,
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
        "name": f"{group_name} > {name}".strip()
        if isinstance(group_name, str) and group_name.strip() and isinstance(name, str) and name.strip()
        else None,
        "unit": "ms",
        "lower_is_better": True,
        **{stat: benchmark.get(stat) for stat in _STATS},
        "rounds": benchmark.get("sampleCount"),
    }


def _disambiguate_names(entries: list[Any]) -> None:
    """Keep the first occurrence of each name and give later ones free suffixes."""
    named = [entry for entry in entries if isinstance(entry, dict) and entry["name"] is not None]
    reserved = {entry["name"] for entry in named}
    next_suffix: dict[str, int] = {}
    for entry in named:
        name = entry["name"]
        suffix = next_suffix.get(name)
        if suffix is None:
            next_suffix[name] = 2
            continue
        while (candidate := f"{name} ({suffix})") in reserved:
            suffix += 1
        entry["name"] = candidate
        reserved.add(candidate)
        next_suffix[name] = suffix + 1


__all__ = ["sniff", "to_v1"]
