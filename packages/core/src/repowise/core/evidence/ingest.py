"""Join a validated ``repowise-evidence-v1`` document to the indexed code and store it.

Entries in v1 name their symbols already (path, qualified name, line), so
joining is an exact lookup in the indexed symbols, never a guess: an entry
whose file is not indexed, whose name matches no symbol or more than one,
whose file is generated or vendored, or whose file differs from the commit
measured is skipped and counted by reason. Evidence measured at another
commit than the indexed one keeps its own commit; it is never re-mapped.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from repowise.core.ingestion.symbol_identity import local_symbol_id
from repowise.core.test_paths import dotted_name_file, files_by_stem, nodeid_function

from .format_v1 import EVIDENCE_FORMAT, EvidenceDocument

#: Every workload class a run may carry, in the order of how far a claim may go.
WORKLOAD_CLASSES = ("unit_test", "benchmark", "entry_point", "load_test", "production")
#: ``production`` is set only by a verified source (a request-log or statement
#: ingest, a connector), never by a flag someone types.
FLAG_WORKLOAD_CLASSES = tuple(c for c in WORKLOAD_CLASSES if c != "production")

#: Origins whose code a run may spend time in but nobody here maintains.
_UNMAINTAINED = frozenset({"generated", "vendored"})
_UNMATCHED_SAMPLE = 10


@dataclass(frozen=True)
class EvidenceCaps:
    """The ``evidence:`` settings ``evidence add`` reads."""

    max_symbols_per_run: int
    max_rows_per_run: int
    history_runs: int
    max_runs: int


@dataclass(frozen=True)
class RunProvenance:
    """Where and how the run was measured, beyond what the file says."""

    workload_name: str
    workload_class: str
    commit_sha: str
    checkout_root: str
    dirty: bool | None
    environment: str = "unknown"
    source: str = "cli"
    #: Tests that failed in the profiled suite, as the profile reports it; None when not said.
    tests_failed: int | None = None


@dataclass(frozen=True)
class IndexedSymbol:
    symbol_id: str
    qualified_name: str
    name: str
    start_line: int | None


@dataclass
class Joined:
    """The document's entries joined to symbols, before anything is stored."""

    rows: list[dict[str, Any]]
    #: Entries that became rows.
    matched: int
    skipped: Counter[str] = field(default_factory=Counter)
    #: Benchmark and test rows kept without their function, by why it did not link.
    unlinked: Counter[str] = field(default_factory=Counter)
    unmatched_sample: list[str] = field(default_factory=list)


def symbol_lookup(
    symbols: Iterable[IndexedSymbol],
) -> dict[str, list[IndexedSymbol]]:
    """Indexed symbols of one file by qualified name, by id after the file and by bare name.

    The id after the file (``TestCache::test_get``) is how a test runner
    names a test, so a benchmark result names its function that way.
    """
    out: dict[str, list[IndexedSymbol]] = {}
    for sym in symbols:
        for key in dict.fromkeys((sym.qualified_name, local_symbol_id(sym.symbol_id), sym.name)):
            if key:
                out.setdefault(key, []).append(sym)
    return out


def _exact(sym: IndexedSymbol, function: str) -> bool:
    """*function* names *sym* by its qualified name or its id after the file."""
    return function in (sym.qualified_name, local_symbol_id(sym.symbol_id))


def resolve_location(
    location: Mapping[str, Any],
    by_file: Mapping[str, Mapping[str, list[IndexedSymbol]]],
) -> tuple[str | None, str | None]:
    """The symbol id a ``{path, function, line}`` names, or ``(None, reason)``.

    Exact: the qualified name or the id after the file first, the bare name
    when that is all the file declares under it, unless the location says
    ``exact``. More than one match is narrowed by the start line and refused
    when it stays ambiguous.
    """
    names = by_file.get(location["path"])
    if names is None:
        return None, "unknown_file"
    found = names.get(location["function"], [])
    by_qualified = [s for s in found if _exact(s, location["function"])]
    if location.get("exact"):
        found = by_qualified
    line = location.get("line")
    # A bare name must also agree on the line when one is given: a method
    # reported as ``read`` is not the module's own ``read``.
    if (len(found) > 1 or not by_qualified) and line is not None:
        found = [s for s in (by_qualified or found) if s.start_line == line]
    else:
        found = by_qualified or found
    if not found:
        return None, "unknown_symbol"
    if len({s.symbol_id for s in found}) > 1:
        return None, "ambiguous_symbol"
    return found[0].symbol_id, None


