"""Async DB helpers: read-only signal joins + symbol-id attachment."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.signals import FileSignals, file_signals
from repowise.core.persistence import crud
from repowise.core.persistence.models import WikiSymbol


async def _load_file_signals(session: AsyncSession, repo_id: str, file_path: str) -> FileSignals:
    """Join git metadata + graph degree for one file (read-only, no recompute).

    Degree is read only when the file is a graph node so topology stays "no
    signal" for files absent from the graph, rather than reporting a spurious
    zero. Shared by the drawer breakdown and the file-detail aggregate.
    """
    git_meta = await crud.get_git_metadata(session, repo_id, file_path)
    node = await crud.get_graph_node(session, repo_id, file_path)
    degrees = (
        await crud.get_node_degree_counts(session, repo_id, file_path) if node is not None else None
    )
    return file_signals(git_meta, degrees)


async def _attach_symbol_ids(
    session: AsyncSession, repo_id: str, finding_dicts: list[dict]
) -> list[dict]:
    """Attach the matching ``WikiSymbol.symbol_id`` to function-level findings.

    Matched by exact (file_path, function_name), falling back to the symbol
    whose line span contains the finding's ``line_start`` (covers methods
    recorded as ``Class.method`` vs the bare symbol name). One query for the
    whole batch; findings with no match keep ``symbol_id = None`` so the UI
    can degrade to the file page.
    """
    paths = {d["file_path"] for d in finding_dicts if d.get("function_name")}
    if not paths:
        return finding_dicts
    rows = (
        await session.execute(
            select(
                WikiSymbol.symbol_id,
                WikiSymbol.file_path,
                WikiSymbol.name,
                WikiSymbol.start_line,
                WikiSymbol.end_line,
            ).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.file_path.in_(paths),
            )
        )
    ).all()
    by_name: dict[tuple[str, str], str] = {}
    by_file: dict[str, list[tuple[int, int, str]]] = {}
    for symbol_id, file_path, name, start_line, end_line in rows:
        by_name.setdefault((file_path, name), symbol_id)
        if name and "." in name:
            by_name.setdefault((file_path, name.rsplit(".", 1)[-1]), symbol_id)
        if start_line is not None and end_line is not None:
            by_file.setdefault(file_path, []).append((start_line, end_line, symbol_id))
    for d in finding_dicts:
        fn = d.get("function_name")
        if not fn:
            d["symbol_id"] = None
            continue
        sid = by_name.get((d["file_path"], fn))
        if sid is None and d.get("line_start") is not None:
            line = d["line_start"]
            spans = by_file.get(d["file_path"], [])
            # Narrowest enclosing span wins (a method, not its class).
            best: tuple[int, str] | None = None
            for start, end, symbol_id in spans:
                if start <= line <= end and (best is None or end - start < best[0]):
                    best = (end - start, symbol_id)
            sid = best[1] if best else None
        d["symbol_id"] = sid
    return finding_dicts
