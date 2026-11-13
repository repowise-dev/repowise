"""``repowise update --full`` on a repo a model already documented: leave nothing stale.

A plain update caps how many dependent pages it re-renders and only marks the
pages a model wrote as stale. ``--full`` on a full index runs the same update
with no cap, renders any structural page still stale, then has the model
rewrite every stale page it wrote, through ``repowise generate --stale``'s own
planner and cost question.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Any

from repowise.cli.helpers import console, load_config, load_state, run_async
from repowise.core.docs_mode import resolve_docs_mode
from repowise.core.index_scope import resolve_index_scope

from .options import UpdateOptions

#: No cascade cap: every dependent page of the change is re-rendered this run.
UNCAPPED_CASCADE = sys.maxsize


def is_full_index(repo_path: Path) -> bool:
    """A model wrote the documentation and the git history is full.

    An Index only repo (``--mode fast``) or one whose documentation was built
    from code takes the upgrade instead.
    """
    state = load_state(repo_path)
    scope = resolve_index_scope(state, load_config(repo_path))
    return resolve_docs_mode(state) == "llm" and scope["git_tier"] != "essential"


def catch_up_command(repo_path: Path) -> str:
    """``repowise update --full``, aimed at this repo's alias when it is a workspace member."""
    from repowise.core.index_scope import fast_to_full_command

    try:
        from repowise.cli.helpers import find_workspace_root
        from repowise.core.workspace import WorkspaceConfig

        from .command import _workspace_alias

        ws_root = find_workspace_root(repo_path)
        if ws_root is None:
            return fast_to_full_command()
        return fast_to_full_command(_workspace_alias(ws_root, WorkspaceConfig.load(ws_root), repo_path))
    except Exception:
        # A hint never fails the run; the bare command still works inside the repo.
        return fast_to_full_command()


def catch_up(opts: UpdateOptions, repo_path: Path) -> Any:
    """Bring the commits in uncapped, refresh structural pages, rewrite stale model pages."""
    from .command import UpdateOutcome, run_update

    console.print(f"[bold]repowise update --full[/bold]: bringing {repo_path} fully up to date")

    def update() -> Any:
        return run_update(
            path=str(repo_path),
            provider_name=opts.provider_name,
            model=opts.model,
            since=opts.since,
            reasoning=opts.reasoning,
            cascade_budget=UNCAPPED_CASCADE,
            dry_run=opts.dry_run,
            workspace=False,
            no_workspace=True,
            repo_alias=None,
            agents_md=opts.agents_md,
            concurrency=opts.concurrency,
            no_cost_tracking=opts.no_cost_tracking,
            verbose=opts.verbose,
            progress=opts.progress,
            skip_cross_repo_hooks=opts.skip_cross_repo_hooks,
            include_working_tree=opts.include_working_tree,
        )

    outcome = update()
    if outcome == UpdateOutcome.DEFERRED:
        return outcome

    if _stale(repo_path, model_written=True):
        from repowise.cli.commands.generate_cmd.command import run_generate

        console.print()
        # "full": the overview and other repo-wide pages are rewritten too, so
        # the run leaves none of them stale.
        written = run_generate(
            str(repo_path),
            stale=True,
            cascade="full",
            provider_name=opts.provider_name,
            model=opts.model,
            reasoning=opts.reasoning,
            concurrency=opts.concurrency,
            dry_run=opts.dry_run,
            yes=opts.yes,
        )
        if written is not None and not opts.dry_run:
            _retire_unproduced(repo_path, written)
    # Pages the change reached only at a distance (two hops, co-change) were
    # marked stale above, not rendered; a plain pass renders them, free.
    if not opts.dry_run and _stale(repo_path, model_written=False):
        update()
    if not opts.dry_run:
        _report_remaining(repo_path)
    return outcome


def _retire_unproduced(repo_path: Path, written: Any) -> None:
    """Retire the stale pages the generator was asked for and no longer yields.

    Rewriting cannot refresh them, so left alone they would stay stale for good.
    """
    produced = {page.page_id for page in written.generated_pages}
    gone = (written.stale_page_ids & written.plan.generate_ids) - produced
    if gone:
        retired = run_async(_retire(repo_path, gone))
        console.print(f"Retired {len(retired)} page(s) the code no longer yields.")


async def _retire(repo_path: Path, page_ids: set[str]) -> list[str]:
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        get_repository_by_path,
        get_session,
    )
    from repowise.core.pipeline.persist.tombstones import retire_page_ids

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return []
            retired = await retire_page_ids(session, repo.id, page_ids)
        if retired:
            # Search skips a retired page anyway; this only frees its fetch slot.
            with contextlib.suppress(Exception):
                await FullTextSearch(engine).delete_many(retired)
        return retired
    finally:
        await engine.dispose()


def _stale(repo_path: Path, *, model_written: bool) -> int:
    from repowise.cli.commands._page_freshness import stale_page_count

    return run_async(stale_page_count(repo_path, model_written=model_written)) or 0


def _report_remaining(repo_path: Path) -> None:
    from repowise.cli.commands._page_freshness import stale_page_count

    left = run_async(stale_page_count(repo_path))
    if left == 0:
        console.print("[green]Up to date: no stale pages.[/green]")
    elif left:
        console.print(
            f"[yellow]{left} page(s) are still stale.[/yellow] "
            "Run [bold]repowise doctor[/bold] to see why."
        )
