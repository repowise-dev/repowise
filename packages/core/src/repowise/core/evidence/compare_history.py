"""The OSS host of :func:`evidence.compare.compare_runs`: read the stored runs,
list the commit ranges from the checkout, and name what each commit changed
from function-level history (``git_function_blame``).

A commit changed a function when blame credits one of the function's current
lines to it. Ceiling: a commit whose lines a later commit in the same range
replaced is not named (nor one that only deleted lines); ``git log -L`` per
function would name it, at a git call per function. Without a checkout (or
with a sha git does not know) the range is not listed and the comparison says
only that it is slower.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from collections.abc import Collection, Iterable, Sequence
from pathlib import Path
from typing import Any

from .compare import (
    Cause,
    Comparison,
    History,
    benchmark_profiles,
    cause_for,
    compare_runs,
    comparison_pairs,
    reached_functions,
    runs_to_read,
)

log = logging.getLogger(__name__)

#: Commits listed per range; an older band is a stale band, not a longer list.
MAX_RANGE_COMMITS = 200
#: Callee hops from a benchmark's own function, and the most functions reached.
CALLEE_DEPTH = 4
MAX_CALLEES = 500

_SYMBOL_KINDS = ("function", "method")


def git_range(
    repo_path: Path | str, base: str, head: str
) -> list[tuple[str, str, list[str]]] | None:
    """``base..head``'s commits ``(sha, subject, files)``, newest first, merges left out;
    paths relative to *repo_path*. ``None`` when git cannot list the range."""
    from ..analysis.git_cli import run_git

    try:
        proc = run_git(
            [
                "log",
                "--no-merges",
                "--relative",
                "--name-only",
                f"--max-count={MAX_RANGE_COMMITS}",
                "--format=%x1e%H%x1f%s",
                f"{base}..{head}",
            ],
            cwd=repo_path,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        log.debug("git log %s..%s failed: %s", base, head, exc)
        return None
    if proc.returncode != 0:
        return None
    out = []
    for block in proc.stdout.split("\x1e")[1:]:
        head_line, _, rest = block.partition("\n")
        sha, _, subject = head_line.partition("\x1f")
        files = [line.strip() for line in rest.splitlines() if line.strip()]
        out.append((sha.strip(), subject.strip() or None, files))
    return out


async def load_history(
    session: Any,
    repository_id: str,
    repo_path: Path | str | None,
    pairs: Iterable[tuple[str, str]],
    benchmark_functions: Collection[str] = (),
) -> History:
    """A :class:`~.compare.History` for *pairs* (``comparison_pairs``): each range from
    git, what each of its commits changed from function blame, and the callees of
    *benchmark_functions* from the graph."""
    ranges: dict[tuple[str, str], list[tuple[str, str | None]]] = {}
    files: dict[str, set[str]] = {}
    if repo_path is not None:
        for base, head in pairs:
            # Off the event loop: a server answers other calls while git runs.
            listed = await asyncio.to_thread(git_range, repo_path, base, head)
            if listed is None:
                continue
            ranges[(base, head)] = [(sha, subject) for sha, subject, _ in listed]
            for sha, _, changed in listed:
                for path in changed:
                    files.setdefault(path, set()).add(sha)
    touched = await _touched(session, repository_id, files)
    callees = await _callees(session, repository_id, benchmark_functions)
    return History(ranges=ranges, touched=touched, callees=callees)


async def _touched(
    session: Any, repository_id: str, files: dict[str, set[str]]
) -> dict[str, set[str]]:
    """Commit -> the functions in *files* blame credits it with."""
    from sqlalchemy import select

    from ..persistence.batches import chunked
    from ..persistence.crud.git import function_blame_rows
    from ..persistence.models import WikiSymbol

    if not files:
        return {}
    spans: list[tuple[str, int, int]] = []
    for batch in chunked(sorted(files)):
        result = await session.execute(
            select(WikiSymbol.symbol_id, WikiSymbol.start_line, WikiSymbol.end_line).where(
                WikiSymbol.repository_id == repository_id,
                WikiSymbol.file_path.in_(batch),
                WikiSymbol.kind.in_(_SYMBOL_KINDS),
            )
        )
        spans.extend((row.symbol_id, row.start_line, row.end_line) for row in result)
    out: dict[str, set[str]] = {}
    for symbol_id, row in (await function_blame_rows(session, repository_id, spans)).items():
        in_range = files.get(row.file_path, set())
        try:
            shas = json.loads(row.commit_shas_json or "[]")
        except (TypeError, ValueError):
            continue
        for sha in shas if isinstance(shas, list) else ():
            if sha in in_range:
                out.setdefault(sha, set()).add(symbol_id)
    return out


async def _callees(session: Any, repository_id: str, roots: Collection[str]) -> dict[str, set[str]]:
    """Each root's callees within :data:`CALLEE_DEPTH` hops (at most :data:`MAX_CALLEES`)."""
    from sqlalchemy import select

    from ..ingestion.models import EXECUTION_EDGE_TYPES
    from ..persistence.batches import chunked
    from ..persistence.models import GraphEdge

    out: dict[str, set[str]] = {}
    for root in sorted(set(roots)):
        seen: set[str] = set()
        frontier = {root}
        for _ in range(CALLEE_DEPTH):
            found: set[str] = set()
            for batch in chunked(sorted(frontier)):
                result = await session.execute(
                    select(GraphEdge.target_node_id).where(
                        GraphEdge.repository_id == repository_id,
                        GraphEdge.source_node_id.in_(batch),
                        GraphEdge.edge_type.in_(sorted(EXECUTION_EDGE_TYPES)),
                    )
                )
                found.update(result.scalars())
            frontier = found - seen - {root}
            seen |= frontier
            if not frontier or len(seen) >= MAX_CALLEES:
                break
        if seen:
            out[root] = set(sorted(seen)[:MAX_CALLEES])
    return out


