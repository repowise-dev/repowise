"""``repowise-evidence-v1``: the one shape every kind of runtime evidence normalizes to.

A converter turns a profiler's, benchmark runner's or test runner's output
into this; ``repowise evidence add`` reads it. One JSON object per run::

    {
      "format": "repowise-evidence-v1",
      "kind": "profile",                  # one of evidence.kinds (below)
      "commit_sha": "<40 or 64 hex>",     # optional: the commit measured
      "dirty": false,                     # optional: uncommitted changes at measurement
      "tool": {"name": "pyinstrument", "version": "4.6.2"},   # optional
      "value_kind": "wall",               # profile: cpu | wall
      "unit": "samples",                  # profile: the unit of every value
      "total": 5300,                      # profile, optional: the whole run's value
      "explained": 580,                   # profile, optional: value with a repo frame on the stack
      "repo_self": 310,                   # profile, optional: value in production repo code itself
      "waits": 900,                       # profile, optional: value blocked waiting
      "libraries": 120,                   # profile, optional: value in libraries
      "total_samples": 5300,              # profile, optional: samples in the whole run, any unit
      "skipped": {"outside_repo": 812},   # optional: entries the converter dropped, by reason
      "symbols": [{"path": "src/app/cli.py", "function": "build", "line": 40,
                   "self": 12, "total": 410,
                   "inside": [{"name": "Path.is_file", "kind": "library", "share": 0.57,
                               "leaf": {"name": "stat", "kind": "native", "share": 0.55}}],
                   "drivers": [{"path": "src/app/main.py", "function": "run", "line": 12,
                                "share": 1.0}]}]
    }

The entry list is named by the kind (:mod:`.kinds` holds every kind):
``symbols`` (profile), ``benchmarks``, ``tests`` (test_timing), ``queries``
(query_count), ``routes`` (route_latency) or ``statements``
(statement_stats). The last two are production kinds: a ``route_latency``
entry is ``{method, route, count, p50_ms, p95_ms, total_ms, errors}`` (a
route template, never a requested URL), a ``statement_stats`` entry is
``{statement_fp, query, calls, total_ms, mean_ms, rows}`` with ``query`` the
normalized text, cut short and with every value masked
(:func:`statement_text`). Any run may say the ``window`` it covers
(``{"start": ISO 8601, "end": ISO 8601}``) and ``base_commit_sha``, the
oldest commit deployed in it (``commit_sha`` is then the newest). A code location is
``path`` (repo-relative POSIX), ``function`` (the qualified name, or the
symbol's id after its file as a test runner names it: ``TestCache::test_get``)
and ``line`` (where the function starts). A profile entry's ``self`` is what
the function costs: its own time and the built-ins and libraries it called,
never time spent waiting; ``total`` adds its callees in the repo. A profile
entry whose ``function`` is ``<module>`` (no ``line``) is the file's module
body: its time is the file's import time, stored as the row
``<path>::<module>``.
Optional on a profile entry, as shares of ``total``: ``inside``, up to three
callees by ``name`` and ``kind`` (``own``, ``repo``, ``wait``, ``import``,
``native``, ``library``) with an optional ``leaf`` of the same shape (the
frame the callee's stacks end in) and, on a ``<module>`` entry's ``library``
item, ``"called": false`` when the run loaded that package and never ran it,
and ``drivers``, up to three callers by
``function`` (with ``path`` and ``line`` when the caller is repo code). A bad
item is dropped; the entry stays.
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
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from repowise.core.git_sha import is_full_sha
from repowise.core.interchange import format_tag, load_json_object, repo_relative_path

EVIDENCE_FORMAT = "repowise-evidence-v1"

VALUE_KINDS = frozenset({"cpu", "wall"})
TEST_OUTCOMES = frozenset({"passed", "failed", "skipped", "error"})
QUERY_SCOPES = frozenset({"test", "request"})
#: A profile entry's ``inside`` kinds (``evidence.profile_convert`` says what each means).
INSIDE_KINDS = ("own", "repo", "wait", "import", "native", "library")
#: A profile entry's ``function`` for a file's module body (its import time).
MODULE_FUNCTION = "<module>"
#: What a surface calls a module body's row.
IMPORT_TIME = "import time"


def is_import_row(symbol_id: str) -> bool:
    """Whether *symbol_id* is a module body's row (``<path>::<module>``): import time."""
    return symbol_id.endswith("::" + MODULE_FUNCTION)


