"""The stale pages an update can regenerate, which are the only ones that wake it."""

from __future__ import annotations

from repowise.core.docs_mode import resolve_docs_mode

from .options import UpdateOptions, UpdateRun


def can_regenerate_pages(run: UpdateRun, opts: UpdateOptions) -> bool:
    """Whether this run's mode writes pages at all."""
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


def structure_ids_to_render(run: UpdateRun, existing_ids: object) -> set[str]:
    """The whole-repo structure pages a rendering update renders by id.

    The stale ones, plus the cycle, API-contract and infra pages the rebuilt
    graph selects that the store (*existing_ids*) lacks or whose file changed.
    """
    from repowise.core.generation.selection.selector import structure_ids_for_incremental_run

    rendered = run.affected.regenerate if run.affected is not None else []
    return set(run.stale_deterministic_ids) | structure_ids_for_incremental_run(
        run.parsed_files or [], run.graph_builder, existing_ids or (), rendered
    )
