"""``repowise-evidence-v1``: the one shape every kind of runtime evidence normalizes to.

A converter turns a profiler's, benchmark runner's or test runner's output
into this; ``repowise evidence add`` reads it. One JSON object per run::

    {
      "format": "repowise-evidence-v1",
      "kind": "profile",                  # profile | benchmark | test_timing | query_count
      "commit_sha": "<40 hex>",           # optional: the commit measured
      "dirty": false,                     # optional: uncommitted changes at measurement
      "tool": {"name": "pyinstrument", "version": "4.6.2"},   # optional
      "value_kind": "wall",               # profile: cpu | wall
      "unit": "samples",                  # profile: the unit of every value
      "total": 5300,                      # profile, optional: the whole run's value
      "explained": 580,                   # profile, optional: value with a repo frame on the stack
      "skipped": {"outside_repo": 812},   # optional: entries the converter dropped, by reason
      "symbols": [{"path": "src/app/cli.py", "function": "build", "line": 40,
                   "self": 12, "total": 410}]
    }

The entry list is named by the kind: ``symbols`` (profile), ``benchmarks``,
``tests`` (test_timing) or ``queries`` (query_count). A code location is
``path`` (repo-relative POSIX), ``function`` (the qualified name) and
``line`` (where the function starts). Paths are repo-relative, and the
document carries no labels, environment variables or command lines: only
the fields named here are read, and nothing else is ever stored.

Whole-document problems (not JSON, another format, an unknown kind, a
malformed commit) raise :class:`EvidenceFormatError`. A bad entry is skipped
and counted by reason, never stored as zero: absent means unknown.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from repowise.core.interchange import format_tag, load_json_object, repo_relative_path

EVIDENCE_FORMAT = "repowise-evidence-v1"

#: Evidence kind -> the key of its entry list.
ENTRY_LISTS: dict[str, str] = {
    "profile": "symbols",
    "benchmark": "benchmarks",
    "test_timing": "tests",
    "query_count": "queries",
}
VALUE_KINDS = frozenset({"cpu", "wall"})
TEST_OUTCOMES = frozenset({"passed", "failed", "skipped", "error"})
QUERY_SCOPES = frozenset({"test", "request"})

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
# A statement digest, never statement text: hex only, so SQL cannot pass.
_DIGEST_RE = re.compile(r"^[0-9a-f]{8,32}$")
_SHORT_TEXT = 64
_BENCH_STATS = ("median", "mean", "min", "max", "stddev")


class EvidenceFormatError(ValueError):
    """The document as a whole cannot be read; *hint* says what to do."""

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class EvidenceDocument:
    """A validated document: its run fields and its entries, normalized."""

    kind: str
    commit_sha: str | None
    dirty: bool | None
    tool_name: str | None
    tool_version: str | None
    value_kind: str | None
    unit: str | None
    total: float | None
    explained: float | None
    #: One dict per valid entry, in document order (see ``_ENTRY_READERS``).
    entries: tuple[dict[str, Any], ...]
    #: Entries not read, by reason: the converter's own counts plus this file's.
    skipped: dict[str, int] = field(default_factory=dict)

    @property
    def units_total(self) -> int:
        """Every entry the run reported, read or skipped."""
        return len(self.entries) + sum(self.skipped.values())


def parse_evidence(text: str) -> EvidenceDocument:
    """Validate a ``repowise-evidence-v1`` document and normalize its entries."""
    data = load_json_object(text)
    if data is None:
        raise EvidenceFormatError(
            "the file is not a JSON object",
            f"write one JSON object with \"format\": \"{EVIDENCE_FORMAT}\"",
        )
    tag = format_tag(data)
    if tag != EVIDENCE_FORMAT:
        raise EvidenceFormatError(
            f"format is {tag!r}, expected {EVIDENCE_FORMAT!r}",
            f"set \"format\": \"{EVIDENCE_FORMAT}\", or convert the tool's output first",
        )
    kind = data.get("kind")
    if kind not in ENTRY_LISTS:
        raise EvidenceFormatError(
            f"unknown kind {kind!r}",
            "kind is one of " + ", ".join(sorted(ENTRY_LISTS)),
        )
    raw_entries = data.get(ENTRY_LISTS[kind])
    if not isinstance(raw_entries, list):
        raise EvidenceFormatError(
            f"a {kind} document needs a {ENTRY_LISTS[kind]!r} list",
            f"put the entries under \"{ENTRY_LISTS[kind]}\": [...]",
        )
    commit = data.get("commit_sha")
    if commit is not None and not (isinstance(commit, str) and _COMMIT_RE.match(commit)):
        raise EvidenceFormatError(
            "commit_sha is not a full 40-character lowercase hex SHA",
            "use the output of `git rev-parse HEAD` at measurement, or leave it out",
        )
    value_kind, unit = _value_kind_and_unit(kind, data)
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}

    skipped: Counter[str] = Counter(_converter_skips(data.get("skipped")))
    entries: list[dict[str, Any]] = []
    seen: set[Any] = set()
    read = _ENTRY_READERS[kind]
    for raw in raw_entries:
        entry, reason = read(raw) if isinstance(raw, dict) else (None, "not_an_object")
        if entry is not None:
            identity = _IDENTITY[kind](entry)
            if identity in seen:
                entry, reason = None, "duplicate"
            else:
                seen.add(identity)
        if entry is None:
            skipped[reason or "invalid"] += 1
        else:
            entries.append(entry)

    return EvidenceDocument(
        kind=kind,
        commit_sha=commit,
        dirty=data["dirty"] if isinstance(data.get("dirty"), bool) else None,
        tool_name=_short_text(tool.get("name")),
        tool_version=_short_text(tool.get("version")),
        value_kind=value_kind,
        unit=unit,
        total=_amount(data.get("total")),
        explained=_amount(data.get("explained")),
        entries=tuple(entries),
        skipped=dict(skipped),
    )


def _value_kind_and_unit(kind: str, data: dict[str, Any]) -> tuple[str | None, str | None]:
    if kind == "test_timing":
        return "wall", "s"
    if kind == "query_count":
        return None, "count"
    value_kind = data.get("value_kind")
    unit = _short_text(data.get("unit"))
    if kind == "profile":
        if value_kind not in VALUE_KINDS:
            raise EvidenceFormatError(
                f"a profile needs value_kind cpu or wall, got {value_kind!r}",
                "say whether the profiler sampled CPU time or wall-clock time",
            )
        if unit is None:
            raise EvidenceFormatError(
                "a profile needs a unit",
                "set \"unit\" to what the values count: samples, ns, ms or s",
            )
        return value_kind, unit
    # Benchmarks carry a unit per entry; the document's is optional.
    return (value_kind if value_kind in VALUE_KINDS else None), unit


def _converter_skips(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for reason, count in raw.items():
        if isinstance(reason, str) and reason and _is_count(count):
            out[reason[:_SHORT_TEXT]] = count
    return out


# --- values -----------------------------------------------------------------


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _count(value: Any) -> int | None:
    return value if _is_count(value) else None


def _amount(value: Any) -> float | None:
    """A finite, non-negative number, ``None`` otherwise (never a made-up zero)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _short_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:_SHORT_TEXT]


