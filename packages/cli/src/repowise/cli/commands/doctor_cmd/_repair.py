"""``repowise doctor --repair``: store drift, agent refresh, Claude Code re-registration."""

from __future__ import annotations

from pathlib import Path as _DoctorPath

from repowise.cli.helpers import console, run_async

from ._checks.context import CheckContext, StoreDrift, open_store
from ._checks.providers import _stale_page_guidance


async def _repair_fts(engine, sf, drift: StoreDrift) -> int:
    """Re-index missing pages and delete orphans; returns entries repaired."""
    from repowise.core.persistence import FullTextSearch, get_session

    fts = FullTextSearch(engine)
    try:
        await fts.ensure_index()
    except Exception as exc:
        # The schema upgrade is not what was asked for, and it must not take
        # the repair down with it: it once refused on a drifted store with an
        # error telling the user to run this very command (issue #1309). Both
        # repairs below work on either column set, so say so and carry on.
        console.print(f"  [yellow]Full-text index upgrade skipped: {exc}[/yellow]")
    repaired = 0
    # Orphans first, deliberately. Deleting one needs only its page_id, so it
    # works on any column set this class has ever written; re-indexing writes
    # every column and would raise there, taking the runnable repair with it.
    if drift.orphaned_fts:
        # One transaction for the lot: per-id deletes cost a write lock each.
        await fts.delete_many(list(drift.orphaned_fts))
        repaired += len(drift.orphaned_fts)
    if drift.missing_from_fts:
        async with get_session(sf) as session:
            from sqlalchemy import select

            from repowise.core.persistence.models import Page

            rows = await session.execute(
                select(Page).where(Page.id.in_(list(drift.missing_from_fts)))
            )
            # ORM rows key on ``id``, not ``page_id``, so they go to
            # index_many as tuples rather than to index_pages.
            batch = [
                (p.id, p.title, p.content, p.summary, p.target_path, p.digest)
                for p in rows.scalars().all()
            ]
            await fts.index_many(batch)
            repaired += len(batch)
    return repaired


def _announce_embedding(drift: StoreDrift, embedder_name: str) -> bool:
    """Say what a re-embed will cost; True when doctor should leave it to reindex.

    A hosted embedder here is a charge on a command people run to diagnose,
    not to spend. It is bounded by the missing pages, not the wiki, unless the
    store is empty.
    """
    missing = drift.missing_from_vector
    reindex_instead = bool(missing) and embedder_name != "mock" and drift.vector_store_empty
    if reindex_instead:
        console.print(
            f"  [yellow]The vector store is empty: {len(missing)} "
            f"page(s) need embedding with {embedder_name}. Not starting a "
            "paid re-embed of the whole wiki from doctor; run "
            "`repowise reindex` to do it.[/yellow]"
        )
    elif missing and embedder_name != "mock":
        console.print(
            f"  [dim]Embedding {len(missing)} missing page(s) with {embedder_name}.[/dim]"
        )
    return reindex_instead


async def _embed_missing(sf, vs, missing: set[str]):
    """Yield once per page embedded, so a later failure keeps the count so far."""
    from sqlalchemy import select

    from repowise.core.persistence import get_session
    from repowise.core.persistence.models import Page
    from repowise.core.persistence.vector_store import embed_item

    async with get_session(sf) as session:
        rows = await session.execute(select(Page).where(Page.id.in_(list(missing))))
        for page in rows.scalars().all():
            if not (page.title or "").strip():
                # A vector no search can reach by name is not a repair; leave
                # the page reported as missing.
                console.print(f"  [yellow]Skipped {page.id}: no title to index it by.[/yellow]")
                continue
            # Same recipe as generation and ``reindex``, so a repaired page is
            # comparable with its neighbours.
            item = embed_item(
                page.id,
                title=page.title,
                page_type=page.page_type or "",
                target_path=page.target_path or "",
                summary=page.summary or "",
                content=page.content or "",
                page_metadata=page.metadata_json,
                digest=page.digest or "",
            )
            if item is None:
                # Below the information floor, so its absence is correct. Said
                # out loud so it does not read as the repair having failed.
                console.print(f"  [dim]Left out {page.id}: too thin to index.[/dim]")
                continue
            await vs.embed_and_upsert(*item)
            yield page.id