#: Items of ``inside`` and of ``drivers`` read per entry; more are dropped, lightest first.
MAX_SPLIT_ITEMS = 3

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
    #: The window a production run covers (``window.start``, ``window.end``), UTC.
    window_start: datetime | None = None
    window_end: datetime | None = None
    #: The oldest commit deployed in the window; ``commit_sha`` is the newest.
    base_commit_sha: str | None = None

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
    window_start, window_end = _window(data.get("window"))

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
        window_start=window_start,
        window_end=window_end,
        base_commit_sha=_commit_sha(data, "base_commit_sha"),
    )


def _envelope(text: str) -> tuple[dict[str, Any], str, list[Any]]:
    """The JSON object, its kind and its entry list, or the document-level error."""
    from .kinds import ENTRY_LISTS, RESERVED_KINDS

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
    if kind is None and _member(data.get("kind"), RESERVED_KINDS):
        raise EvidenceFormatError(
            f"kind {data['kind']!r} is reserved: its shape is documented, and no "
            "release reads it yet",
            "kind is one of " + ", ".join(sorted(ENTRY_LISTS)),
        )
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


def _commit_sha(data: dict[str, Any], key: str = "commit_sha") -> str | None:
    commit = data.get(key)
    if commit is None:
        return None
    commit = commit.lower() if isinstance(commit, str) else commit
    if not is_full_sha(commit):
        raise EvidenceFormatError(
            f"{key} is not a full 40- or 64-character hex SHA",
            "use the output of `git rev-parse HEAD` at measurement, or leave it out",
        )
    return commit


def _instant(value: Any, name: str) -> datetime:
    """An ISO 8601 time with its offset, in UTC."""
    try:
        at = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        at = None
    if at is None or at.tzinfo is None:
        raise EvidenceFormatError(
            f"{name} is not an ISO 8601 time with its UTC offset",
            'write it like "2026-11-26T09:00:00Z", or leave the window out',
        )
    return at.astimezone(UTC)


def _window(raw: Any) -> tuple[datetime | None, datetime | None]:
    """``window: {start, end}``: when a production run's counters started and stopped."""
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        raise EvidenceFormatError(
            "window is not an object", 'write "window": {"start": ..., "end": ...}'
        )
    start = None if raw.get("start") is None else _instant(raw["start"], "window.start")
    end = None if raw.get("end") is None else _instant(raw["end"], "window.end")
    if start is not None and end is not None and start > end:
        raise EvidenceFormatError(
            "window.start is after window.end", "swap them, or leave the window out"
        )
    return start, end


def _read_entries(
    kind: str, raw_entries: list[Any], skipped: Counter[str]
) -> list[dict[str, Any]]:
    """The valid entries in order; each one not read is counted in *skipped*.

    Two entries are one measurement when their kind's ``identity`` agrees: no
    finer than the store's key, so two entries never land on one stored row.
    """
    from .kinds import evidence_kinds

    spec = evidence_kinds[kind]
    entries: list[dict[str, Any]] = []
    seen: set[Any] = set()
    read = spec.read_entry
    for raw in raw_entries:
        entry, reason = read(raw) if isinstance(raw, dict) else (None, "not_an_object")
        if entry is not None:
            identity = spec.identity(entry)
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
    from .kinds import evidence_kinds

    clock = evidence_kinds[kind].clock
    if clock is not None:
        return clock
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


def profile_entry(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
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
        "inside": _split_items(
            raw.get("inside"),
            _module_inside_item if where.get("function") == MODULE_FUNCTION else _inside_item,
        ),
        "drivers": _split_items(raw.get("drivers"), _driver_item),
    }
    if entry["self_value"] is None and entry["total_value"] is None:
        return None, "no_value"
    return entry, None