async def load_comparisons(
    session: Any, repository_id: str, repo_path: Path | str | None
) -> list[Comparison]:
    """Every workload's comparison from the store (and the checkout at *repo_path*)."""
    from ..persistence.crud.analysis.evidence import EVIDENCE_ROW_TABLES, list_evidence_runs

    runs = await list_evidence_runs(session, repository_id)
    wanted = runs_to_read(runs)
    rows = await _rows(session, runs, wanted, EVIDENCE_ROW_TABLES)
    origins = await _profile_origins(session, repository_id, runs, rows)
    found = compare_runs(runs, rows, origins=origins)
    slower = [c for c in found if c.verdict == "slower"]
    if not slower:
        # Nothing to explain: no git call, no history read.
        return found
    pairs = [
        p for p in comparison_pairs(runs) if p in {(c.base_commit, c.head_commit) for c in slower}
    ]
    benchmark_functions = {
        s.symbol_id for c in slower if c.kind == "benchmark" for s in c.slower if s.symbol_id
    }
    history = await load_history(session, repository_id, repo_path, pairs, benchmark_functions)
    return compare_runs(runs, rows, history, origins)


async def _profile_origins(
    session: Any, repository_id: str, runs: Sequence[Any], rows: dict[str, list[Any]]
) -> dict[str, str]:
    """``code_origin`` of every file a profile row names (stored per file, path fallback)."""
    from ..ingestion.symbol_identity import file_of_symbol
    from .ingest import load_origins

    files = {
        file_of_symbol(r.symbol_id)
        for run in runs
        if run.kind == "profile"
        for r in rows.get(run.id, ())
        if r.symbol_id
    }
    return await load_origins(session, repository_id, files) if files else {}


async def benchmark_cause(
    session: Any,
    repository_id: str,
    repo_path: Path | str | None,
    base: str | None,
    head: str | None,
    benchmark_functions: Sequence[str],
) -> Cause | None:
    """The commits between *base* and *head* that changed what *benchmark_functions*
    ran (their callees, and the functions the newest benchmark profiles measured):
    the cause line ``perf check`` shares with :func:`load_comparisons`."""
    from ..persistence.crud.analysis.evidence import EVIDENCE_ROW_TABLES, list_evidence_runs

    if not base or not head or base == head:
        return None
    runs = await list_evidence_runs(session, repository_id)
    profiles = benchmark_profiles(runs)
    rows = await _rows(session, profiles, [r.id for r in profiles], EVIDENCE_ROW_TABLES)
    history = await load_history(
        session, repository_id, repo_path, [(base, head)], benchmark_functions
    )
    reached = reached_functions(list(benchmark_functions), history, rows.values())
    return cause_for(base, head, reached, history)


async def _rows(
    session: Any, runs: Sequence[Any], wanted: Sequence[str], tables: dict[str, Any]
) -> dict[str, list[Any]]:
    from sqlalchemy import select

    from ..persistence.batches import chunked

    kinds = {run.id: run.kind for run in runs}
    out: dict[str, list[Any]] = {}
    for kind in sorted({kinds[i] for i in wanted}):
        table = tables[kind]
        ids = [i for i in wanted if kinds[i] == kind]
        for batch in chunked(ids):
            result = await session.execute(select(table).where(table.c.run_id.in_(batch)))
            for row in result:
                out.setdefault(row.run_id, []).append(row)
    return out


__all__ = ["benchmark_cause", "git_range", "load_comparisons", "load_history"]