async def _repair_vectors(sf, repo_path: _DoctorPath, drift: StoreDrift) -> int:
    """Re-embed missing pages and delete orphans; returns entries repaired."""
    repaired = 0
    try:
        from repowise.cli.providers import (
            build_embedder,
            build_vector_store,
            resolve_embedder_for_repo,
        )

        # Repair with the embedder that built this store, not a hardcoded
        # mock: writing 8-wide mock vectors into a real table makes LanceDB
        # drop it. build_vector_store refuses that combination and returns None.
        embedder_name = resolve_embedder_for_repo(repo_path)
        embedder = build_embedder(embedder_name, repo_path)
        vs = build_vector_store(_DoctorPath(repo_path), embedder)
        if vs is None:
            raise RuntimeError(
                "no embedder available that can write this store; "
                "set an embedder key and run `repowise reindex`"
            )
        reindex_instead = _announce_embedding(drift, embedder_name)
        if drift.missing_from_vector and not reindex_instead:
            async for _page_id in _embed_missing(sf, vs, drift.missing_from_vector):
                repaired += 1
        for pid in drift.orphaned_vector:
            await vs.delete(pid)
            repaired += 1
        await vs.close()
    except Exception as exc:
        console.print(f"[yellow]Vector repair skipped: {exc}[/yellow]")
    return repaired


async def _repair_stores(ctx: CheckContext) -> int:
    engine, sf = await open_store(ctx.repo_path)
    drift = ctx.drift
    repaired = 0
    if drift.missing_from_fts or drift.orphaned_fts:
        repaired += await _repair_fts(engine, sf, drift)
    lance_dir = ctx.repowise_dir / "lancedb"
    if lance_dir.exists() and (drift.missing_from_vector or drift.orphaned_vector):
        repaired += await _repair_vectors(sf, ctx.repo_path, drift)
    await engine.dispose()
    return repaired


def _report_store_repair(ctx: CheckContext) -> None:
    has_mismatches = ctx.drift.has_mismatches()
    if has_mismatches:
        console.print("\n[bold]Repairing store mismatches...[/bold]")
        repaired_count = run_async(_repair_stores(ctx))
        console.print(f"[bold green]Repaired {repaired_count} entries.[/bold green]")
        if ctx.stale_count:
            console.print(f"[yellow]{_stale_page_guidance(ctx.stale_counts)}[/yellow]")
    elif not ctx.registration_wedged and not ctx.agents_need_refresh:
        if ctx.stale_count:
            console.print(
                f"[yellow]No store drift to repair. "
                f"{_stale_page_guidance(ctx.stale_counts)}[/yellow]"
            )
        else:
            console.print("[green]Nothing to repair.[/green]")


def _refresh_agents(repo_path: _DoctorPath) -> None:
    from repowise.cli.commands.agents_cmd import refresh_wired_agents

    console.print("\n[bold]Refreshing agent integrations...[/bold]")
    payload = refresh_wired_agents(repo_path)
    for agent in payload["agents"]:
        for scope, write in agent["writes"].items():
            for entry in write["files"]:
                console.print(f"  {agent['id']} [{scope}]: {entry['action']} {entry['path']}")
    if not payload["changed"]:
        console.print(
            "[yellow]Nothing moved. A hook whose matcher is stale needs "
            "[bold]repowise hook rewrite install[/bold]; a damaged config file "
            "has to be fixed or removed by hand.[/yellow]"
        )


def _reregister_claude(repo_path: _DoctorPath) -> None:
    from repowise.cli.editor_integrations.claude_config import register_with_claude_code

    fixed = register_with_claude_code(repo_path)
    if fixed:
        console.print(
            f"[bold green]Re-registered the Claude Code MCP entry ({fixed}).[/bold green]"
        )
    else:
        console.print(
            "[yellow]Could not re-register the Claude Code MCP entry; "
            "run `repowise init` to redo editor setup.[/yellow]"
        )


def run_repair(ctx: CheckContext) -> None:
    """Store drift first, then agent integrations, then the Claude Code entry."""
    _report_store_repair(ctx)
    if ctx.agents_need_refresh:
        _refresh_agents(ctx.repo_path)
    if ctx.registration_wedged:
        _reregister_claude(ctx.repo_path)