def join_entries(
    doc: EvidenceDocument,
    by_file: Mapping[str, Mapping[str, list[IndexedSymbol]]],
    origins: Mapping[str, str],
    changed: frozenset[str],
) -> Joined:
    """Every entry of *doc* as a row of its kind's table, or a counted skip.

    *by_file* holds the indexed symbols of the files the entries name,
    *origins* their ``code_origin``, *changed* the files that differ from the
    commit measured (empty when that is not known).
    """
    joined = Joined(rows=[], matched=0)
    seen: set[Any] = set()
    for entry in doc.entries:
        row, reason, path = _join(doc.kind, entry, by_file, origins, changed)
        if row is not None and reason is not None:
            # Kept without its function: a benchmark or test is a series anyway.
            joined.unlinked[reason] += 1
            reason = None
        if row is not None:
            key = _ROW_KEY[doc.kind](row)
            if key in seen:
                row, reason = None, "duplicate"
            else:
                seen.add(key)
        if row is None:
            joined.skipped[reason or "invalid"] += 1
            if path and len(joined.unmatched_sample) < _UNMATCHED_SAMPLE:
                joined.unmatched_sample.append(path)
            continue
        joined.rows.append(row)
        joined.matched += 1
    return joined


def _join(
    kind: str,
    entry: Mapping[str, Any],
    by_file: Mapping[str, Mapping[str, list[IndexedSymbol]]],
    origins: Mapping[str, str],
    changed: frozenset[str],
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """(row, reason, path) for one entry.

    A profile or query entry without its function is skipped for *reason*. A
    benchmark or test is a series of its own, so it keeps its row and
    *reason* says why its function was not linked.
    """
    optional = kind in ("benchmark", "test_timing")
    location = entry.get("location") if optional else entry
    if location is None:
        # A test named by a classname no indexed file matched (place_test_entries).
        reason = "unplaced" if entry.get("classname") else None
        return _ROW_BUILDERS[kind](entry, None), reason, None
    path = location["path"]
    origin = origins.get(path)
    if origin in _UNMAINTAINED:
        reason: str | None = origin
        symbol_id = None
    elif path in changed:
        reason, symbol_id = "dirty_file", None
    else:
        symbol_id, reason = resolve_location(location, by_file)
    if symbol_id is None and not optional:
        return None, reason, path
    return _ROW_BUILDERS[kind](entry, symbol_id), reason, path


def _profile_row(entry: Mapping[str, Any], symbol_id: str | None) -> dict[str, Any]:
    return {
        "symbol_id": symbol_id,
        "line": entry["line"],
        "self_value": entry["self_value"],
        "total_value": entry["total_value"],
        "self_samples": entry["self_samples"],
        "total_samples": entry["total_samples"],
        "call_count": entry["call_count"],
        "resolution": "declared",
    }


def _benchmark_row(entry: Mapping[str, Any], symbol_id: str | None) -> dict[str, Any]:
    return {
        "benchmark_key": entry["name"],
        "symbol_id": symbol_id,
        **{
            k: entry[k]
            for k in (
                "unit", "lower_is_better", "median", "mean", "min", "max", "stddev",
                "rounds", "iterations", "allocs_per_op", "bytes_per_op",
            )
        },
    }


def _test_row(entry: Mapping[str, Any], symbol_id: str | None) -> dict[str, Any]:
    return {
        "test_id": entry["test_id"],
        "test_file": entry["test_file"],
        "symbol_id": symbol_id,
        "duration_s": entry["duration_s"],
        "outcome": entry["outcome"],
        "first_in_class": entry["first_in_class"],
    }


def _query_row(entry: Mapping[str, Any], symbol_id: str | None) -> dict[str, Any]:
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


_ROW_BUILDERS = {
    "profile": _profile_row,
    "benchmark": _benchmark_row,
    "test_timing": _test_row,
    "query_count": _query_row,
}

#: The stored key of a row within its run (see the evidence tables' keys).
_ROW_KEY = {
    "profile": lambda r: r["symbol_id"],
    "benchmark": lambda r: r["benchmark_key"],
    "test_timing": lambda r: r["test_id"],
    "query_count": lambda r: (
        r["scope_kind"], r["scope_id"], r["site_symbol_id"], r["statement_fp"]
    ),
}


def rebase_test_entries(
    doc: EvidenceDocument, files: Collection[str], base: str
) -> EvidenceDocument:
    """*doc* with each test file the index does not hold read from *base*, when the index holds that.

    *base* is the repo-relative directory the report was read from. A runner in a
    package below the repository root names test files from where it ran and writes
    its report there (Vitest, which has no option to prefix them). The joined path wins
    when the index holds it, even beside a root file of the same name; a path that is
    already repo-relative (``web/src/x.test.ts``) joins to none and is kept.
    """
    if doc.kind != "test_timing" or not base:
        return doc
    entries = []
    for entry in doc.entries:
        path = entry.get("test_file")
        moved = f"{base}/{path}" if path else None
        test_id = entry["test_id"]
        # The id names the file first (``path::name``); one that does not is left whole.
        if moved in files and test_id.startswith(f"{path}::"):
            location = entry.get("location")
            entry = {
                **entry,
                "test_id": moved + test_id[len(path) :],
                "test_file": moved,
                "location": {**location, "path": moved} if location else None,
            }
        entries.append(entry)
    return replace(doc, entries=tuple(entries))


def place_test_entries(doc: EvidenceDocument, files: Collection[str]) -> EvidenceDocument:
    """*doc* with each test named only by a dotted ``classname`` placed on its file.

    A JUnit report that does not say a test's file names its class
    (``tests.unit.test_x.TestC``, ``com.acme.CartTest``); the indexed file
    that name leads to (:func:`~repowise.core.test_paths.dotted_name_file`,
    one of *files* or none) gives the test its ``path::qualname`` id and its
    function. A name that leads to no file, or to two, stays unplaced.
    """
    if doc.kind != "test_timing" or not any(e.get("classname") for e in doc.entries):
        return doc
    by_stem = files_by_stem(files)
    entries = []
    for entry in doc.entries:
        classname = entry.get("classname")
        found = dotted_name_file(classname, by_stem) if classname else None
        if found is not None:
            path, rest = found
            name = entry["test_id"].removeprefix(f"{classname}::")
            test_id = f"{path}::{'::'.join((*rest, name))}"
            function = nodeid_function(test_id)
            entry = {
                **entry,
                "test_id": test_id,
                "test_file": path,
                "classname": None,
                "location": {"path": path, "function": function, "line": None, "exact": True}
                if function
                else None,
            }
        entries.append(entry)
    return replace(doc, entries=tuple(entries))


def cap_rows(
    kind: str, rows: list[dict[str, Any]], caps: EvidenceCaps
) -> tuple[list[dict[str, Any]], int]:
    """The rows kept under the cap, and how many were dropped.

    A profile keeps its costliest symbols (self, the cost waits excluded,
    then inclusive, then id, so the cut is stable): a leaf that does the work
    outlives the frames that only call it. Other kinds keep the document's
    order.
    """
    if kind == "profile":
        limit = caps.max_symbols_per_run
        rows = sorted(
            rows,
            key=lambda r: (-(r["self_value"] or 0.0), -(r["total_value"] or 0.0), r["symbol_id"]),
        )
    else:
        limit = caps.max_rows_per_run
    return rows[:limit], max(len(rows) - limit, 0)


def run_key(
    text: str,
    provenance: RunProvenance,
    kind: str,
    index_commit: str | None = None,
    report_dir: str = "",
) -> str:
    """One measurement's identity: its content, everything the run row records,
    the index it was joined against (a later index joins it afresh), and the
    directory its test paths were read from (:func:`rebase_test_entries`)."""
    digest = hashlib.blake2b(digest_size=16)
    for part in (
        kind,
        str(index_commit),
        provenance.workload_name,
        provenance.workload_class,
        provenance.environment,
        provenance.checkout_root,
        str(provenance.dirty),
        provenance.source,
        # Only when known, so a run that never reports it keeps its key.
        *(() if provenance.tests_failed is None else (f"tests_failed={provenance.tests_failed}",)),
        # Only test paths are read from it (rebase_test_entries), so other kinds keep their key.
        *((f"report_dir={report_dir}",) if report_dir and kind == "test_timing" else ()),
        text,
    ):
        digest.update(part.encode("utf-8", "replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def run_fields(
    doc: EvidenceDocument,
    provenance: RunProvenance,
    joined: Joined,
    *,
    key: str,
    truncated: int,
    mapping_partial: bool,
    unlisted: int | None = None,
) -> dict[str, Any]:
    """The ``evidence_runs`` row for one ingest; *unlisted* from :func:`unlisted_count`."""
    skipped = Counter(doc.skipped) + joined.skipped
    outside = skipped.get("outside_repo", 0)
    return {
        "run_key": key,
        "kind": doc.kind,
        "format": EVIDENCE_FORMAT,
        "source": provenance.source,
        "workload_name": provenance.workload_name,
        "workload_class": provenance.workload_class,
        "value_kind": doc.value_kind,
        "unit": doc.unit,
        "environment": provenance.environment,
        "tool_name": doc.tool_name,
        "tool_version": doc.tool_version,
        "commit_sha": provenance.commit_sha,
        "dirty": provenance.dirty,
        "checkout_root": provenance.checkout_root,
        "units_total": doc.units_total,
        # Rows stored: matched + skipped + outside + truncated = total.
        "units_matched": joined.matched - truncated,
        "units_skipped": sum(skipped.values()) - outside,
        "units_outside_repo": outside,
        "units_truncated": truncated,
        "skipped_json": json.dumps(dict(sorted(skipped.items()))),
        "unmatched_sample_json": json.dumps(joined.unmatched_sample),
        "total_value": doc.total,
        "explained_value": doc.explained,
        "repo_self_value": doc.repo_self,
        "wait_value": doc.waits,
        "library_value": doc.libraries,
        "total_samples": doc.total_samples,
        "unlisted_symbols": unlisted,
        "mapping_partial": mapping_partial,
        "tests_failed": provenance.tests_failed,
    }


def unlisted_count(rows: Iterable[Mapping[str, Any]], origins: Mapping[str, str]) -> int:
    """Profile rows outside production code: counted before a cap drops any, never listed."""
    from repowise.core.ingestion.symbol_identity import file_of_symbol

    return sum(origins.get(file_of_symbol(row["symbol_id"])) != "production" for row in rows)


def entry_paths(doc: EvidenceDocument) -> set[str]:
    """Every file the document's entries name."""
    out: set[str] = set()
    for entry in doc.entries:
        location = entry.get("location") if doc.kind in ("benchmark", "test_timing") else entry
        if location is not None:
            out.add(location["path"])
    return out


async def load_symbols(
    session: Any, repository_id: str, paths: Iterable[str]
) -> dict[str, dict[str, list[IndexedSymbol]]]:
    """The indexed symbols of *paths*, per file, by name (:func:`symbol_lookup`).

    A file indexed with no symbols still gets an entry, so "no such symbol"
    and "no such file" stay apart.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import WikiSymbol

    wanted = sorted(set(paths))
    per_file: dict[str, list[IndexedSymbol]] = {}
    for i in range(0, len(wanted), 400):
        result = await session.execute(
            select(
                WikiSymbol.file_path,
                WikiSymbol.symbol_id,
                WikiSymbol.qualified_name,
                WikiSymbol.name,
                WikiSymbol.start_line,
            ).where(
                WikiSymbol.repository_id == repository_id,
                WikiSymbol.file_path.in_(wanted[i : i + 400]),
            )
        )
        for row in result:
            per_file.setdefault(row.file_path, []).append(
                IndexedSymbol(row.symbol_id, row.qualified_name, row.name, row.start_line)
            )
    return {path: symbol_lookup(symbols) for path, symbols in per_file.items()}


async def load_origins(
    session: Any, repository_id: str, paths: Iterable[str]
) -> dict[str, str]:
    """``code_origin`` of *paths*: the stored one, else :func:`path_origin`."""
    from sqlalchemy import select

    from repowise.core.code_origin import path_origin
    from repowise.core.persistence.models import HealthFileMetric

    wanted = sorted(set(paths))
    stored: dict[str, str] = {}
    for i in range(0, len(wanted), 400):
        result = await session.execute(
            select(HealthFileMetric.file_path, HealthFileMetric.code_origin).where(
                HealthFileMetric.repository_id == repository_id,
                HealthFileMetric.file_path.in_(wanted[i : i + 400]),
            )
        )
        stored.update({row.file_path: row.code_origin for row in result if row.code_origin})
    return {path: stored.get(path) or path_origin(path) for path in wanted}


async def stamp_bodies(
    session: Any,
    repository_id: str,
    rows: Iterable[dict[str, Any]],
    *,
    index_at_measured_commit: bool,
) -> None:
    """Give each profile row its function's lineage and, when the index is at
    the commit measured, its body digest (``function_facts``).

    The digest is what tells a later index whether the measured body is still
    the one indexed. Joined at another commit, the indexed body may not be the
    one measured, so none is stored and the row reads stale once HEAD moves.
    """
    from repowise.core.ingestion.symbol_identity import file_of_symbol
    from repowise.core.persistence.crud import stored_bodies

    rows = list(rows)
    paths = {file_of_symbol(row["symbol_id"]) for row in rows}
    bodies = {b.symbol_id: b for b in await stored_bodies(session, repository_id, paths)}
    for row in rows:
        body = bodies.get(row["symbol_id"])
        if body is None:
            continue
        row["lineage_id"] = body.lineage_id
        if index_at_measured_commit:
            row["body_digest"] = body.digest


def uncommitted_paths(checkout_root: str | Path) -> frozenset[str] | None:
    """Tracked files under *checkout_root* that differ from its HEAD, repo-relative.

    ``None`` when git cannot say (not a checkout). An untracked file has no
    symbol at the commit, so only tracked changes count.
    """
    from repowise.core.analysis.git_cli import run_git

    try:
        proc = run_git(
            ["diff", "--name-only", "-z", "--relative", "--no-renames", "HEAD", "--"],
            str(checkout_root),
        )
    except (OSError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    return frozenset(p for p in proc.stdout.split("\0") if p)


__all__ = [
    "FLAG_WORKLOAD_CLASSES",
    "WORKLOAD_CLASSES",
    "EvidenceCaps",
    "IndexedSymbol",
    "Joined",
    "RunProvenance",
    "cap_rows",
    "entry_paths",
    "join_entries",
    "load_origins",
    "load_symbols",
    "place_test_entries",
    "rebase_test_entries",
    "resolve_location",
    "run_fields",
    "run_key",
    "stamp_bodies",
    "symbol_lookup",
    "uncommitted_paths",
    "unlisted_count",
]