def _line(value: Any) -> int | None:
    return value if _is_count(value) and value >= 1 else None


def _location(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """``{path, function, line}`` from an entry or a ``site`` object."""
    if not isinstance(raw, dict):
        return None, "no_path"
    path, reason = repo_relative_path(raw.get("path"))
    if path is None:
        return None, reason
    function = raw.get("function")
    if not isinstance(function, str) or not function.strip():
        return None, "no_function"
    return {"path": path, "function": function.strip(), "line": _line(raw.get("line"))}, None


# --- entries ----------------------------------------------------------------


def _profile_entry(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    where, reason = _location(raw)
    if where is None:
        return None, reason
    entry = {
        **where,
        "self_value": _amount(raw.get("self")),
        "total_value": _amount(raw.get("total")),
        "self_samples": _count(raw.get("self_samples")),
        "total_samples": _count(raw.get("total_samples")),
        "call_count": _count(raw.get("calls")),
    }
    if entry["self_value"] is None and entry["total_value"] is None:
        return None, "no_value"
    return entry, None


def _benchmark_entry(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, "no_name"
    stats = {stat: _amount(raw.get(stat)) for stat in _BENCH_STATS}
    exact = {k: _amount(raw.get(k)) for k in ("allocs_per_op", "bytes_per_op")}
    if all(v is None for v in (*stats.values(), *exact.values())):
        return None, "no_value"
    # The benchmark function is optional: a result without one is a series still.
    where, _ = _location(raw)
    return {
        "name": name.strip(),
        "location": where,
        "unit": _short_text(raw.get("unit")),
        "lower_is_better": raw["lower_is_better"]
        if isinstance(raw.get("lower_is_better"), bool)
        else None,
        **stats,
        "rounds": _count(raw.get("rounds")),
        "iterations": _count(raw.get("iterations")),
        **exact,
    }, None


def _test_entry(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    test_id = raw.get("id")
    if not isinstance(test_id, str) or not test_id.strip():
        return None, "no_id"
    outcome = raw.get("outcome") if raw.get("outcome") in TEST_OUTCOMES else None
    duration = _amount(raw.get("duration_s"))
    if duration is None and outcome is None:
        return None, "no_value"
    where, _ = _location(raw)
    test_file, _ = repo_relative_path(raw.get("path"))
    return {
        "test_id": test_id.strip(),
        "test_file": test_file,
        "location": where,
        "duration_s": duration,
        "outcome": outcome,
    }, None


def _query_entry(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
    scope_kind, scope_id = scope.get("kind"), scope.get("id")
    if scope_kind not in QUERY_SCOPES or not isinstance(scope_id, str) or not scope_id.strip():
        return None, "no_scope"
    site, reason = _location(raw.get("site"))
    if site is None:
        return None, reason
    fp = raw.get("statement_fp")
    if not isinstance(fp, str) or not _DIGEST_RE.match(fp):
        return None, "statement_not_digest"
    count = _count(raw.get("count"))
    if count is None:
        return None, "no_value"
    return {
        "scope_kind": scope_kind,
        "scope_id": scope_id.strip(),
        **site,
        "statement_fp": fp,
        "query_count": count,
        "identical_max": _count(raw.get("identical_max")),
        "total_ms": _amount(raw.get("total_ms")),
    }, None


_ENTRY_READERS = {
    "profile": _profile_entry,
    "benchmark": _benchmark_entry,
    "test_timing": _test_entry,
    "query_count": _query_entry,
}

#: What makes two entries of a kind the same measurement.
_IDENTITY = {
    "profile": lambda e: (e["path"], e["function"], e["line"]),
    "benchmark": lambda e: e["name"],
    "test_timing": lambda e: e["test_id"],
    "query_count": lambda e: (
        e["scope_kind"], e["scope_id"], e["path"], e["function"], e["line"], e["statement_fp"]
    ),
}


__all__ = [
    "ENTRY_LISTS",
    "EVIDENCE_FORMAT",
    "QUERY_SCOPES",
    "TEST_OUTCOMES",
    "VALUE_KINDS",
    "EvidenceDocument",
    "EvidenceFormatError",
    "parse_evidence",
]