def _share_value(value: Any) -> float | None:
    number = _amount(value)
    return number if number is not None and number <= 1 else None


def _inside_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    name, _ = _name(raw.get("name"))
    kind, share = _member(raw.get("kind"), INSIDE_KINDS), _share_value(raw.get("share"))
    if name is None or kind is None or share is None:
        return None
    item: dict[str, Any] = {"name": name, "kind": kind, "share": share}
    leaf = raw.get("leaf")
    # One level only: a leaf names no leaf of its own.
    if isinstance(leaf, dict) and "leaf" not in leaf and (read := _inside_item(leaf)):
        item["leaf"] = read
    return item


def _module_inside_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    """An import row's ``inside`` item: a library may say the run never ran it."""
    item = _inside_item(raw)
    if item is not None and item["kind"] == "library" and raw.get("called") is False:
        item["called"] = False
    return item


def _driver_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    function, _ = _name(raw.get("function"))
    share = _share_value(raw.get("share"))
    if function is None or share is None:
        return None
    item: dict[str, Any] = {"function": function, "share": share}
    if raw.get("path") is not None:
        path, _ = repo_relative_path(raw.get("path"))
        if path is None or len(path) > _LONG_TEXT:
            return None
        item["path"] = path
        if (line := _line(raw.get("line"))) is not None:
            item["line"] = line
    return item


def _split_items(
    raw: Any, read: Callable[[dict[str, Any]], dict[str, Any] | None]
) -> list[dict[str, Any]] | None:
    """A profile entry's ``inside`` or ``drivers``: its valid items, heaviest
    first, at most :data:`MAX_SPLIT_ITEMS`; None when absent or none is valid."""
    if not isinstance(raw, list):
        return None
    items = [item for item in (read(r) for r in raw if isinstance(r, dict)) if item is not None]
    items.sort(key=lambda item: -item["share"])
    return items[:MAX_SPLIT_ITEMS] or None


