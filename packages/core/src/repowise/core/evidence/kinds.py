"""Evidence kinds: what each kind of ``repowise-evidence-v1`` entry is and where it goes.

:data:`evidence_kinds` is the one list of kinds, a plain dict the built-in
kinds fill when this module is imported. ``format_v1`` reads a document's
entries through it, ``ingest`` joins and caps them through it, and the store
writes each run's rows to the table its kind names. A kind declares:

* ``entries``: the key of the document's entry list (``symbols``, ``routes``);
* ``read_entry``: the entry validator, ``(entry, None)`` or ``(None, reason)``;
* ``identity``: what makes two entries one measurement (no finer than the row key);
* ``join``: how its entries reach the code: ``frame`` and ``symbol`` (a
  function the entry names), ``site`` (the call site that issued a
  statement), ``test`` (a test function), ``route`` (the route table) or
  ``statement`` (the statement-site index);
* ``table``, ``row`` and ``row_key``: the store table, an entry as a row of
  it, and the row's key within its run;
* ``retention``: how its runs are pruned (``history``: the newest
  ``evidence.history_runs`` per workload and ``evidence.max_runs`` per kind);
* ``classes``: the workload classes a run of the kind may carry. A kind
  whose only class is ``production`` (``route_latency``,
  ``statement_stats``) is production by what it measures, so its runs need
  no ``--class`` and no commit: a production window spans deploys.

Reserved, documented here and in ``docs/layers/RUNTIME_EVIDENCE.md``, with
no reader yet (a document of either kind is refused as unknown):

* ``error_event``: production errors, one entry per error group with its
  stack ``frames`` (``{path, function, line}``, leaf first), ``count``,
  ``users``, ``first_seen`` and ``last_seen`` (ISO 8601) and the ``release``
  or ``commit_sha`` first seen in.
* ``executed_set``: production coverage, the ``symbols`` (``{path,
  function}``) or ``files`` executed at least once over the run's window.

Ceiling: a kind added outside this module must store into one of the
built-in tables (:data:`STORE_TABLES`) and map its entries onto that table's
columns; a kind with a table of its own waits for a seam revision with its
migration. That keeps every store schema owned by core migrations.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from typing import Any

from . import format_v1 as v1

#: Every workload class a run may carry, in the order of how far a claim may go.
WORKLOAD_CLASSES = ("unit_test", "benchmark", "entry_point", "load_test", "production")
#: ``production`` is set only by a verified source (a connector, an upload the
#: host verified) or by a production-only kind, never by a flag someone types.
FLAG_WORKLOAD_CLASSES = tuple(c for c in WORKLOAD_CLASSES if c != "production")

JOINS = ("frame", "symbol", "site", "test", "route", "statement")
#: Joins that resolve a ``{path, function, line}`` the entry carries.
LOCATION_JOINS = frozenset({"frame", "symbol", "site", "test"})
RETENTION_RULES = ("history",)
#: The row tables a kind may store into. A new one comes with a core migration.
STORE_TABLES = (
    "evidence_symbol_costs",
    "evidence_benchmark_results",
    "evidence_test_timings",
    "evidence_query_counts",
    "evidence_route_latency",
    "evidence_statement_stats",
)
#: Reserved kind names, with a documented shape and no reader yet.
RESERVED_KINDS = ("error_event", "executed_set")

Entry = dict[str, Any]
Row = dict[str, Any]


@dataclass(frozen=True)
class EvidenceKind:
    """One kind of evidence entry (see the module docstring for each field)."""

    name: str
    entries: str
    read_entry: Callable[[dict[str, Any]], tuple[Entry | None, str | None]]
    identity: Callable[[Mapping[str, Any]], Hashable]
    join: str
    table: str
    row: Callable[[Mapping[str, Any], str | None], Row]
    row_key: Callable[[Mapping[str, Any]], Hashable]
    classes: tuple[str, ...]
    retention: str = "history"
    #: ``(value_kind, unit)`` of every run of the kind; None: the document says.
    clock: tuple[str | None, str | None] | None = None
    #: An entry whose function does not join keeps its row (a benchmark or a
    #: test is a series of its own); otherwise it is skipped by reason.
    keeps_unlinked: bool = False
    #: Sort key of the rows a per-run cap keeps first; None keeps document order.
    cap_order: Callable[[Mapping[str, Any]], Any] | None = None
    #: The ``EvidenceCaps`` field that caps the kind's rows per run.
    cap: str = "max_rows_per_run"

    @property
    def production_only(self) -> bool:
        """Every run of the kind is production: it needs no class flag and no commit."""
        return self.classes == ("production",)

    def location(self, entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """The ``{path, function, line}`` an entry joins by; None when it names none."""
        if self.join not in LOCATION_JOINS:
            return None
        return entry.get("location") if self.keeps_unlinked else entry


#: Kind name -> kind. Filled below; read by ``format_v1``, ``ingest`` and the store.
evidence_kinds: dict[str, EvidenceKind] = {}
#: Kind name -> the key of its entry list (kept in step with :data:`evidence_kinds`).
ENTRY_LISTS: dict[str, str] = {}


def register_kind(kind: EvidenceKind) -> None:
    """Add *kind*; a name taken or reserved, or a field out of range, raises ``ValueError``."""
    if kind.name in evidence_kinds or kind.name in RESERVED_KINDS:
        raise ValueError(f"evidence kind {kind.name!r} is already taken or reserved")
    if kind.join not in JOINS:
        raise ValueError(f"join {kind.join!r} is not one of {', '.join(JOINS)}")
    if kind.table not in STORE_TABLES:
        raise ValueError(f"table {kind.table!r} is not an evidence row table")
    if kind.retention not in RETENTION_RULES:
        raise ValueError(f"retention {kind.retention!r} is not one of {', '.join(RETENTION_RULES)}")
    if not kind.classes or set(kind.classes) - set(WORKLOAD_CLASSES):
        raise ValueError(f"classes {kind.classes!r} are not workload classes")
    evidence_kinds[kind.name] = kind
    ENTRY_LISTS[kind.name] = kind.entries


# --- rows -------------------------------------------------------------------


def _profile_row(entry: Mapping[str, Any], symbol_id: str | None) -> Row:
    inside, drivers = entry.get("inside"), entry.get("drivers")
    return {
        "symbol_id": symbol_id,
        "line": entry["line"],
        "self_value": entry["self_value"],
        "total_value": entry["total_value"],
        "self_samples": entry["self_samples"],
        "total_samples": entry["total_samples"],
        "call_count": entry["call_count"],
        "resolution": "declared",
        "inside_json": json.dumps(inside, sort_keys=True) if inside else None,
        "drivers_json": json.dumps(drivers, sort_keys=True) if drivers else None,
    }


def _benchmark_row(entry: Mapping[str, Any], symbol_id: str | None) -> Row:
    return {
        "benchmark_key": entry["name"],
        "symbol_id": symbol_id,
        **{
            k: entry[k]
            for k in (
                "unit",
                "lower_is_better",
                "median",
                "mean",
                "min",
                "max",
                "stddev",
                "rounds",
                "iterations",
                "allocs_per_op",
                "bytes_per_op",
            )
        },
    }


def _test_row(entry: Mapping[str, Any], symbol_id: str | None) -> Row:
    return {
        "test_id": entry["test_id"],
        "test_file": entry["test_file"],
        "symbol_id": symbol_id,
        "duration_s": entry["duration_s"],
        "outcome": entry["outcome"],
        "first_in_class": entry["first_in_class"],
    }


def _query_row(entry: Mapping[str, Any], symbol_id: str | None) -> Row:
    return {
        "scope_kind": entry["scope_kind"],
        "scope_id": entry["scope_id"],
        "scope_items": entry["scope_items"],
        "site_symbol_id": symbol_id,
        "statement_fp": entry["statement_fp"],
        "site_line": entry["line"],
        "query_count": entry["query_count"],
        "identical_max": entry["identical_max"],
        "total_ms": entry["total_ms"],
    }


def _route_row(entry: Mapping[str, Any], symbol_id: str | None) -> Row:
    return {
        "method": entry["method"],
        "route": entry["route"],
        "handler_symbol_id": symbol_id,
        "request_count": entry["count"],
        "p50_ms": entry["p50_ms"],
        "p95_ms": entry["p95_ms"],
        "total_ms": entry["total_ms"],
        "error_count": entry["errors"],
    }


def _statement_row(entry: Mapping[str, Any], symbol_id: str | None) -> Row:
    return {
        "statement_fp": entry["statement_fp"],
        "query_text": entry["query"],
        "symbol_id": symbol_id,
        "issuer": entry.get("issuer"),
        "calls": entry["calls"],
        "total_ms": entry["total_ms"],
        "mean_ms": entry["mean_ms"],
        "row_count": entry["rows"],
    }


def _costliest_profile(r: Mapping[str, Any]) -> Any:
    # Self, the cost waits excluded, then inclusive, then id, so the cut is stable.
    return (-(r["self_value"] or 0.0), -(r["total_value"] or 0.0), r["symbol_id"])


def _slowest_test(r: Mapping[str, Any]) -> Any:
    # Untimed last; ties keep the document's order (the sort is stable).
    return (r["duration_s"] is None, -(r["duration_s"] or 0.0))


def _costliest_route(r: Mapping[str, Any]) -> Any:
    return (r["total_ms"] is None, -(r["total_ms"] or 0.0), -r["request_count"])


def _costliest_statement(r: Mapping[str, Any]) -> Any:
    return (r["total_ms"] is None, -(r["total_ms"] or 0.0), -r["calls"])


_ANY_CLASS = WORKLOAD_CLASSES
_BUILT_IN = (
    EvidenceKind(
        name="profile",
        entries="symbols",
        read_entry=v1.profile_entry,
        identity=lambda e: (e["path"], e["function"]),
        join="frame",
        table="evidence_symbol_costs",
        row=_profile_row,
        row_key=lambda r: r["symbol_id"],
        classes=_ANY_CLASS,
        cap_order=_costliest_profile,
        cap="max_symbols_per_run",
    ),
    EvidenceKind(
        name="benchmark",
        entries="benchmarks",
        read_entry=v1.benchmark_entry,
        identity=lambda e: e["name"],
        join="symbol",
        table="evidence_benchmark_results",
        row=_benchmark_row,
        row_key=lambda r: r["benchmark_key"],
        classes=FLAG_WORKLOAD_CLASSES,
        keeps_unlinked=True,
    ),
    EvidenceKind(
        name="test_timing",
        entries="tests",
        read_entry=v1.test_entry,
        identity=lambda e: e["test_id"],
        join="test",
        table="evidence_test_timings",
        row=_test_row,
        row_key=lambda r: r["test_id"],
        classes=FLAG_WORKLOAD_CLASSES,
        clock=("wall", "s"),
        keeps_unlinked=True,
        cap_order=_slowest_test,
    ),
    EvidenceKind(
        name="query_count",
        entries="queries",
        read_entry=v1.query_entry,
        identity=lambda e: (
            e["scope_kind"],
            e["scope_id"],
            e["path"],
            e["function"],
            e["statement_fp"],
        ),
        join="site",
        table="evidence_query_counts",
        row=_query_row,
        row_key=lambda r: (r["scope_kind"], r["scope_id"], r["site_symbol_id"], r["statement_fp"]),
        classes=_ANY_CLASS,
        clock=(None, "count"),
    ),
    EvidenceKind(
        name="route_latency",
        entries="routes",
        read_entry=v1.route_entry,
        identity=lambda e: (e["method"], e["route"]),
        join="route",
        table="evidence_route_latency",
        row=_route_row,
        row_key=lambda r: (r["method"], r["route"]),
        classes=("production",),
        clock=("wall", "ms"),
        cap_order=_costliest_route,
    ),
    EvidenceKind(
        name="statement_stats",
        entries="statements",
        read_entry=v1.statement_entry,
        identity=lambda e: e["statement_fp"],
        join="statement",
        table="evidence_statement_stats",
        row=_statement_row,
        row_key=lambda r: r["statement_fp"],
        classes=("production",),
        clock=("wall", "ms"),
        cap_order=_costliest_statement,
    ),
)
for _kind in _BUILT_IN:
    register_kind(_kind)


__all__ = [
    "ENTRY_LISTS",
    "FLAG_WORKLOAD_CLASSES",
    "JOINS",
    "LOCATION_JOINS",
    "RESERVED_KINDS",
    "RETENTION_RULES",
    "STORE_TABLES",
    "WORKLOAD_CLASSES",
    "EvidenceKind",
    "evidence_kinds",
    "register_kind",
]
