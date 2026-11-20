"""``repowise-evidence-v1``: the one shape every kind of runtime evidence normalizes to.

A converter turns a profiler's, benchmark runner's or test runner's output
into this; ``repowise evidence add`` reads it. One JSON object per run::

    {
      "format": "repowise-evidence-v1",
      "kind": "profile",                  # profile | benchmark | test_timing | query_count
      "commit_sha": "<40 or 64 hex>",     # optional: the commit measured
      "dirty": false,                     # optional: uncommitted changes at measurement
      "tool": {"name": "pyinstrument", "version": "4.6.2"},   # optional
      "value_kind": "wall",               # profile: cpu | wall
      "unit": "samples",                  # profile: the unit of every value
      "total": 5300,                      # profile, optional: the whole run's value
      "explained": 580,                   # profile, optional: value with a repo frame on the stack
      "repo_self": 310,                   # profile, optional: value in production repo code itself
      "waits": 900,                       # profile, optional: value blocked waiting
      "libraries": 120,                   # profile, optional: value in dependencies
      "total_samples": 5300,              # profile, optional: samples in the whole run, any unit
      "skipped": {"outside_repo": 812},   # optional: entries the converter dropped, by reason
      "symbols": [{"path": "src/app/cli.py", "function": "build", "line": 40,
                   "self": 12, "total": 410}]
    }

The entry list is named by the kind: ``symbols`` (profile), ``benchmarks``,
``tests`` (test_timing) or ``queries`` (query_count). A code location is
``path`` (repo-relative POSIX), ``function`` (the qualified name, or the
symbol's id after its file as a test runner names it: ``TestCache::test_get``)
and ``line`` (where the function starts). A profile entry's ``self`` is what
the function costs: its own time and the built-ins and libraries it called,
never time spent waiting; ``total`` adds its callees in the repo.
A test entry with no ``path`` may carry ``classname``, the dotted name a
JUnit report gives it (``tests.unit.test_x.TestC``): ingest places it on the
indexed file that name leads to. ``"first_in_class": true`` marks the first
test a run reports for its class, whose time may include fixtures the whole
class shares.
``"exact": true`` says ``function``
is the symbol's own name, so it never falls back to a bare-name match (a
benchmark named by its node id). Paths are repo-relative, and the
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
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from repowise.core.git_sha import is_full_sha
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

# A statement digest, never statement text: hex only, so SQL cannot pass.
_DIGEST_RE = re.compile(r"[0-9a-f]{8,32}")
#: Longest name, id or path an entry may carry; longer ones are skipped.
_LONG_TEXT = 512
# A tool name, version or unit is one token, so it cannot carry a command line.
_TOKEN_RE = re.compile(r"[A-Za-z0-9._+/-]{1,64}")
_REASON_RE = re.compile(r"[a-z0-9_]{1,40}")
# Integers past this are not exact as floats and overflow the store's columns.
_MAX_INT = 2**53
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
    #: The run's split (``profile_convert``): production repo code itself, waits, libraries.
    repo_self: float | None
    waits: float | None
    libraries: float | None
    #: Samples in the whole run, whatever the unit; ``None`` when not counted.
    total_samples: int | None
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
    data, kind, raw_entries = _envelope(text)
    commit = _commit_sha(data)
    value_kind, unit = _value_kind_and_unit(kind, data)
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}

    skipped: Counter[str] = Counter(_converter_skips(data.get("skipped")))
    entries = _read_entries(kind, raw_entries, skipped)

    return EvidenceDocument(
        kind=kind,
        commit_sha=commit,
        dirty=data["dirty"] if isinstance(data.get("dirty"), bool) else None,
        tool_name=_token(tool.get("name")),
        tool_version=_token(tool.get("version")),
        value_kind=value_kind,
        unit=unit,
        total=_amount(data.get("total")),
        explained=_amount(data.get("explained")),
        repo_self=_amount(data.get("repo_self")),
        waits=_amount(data.get("waits")),
        libraries=_amount(data.get("libraries")),
        total_samples=_count(data.get("total_samples")),
        entries=tuple(entries),
        skipped=dict(skipped),
    )


def _envelope(text: str) -> tuple[dict[str, Any], str, list[Any]]:
    """The JSON object, its kind and its entry list, or the document-level error."""
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
    kind = _member(data.get("kind"), ENTRY_LISTS)
    if kind is None:
        raise EvidenceFormatError(
            f"unknown kind {data.get('kind')!r}",
            "kind is one of " + ", ".join(sorted(ENTRY_LISTS)),
        )
    raw_entries = data.get(ENTRY_LISTS[kind])
    if not isinstance(raw_entries, list):
        raise EvidenceFormatError(
            f"a {kind} document needs a {ENTRY_LISTS[kind]!r} list",
            f"put the entries under \"{ENTRY_LISTS[kind]}\": [...]",
        )
    return data, kind, raw_entries


def _commit_sha(data: dict[str, Any]) -> str | None:
    commit = data.get("commit_sha")
    if commit is None:
        return None
    commit = commit.lower() if isinstance(commit, str) else commit
    if not is_full_sha(commit):
        raise EvidenceFormatError(
            "commit_sha is not a full 40- or 64-character hex SHA",
            "use the output of `git rev-parse HEAD` at measurement, or leave it out",
        )
    return commit


def _read_entries(
    kind: str, raw_entries: list[Any], skipped: Counter[str]
) -> list[dict[str, Any]]:
    """The valid entries in order; each one not read is counted in *skipped*."""
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
    return entries


def _value_kind_and_unit(kind: str, data: dict[str, Any]) -> tuple[str | None, str | None]:
    if kind == "test_timing":
        return "wall", "s"
    if kind == "query_count":
        return None, "count"
    value_kind = _member(data.get("value_kind"), VALUE_KINDS)
    unit = _token(data.get("unit"))
    if kind == "profile":
        if value_kind is None:
            raise EvidenceFormatError(
                f"a profile needs value_kind cpu or wall, got {data.get('value_kind')!r}",
                "say whether the profiler sampled CPU time or wall-clock time",
            )
        if unit is None:
            raise EvidenceFormatError(
                "a profile needs a unit",
                "set \"unit\" to one word naming what the values count: samples, ns, ms or s",
            )
        return value_kind, unit
    # Benchmarks carry a unit per entry; the document's is optional.
    return value_kind, unit


def _converter_skips(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: Counter[str] = Counter()
    for reason, count in raw.items():
        if _is_count(count):
            # A reason is a word, never free text; anything else is "other".
            key = reason if isinstance(reason, str) and _REASON_RE.fullmatch(reason) else "other"
            out[key] += count
    return dict(out)


# --- values -----------------------------------------------------------------


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_INT


def _count(value: Any) -> int | None:
    return value if _is_count(value) else None


def _amount(value: Any) -> float | None:
    """A finite, non-negative number, ``None`` otherwise (never a made-up zero)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int) and abs(value) > _MAX_INT:
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _member(value: Any, allowed: Collection[str]) -> str | None:
    """*value* when it is one of *allowed*; a list or object is never hashed."""
    return value if isinstance(value, str) and value in allowed else None


