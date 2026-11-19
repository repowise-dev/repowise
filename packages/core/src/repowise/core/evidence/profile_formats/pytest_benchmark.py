"""pytest-benchmark JSON (``pytest --benchmark-json=PATH``): benchmark results, not a profile.

Each benchmark becomes one series value: its ``fullname`` (the pytest node
id, parameters included) names the series, and its stats are seconds. The
node id without its parameters is the benchmark function's symbol id
(``benchmarks/test_io.py::TestCache::test_get``), so the entry names that
function by its file and its id after the file; ingest joins it to the
stored symbol exactly or keeps the series without it. A benchmark file is
test or tooling code: the row names the benchmark, it never claims the cost
of a production function.

``commit_info.id`` is copied as the document's commit only when it is a
full SHA. ``evidence add`` still needs ``--commit`` for a converted file and
refuses one that disagrees, so the recorded commit is a cross-check and is
never taken on its own. Machine info, parameters and ``extra_info`` are not
read: the v1 document carries no labels.
"""

from __future__ import annotations

import json
import re
from typing import Any

from repowise.core.git_sha import is_full_sha
from repowise.core.ingestion.symbol_identity import file_of_symbol, local_symbol_id
from repowise.core.test_paths import nodeid_without_params

from ..format_v1 import EVIDENCE_FORMAT, EvidenceFormatError

# pytest-benchmark writes ``machine_info`` as the first key of its object.
_FIRST_KEY = re.compile(rb'\{\s*"machine_info"\s*:')
_STATS = ("min", "max", "mean", "median", "stddev", "rounds", "iterations")


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
            "the file is not pytest-benchmark JSON: no \"benchmarks\" list",
            "write it with pytest --benchmark-json=PATH",
        )
    out: dict[str, Any] = {
        "format": EVIDENCE_FORMAT,
        "kind": "benchmark",
        "tool": {"name": "pytest-benchmark", "version": doc.get("version")},
        "benchmarks": [_entry(raw) if isinstance(raw, dict) else raw for raw in benchmarks],
    }
    info = doc.get("commit_info") if isinstance(doc.get("commit_info"), dict) else {}
    commit = info.get("id").lower() if isinstance(info.get("id"), str) else None
    if is_full_sha(commit):
        out["commit_sha"] = commit
    if isinstance(info.get("dirty"), bool):
        out["dirty"] = info["dirty"]
    return out


def _entry(raw: dict[str, Any]) -> dict[str, Any]:
    stats = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
    entry: dict[str, Any] = {
        "name": raw.get("fullname"),
        "unit": "s",
        "lower_is_better": True,
        **{stat: stats.get(stat) for stat in _STATS},
    }
    symbol_id = _symbol_id(raw.get("fullname"), raw.get("name"))
    if symbol_id is not None:
        entry["path"] = file_of_symbol(symbol_id)
        entry["function"] = local_symbol_id(symbol_id)
        # The node id is the symbol id: a method sharing the bare name is not it.
        entry["exact"] = True
    return entry


def _symbol_id(fullname: Any, name: Any) -> str | None:
    """The node id without its parameters: ``a.py::test_f[1]`` -> ``a.py::test_f``.

    ``name`` is the node's own last part (``test_f[1]``); the node id must end
    with it.
    """
    if not isinstance(fullname, str) or not isinstance(name, str) or not name:
        return None
    if not fullname.endswith(name) or not local_symbol_id(fullname):
        return None
    return nodeid_without_params(fullname)


__all__ = ["sniff", "to_v1"]