def benchmark_entry(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
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


def test_entry(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
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


def query_entry(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    scope, reason = _query_scope(raw)
    if scope is None:
        return None, reason
    scope_kind, scope_id, items = scope
    site, reason = _location(raw.get("site"))
    if raw.get("site") is None and isinstance(raw.get("query"), str):
        # No call site, but the statement's text: the statement-site index
        # joins it to the function that issues it (``evidence.statement_join``).
        shape_text = masked_statement(raw["query"])
        if shape_text is not None:
            site = {"path": None, "function": None, "line": None, "shape_text": shape_text}
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


# --- production entries -----------------------------------------------------

#: An HTTP method as a route table names it (``GET``, ``ALL``), upper case.
_METHOD_RE = re.compile(r"[A-Z]{1,16}")
#: A path segment that is a value, not part of a template: a number, an email
#: or other ``@`` handle, or a long hex id or UUID. Ceiling: a template with a
#: literal numeric segment (``/v2/2024/report``) is refused too; name it
#: ``:year`` in the file.
_VALUE_SEGMENT_RE = re.compile(r"\d+|[^/]*@[^/]*|[0-9a-fA-F-]{16,}")
#: Longest statement text kept: enough to name its tables and functions for
#: the statement-site join, never a whole statement.
STATEMENT_TEXT_MAX = 200
# One left-to-right pass of PostgreSQL's lexical rules, so a quote inside a
# comment, a name or another string is read as what it sits in. Quoted text
# and numbers outside a parameter (``$1``) are values, never kept; a
# double-quoted name is kept as written. A backslash escapes the next
# character only in an ``E'...'`` string: in a standard string it is a
# character like any other.
_SQL_TOKEN_RE = re.compile(
    r"(?P<comment>/\*.*?(?:\*/|\Z)|--[^\n]*)"
    r"|(?P<name>\"(?:[^\"]|\"\")*(?:\"|\Z))"
    r"|(?P<dollar>\$(?P<tag>[A-Za-z_]\w*)?\$.*?(?:\$(?P=tag)\$|\Z))"
    r"|(?P<escaped>(?<!\w)[Ee]'(?:[^'\\]|\\.|'')*(?:'|\Z))"
    r"|(?P<string>'(?:[^']|'')*(?:'|\Z))"
    # A number is any word that starts with a digit (``0x1F``, ``1_000``,
    # ``3.5e2``, ``5.5.5``) or a dot and a digit (``.5``), unless it ends a
    # parameter or a name (``$12``, ``t2``).
    r"|(?P<number>(?<![$\w])\.?\d[\w.]*)",
    re.DOTALL,
)


def _mask(match: re.Match[str]) -> str:
    if match.group("name") is not None:
        return match.group("name")
    return " " if match.group("comment") is not None else "?"


def statement_text(value: Any) -> str | None:
    """Statement text as stored: comments dropped, every quoted string and
    number outside a parameter replaced by ``?``, whitespace collapsed, cut at
    :data:`STATEMENT_TEXT_MAX`; None for anything else.

    ``pg_stat_statements`` already replaces constants with ``$n``; this holds
    for text from any other source too, so no value reaches the store. The
    text is read as PostgreSQL: a double-quoted word is a name and is kept.
    """
    text = masked_statement(value)
    if text is None:
        return None
    return text if len(text) <= STATEMENT_TEXT_MAX else text[: STATEMENT_TEXT_MAX - 3] + "..."


def masked_statement(value: Any) -> str | None:
    """:func:`statement_text` before the cut, for the statement-site join only
    (a PostgREST call names its function past the first 200 characters).
    Never stored."""
    if not isinstance(value, str):
        return None
    text = _SQL_TOKEN_RE.sub(_mask, value)
    text = " ".join("".join(c if ord(c) >= 0x20 else " " for c in text).split())
    return text or None


def route_entry(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """A ``route_latency`` entry: one route's requests over the run's window."""
    method = raw.get("method")
    method = method.strip().upper() if isinstance(method, str) else None
    if method is None or not _METHOD_RE.fullmatch(method):
        return None, "bad_method"
    route, reason = _name(raw.get("route"))
    if route is None:
        return None, "no_route" if reason == "no_name" else reason
    # A template (``/users/:id``), never a URL someone requested: a query
    # string, a fragment or a segment that reads as a value may carry what
    # they typed or who they are.
    if (
        not route.startswith("/")
        or "?" in route
        or "#" in route
        or any(_VALUE_SEGMENT_RE.fullmatch(part) for part in route.split("/"))
    ):
        return None, "not_a_route_template"
    count = _count(raw.get("count"))
    if count is None:
        return None, "no_value"
    return {
        "method": method,
        "route": route,
        "count": count,
        "p50_ms": _amount(raw.get("p50_ms")),
        "p95_ms": _amount(raw.get("p95_ms")),
        "total_ms": _amount(raw.get("total_ms")),
        "errors": _count(raw.get("errors")),
    }, None


def statement_entry(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """A ``statement_stats`` entry: one normalized statement's totals over the window."""
    fp = raw.get("statement_fp")
    fp = fp.lower() if isinstance(fp, str) else None
    if fp is None or not _DIGEST_RE.fullmatch(fp):
        return None, "statement_not_digest"
    calls = _count(raw.get("calls"))
    if calls is None:
        return None, "no_value"
    return {
        "statement_fp": fp,
        "query": statement_text(raw.get("query")),
        # The whole masked text, for the statement-site join; never stored.
        "shape_text": masked_statement(raw.get("query")),
        "calls": calls,
        "total_ms": _amount(raw.get("total_ms")),
        "mean_ms": _amount(raw.get("mean_ms")),
        "rows": _count(raw.get("rows")),
    }, None


__all__ = [
    "EVIDENCE_FORMAT",
    "IMPORT_TIME",
    "MODULE_FUNCTION",
    "QUERY_SCOPES",
    "STATEMENT_TEXT_MAX",
    "TEST_OUTCOMES",
    "VALUE_KINDS",
    "EvidenceDocument",
    "EvidenceFormatError",
    "benchmark_entry",
    "is_import_row",
    "masked_statement",
    "parse_evidence",
    "profile_entry",
    "query_entry",
    "route_entry",
    "statement_entry",
    "statement_text",
    "test_entry",
]
