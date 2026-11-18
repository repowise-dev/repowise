"""hyperfine JSON (``hyperfine --export-json PATH``): command-line timings, not a profile.

Each entry of ``results`` becomes one series value in seconds. The v1 document
carries no command lines (see ``format_v1.py``), and hyperfine writes the
command it ran under ``command``, so the series are named by their position
(``command 1``, ``command 2``) unless the file holds a ``command_name``, which
only a name the user chose can be; that one is followed by its position
(``slow path (2)``), so no two results share a name and a named result can never
equal a positional one. ``user``, ``system``, ``parameters``,
``exit_codes`` and the ``times`` values are not read: ``times`` only tells how
many rounds ran.

A command line is not a function, so no entry names one.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..format_v1 import EVIDENCE_FORMAT, EvidenceFormatError

# hyperfine writes ``results`` first and, in each result, ``command`` first. Only that opening
# is read: a command can be long, and the sniffed bytes end wherever they end.
_FIRST_RESULT = re.compile(rb'\{\s*"results"\s*:\s*\[\s*\{\s*"command"\s*:')
_STATS = ("min", "max", "mean", "median", "stddev")


def sniff(head: bytes) -> bool:
    return _FIRST_RESULT.match(head.lstrip(b"\xef\xbb\xbf").lstrip()) is not None


def to_v1(data: bytes) -> dict[str, Any]:
    """The whole file as a v1 ``benchmark`` document; entries are checked by the v1 parser."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        doc = None
    results = doc.get("results") if isinstance(doc, dict) else None
    if not isinstance(results, list):
        raise EvidenceFormatError(
            'the file is not hyperfine JSON: no "results" list',
            "write it with hyperfine --export-json PATH",
        )
    return {
        "format": EVIDENCE_FORMAT,
        "kind": "benchmark",
        "tool": {"name": "hyperfine"},
        "benchmarks": [
            _entry(raw, position) if isinstance(raw, dict) else raw
            for position, raw in enumerate(results, start=1)
        ],
    }


def _entry(raw: dict[str, Any], position: int) -> dict[str, Any]:
    name = raw.get("command_name")
    times = raw.get("times")
    return {
        "name": f"{name.strip()} ({position})"
        if isinstance(name, str) and name.strip()
        else f"command {position}",
        "unit": "s",
        "lower_is_better": True,
        **{stat: raw.get(stat) for stat in _STATS},
        "rounds": len(times) if isinstance(times, list) else None,
    }


__all__ = ["sniff", "to_v1"]
