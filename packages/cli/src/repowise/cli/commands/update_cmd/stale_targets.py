"""The stale pages an update can regenerate, which are the only ones that wake it."""

from __future__ import annotations

from repowise.core.docs_mode import resolve_docs_mode

from .options import UpdateOptions, UpdateRun


def can_regenerate_pages(run: UpdateRun, opts: UpdateOptions) -> bool:
    """Whether this run's mode writes pages at all."""
    if opts.defer_docs:
        return False
    if run.index_only:
        # Index-only renders template wikis only; model pages wait for a model run.
        return resolve_docs_mode(run.state) == "deterministic"
    # An explicit --provider or --docs asked for a model: resolve (or prompt) as before.
    if opts.provider_name is not None or opts.docs_flag is True:
        return True
    from repowise.core.providers.llm.registry import provider_available_for_repo

    return provider_available_for_repo(run.repo_path)


def load_actionable_stale_targets(run: UpdateRun, opts: UpdateOptions) -> int:
    """Set ``run.stale_*`` to the stale pages this run heals; return how many it leaves stale.

    Pages this mode cannot regenerate stay marked stale but must not wake the
    pipeline, or every no-op update re-indexes the whole repo for nothing.
    """
    from repowise.core.persistence import load_stale_update_targets

    paths, page_ids = load_stale_update_targets(run.repo_path)
    if not (paths or page_ids) or can_regenerate_pages(run, opts):
        run.stale_db_paths, run.stale_deterministic_ids = paths, page_ids
        return 0
    # A page whose file is gone is index work: the deletion path prunes it in any mode.
    absent = [path for path in paths if not (run.repo_path / path).exists()]
    run.stale_db_paths, run.stale_deterministic_ids = absent, set()
    return len(paths) - len(absent) + len(page_ids)


async def defer_renderer_pages(run: UpdateRun) -> None:
    """A docs-only renderer change needs no graph rebuild to mark its pages."""
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_repository_by_path,
        get_session,
    )
    from repowise.core.pipeline.persist.tombstones import mark_deferred_pages

    engine = create_engine(get_db_url_for_repo(run.repo_path))
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(run.repo_path))
            if repo is not None:
                await mark_deferred_pages(
                    session, repo.id, scope="template", repo_path=run.repo_path
                )
    finally:
        await engine.dispose()


def structure_ids_to_render(run: UpdateRun, existing_ids: object) -> set[str]:
    """The whole-repo structure pages a rendering update renders by id.

    The stale ones, plus the cycle, API-contract and infra pages the rebuilt
    graph selects that the store (*existing_ids*) lacks or whose file changed,
    plus the selected flow pages the store has no visible row for. The flow
    term lives here, not in the shared selector: a flow page calls the model
    unless the template renderer writes it, and the server sync renders the
    shared ids with a model.
    """
    from repowise.core.generation.selection.selector import structure_ids_for_incremental_run

    rendered = run.affected.regenerate if run.affected is not None else []
    return (
        set(run.stale_deterministic_ids)
        | structure_ids_for_incremental_run(
            run.parsed_files or [], run.graph_builder, existing_ids or (), rendered
        )
        | _unwritten_flow_ids(run)
    )


def _unwritten_flow_ids(run: UpdateRun) -> set[str]:
    """The selected flow pages the store has no visible row for.

    Read from the store here, not taken from the caller's page map: that map is
    empty after a failed read and, on a model wiki, leaves out the pages whose
    model call failed, and rendering either kind would put template text over a
    page a model wrote or over the failure marker ``doctor`` reads. A store
    that cannot be read therefore adds no page.
    """
    from repowise.cli.helpers import run_async
    from repowise.core.generation.flow_pages import selected_flow_page_ids

    selected = selected_flow_page_ids(run.graph_builder, run.parsed_files or [])
    if not selected:
        return set()
    try:
        written = run_async(_visible_flow_page_ids(run.repo_path))
    except Exception as exc:
        run.degraded.append(f"New flow pages: {exc}")
        return set()
    return set() if written is None else selected - written


async def _visible_flow_page_ids(repo_path: object) -> set[str] | None:
    """Ids of the flow pages a reader can open, ``None`` for an unregistered repository.

    A tombstone is left out: it hides the page of an entry a full generation
    dropped, and an entry selected again needs a page before the row expires.
    """
    from sqlalchemy import select

    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import get_repository_by_path
    from repowise.core.persistence.models import Page

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return None
            rows = await session.execute(
                select(Page.id).where(
                    Page.repository_id == repo.id,
                    Page.page_type == "flow_page",
                    # shortcut: a model-written tombstone is re-rendered, restore to reuse its prose
                    Page.freshness_status != "tombstone",
                )
            )
            return set(rows.scalars())
    finally:
        await engine.dispose()


def stale_flow_pages(run: UpdateRun) -> int:
    """Decay the flow pages whose traced steps sit in a changed file.

    A walkthrough cites its steps by line, so any edit to a step's file can move
    what it points at. The stored page's own ``file_paths`` decide, not this
    commit's selection: a page whose entry was renamed away still names the
    files it was written from. A page still in its keyless rendering is
    re-rendered in this run from the full graph, which costs nothing; one a
    model wrote is only marked stale, and rewriting it is ``repowise generate
    --stale``, the same opt-in every model-written page takes. Returns how many
    were touched.
    """
    changed = {fd.path for fd in run.file_diffs} | {
        fd.old_path for fd in run.file_diffs if getattr(fd, "old_path", None)
    }
    if not changed:
        return 0
    from repowise.cli.helpers import run_async

    try:
        touched, stubs = run_async(_decay_flow_pages(run.repo_path, changed))
    except Exception as exc:
        run.degraded.append(f"Flow page staleness: {exc}")
        return 0
    run.stale_deterministic_ids = set(run.stale_deterministic_ids) | stubs
    return touched


async def _decay_flow_pages(repo_path: object, changed: set[str]) -> tuple[int, set[str]]:
    """Mark the flow pages walking through *changed* stale; return (count, keyless ids)."""
    import json

    from sqlalchemy import select

    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import get_repository_by_path
    from repowise.core.persistence.models import Page
    from repowise.core.pipeline.persist import mark_page_ids_stale

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return 0, set()
            rows = await session.execute(
                select(Page.id, Page.provider_name, Page.metadata_json).where(
                    Page.repository_id == repo.id, Page.page_type == "flow_page"
                )
            )
            touched = {
                (page_id, provider)
                for page_id, provider, meta in rows
                if changed.intersection(json.loads(meta or "{}").get("file_paths") or ())
            }
            await mark_page_ids_stale(session, repo.id, {page_id for page_id, _ in touched})
            return len(touched), {pid for pid, provider in touched if provider == "template"}
    finally:
        await engine.dispose()