def _token(value: Any) -> str | None:
    return value if isinstance(value, str) and _TOKEN_RE.fullmatch(value) else None


def _name(value: Any) -> tuple[str | None, str | None]:
    """A name or id: ``(text, None)``, or ``(None, reason)`` when missing or too long."""
    if not isinstance(value, str) or not value.strip():
        return None, "no_name"
    text = value.strip()
    if len(text) > _LONG_TEXT or any(ord(c) < 0x20 for c in text):
        return None, "bad_name"
    return text, None


def _line(value: Any) -> int | None:
    return value if _is_count(value) and value >= 1 else None


def _location(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """``{path, function, line}`` from an entry or a ``site`` object."""
    if not isinstance(raw, dict):
        return None, "no_path"
    path, reason = repo_relative_path(raw.get("path"))
    if path is None:
        return None, reason
    if len(path) > _LONG_TEXT:
        return None, "bad_path"
    function, reason = _name(raw.get("function"))
    if function is None:
        return None, "no_function" if reason == "no_name" else reason
    where = {"path": path, "function": function, "line": _line(raw.get("line"))}
    if raw.get("exact") is True:
        where["exact"] = True
    return where, None


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
    name, reason = _name(raw.get("name"))
    if name is None:
        return None, reason
    stats = {stat: _amount(raw.get(stat)) for stat in _BENCH_STATS}
    exact = {k: _amount(raw.get(k)) for k in ("allocs_per_op", "bytes_per_op")}
    if all(v is None for v in (*stats.values(), *exact.values())):
        return None, "no_value"
    # The benchmark function is optional: a result without one is a series still.
    where, _ = _location(raw)
    return {
        "name": name,
        "location": where,
        "unit": _token(raw.get("unit")),
        "lower_is_better": raw["lower_is_better"]
        if isinstance(raw.get("lower_is_better"), bool)
        else None,
        **stats,
        "rounds": _count(raw.get("rounds")),
        "iterations": _count(raw.get("iterations")),
        **exact,
    }, None


def _test_entry(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    test_id, reason = _name(raw.get("id"))
    if test_id is None:
        return None, "no_id" if reason == "no_name" else reason
    outcome = _member(raw.get("outcome"), TEST_OUTCOMES)
    duration = _amount(raw.get("duration_s"))
    if duration is None and outcome is None:
        return None, "no_value"
    where, _ = _location(raw)
    test_file, _ = repo_relative_path(raw.get("path"))
    classname, _ = _name(raw.get("classname")) if test_file is None else (None, None)
    return {
        "test_id": test_id,
        "test_file": test_file,
        "location": where,
        "duration_s": duration,
        "outcome": outcome,
        "classname": classname,
        "first_in_class": raw["first_in_class"] if isinstance(raw.get("first_in_class"), bool) else None,
    }, None


def _query_scope(
    raw: dict[str, Any],
) -> tuple[tuple[str, str, int | None] | None, str | None]:
    """``((kind, id, items), None)`` for the test or request a count belongs to.

    ``items`` is how many items the scope ran over (the input size), when the
    run says; it is what lets two scopes show a count growing or staying flat.
    """
    scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
    scope_kind = _member(scope.get("kind"), QUERY_SCOPES)
    scope_id, reason = _name(scope.get("id"))
    if scope_kind is None or scope_id is None:
        return None, "no_scope" if reason in (None, "no_name") else reason
    return (scope_kind, scope_id, _count(scope.get("items"))), None


def _query_entry(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    scope, reason = _query_scope(raw)
    if scope is None:
        return None, reason
    scope_kind, scope_id, items = scope
    site, reason = _location(raw.get("site"))
    if site is None:
        return None, reason
    fp = raw.get("statement_fp")
    if not isinstance(fp, str) or not _DIGEST_RE.fullmatch(fp):
        return None, "statement_not_digest"
    count = _count(raw.get("count"))
    if count is None:
        return None, "no_value"
    return {
        "scope_kind": scope_kind,
        "scope_id": scope_id,
        "scope_items": items,
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

#: What makes two entries of a kind the same measurement: no finer than the
#: store's key, so two entries never land on one stored row. The line is not
#: part of it; one function is one symbol.
_IDENTITY = {
    "profile": lambda e: (e["path"], e["function"]),
    "benchmark": lambda e: e["name"],
    "test_timing": lambda e: e["test_id"],
    "query_count": lambda e: (
        e["scope_kind"], e["scope_id"], e["path"], e["function"], e["statement_fp"]
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
