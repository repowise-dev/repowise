"""Telling a user that their index predates an orientation page.

An onboarding slot registered after an index was built cannot arrive on the
incremental ``repowise update`` path, and must not try: that run's
``parsed_files`` is the changed-file slice, and the slot gates read whole-repo
signals.  A Glossary mined from a truncated vocabulary scan is worse than an
absent one, which is why ``update_cmd.command`` sets ``file_pages_only`` and
the repo-wide levels are skipped.

``repowise update --full`` is the command that does reach them.  It re-parses
the whole repository, rehydrates the graph from SQL instead of re-resolving it,
runs the onboarding level with every registered slot, and keeps the index.
Measured on ``test-repos/microdot`` (2026-08-04): a slot deleted from a
persisted index came back on the next ``update --full``, and the resulting
orientation was identical to a fresh ``repowise init`` of the same repository.
So the remedy this module names is ``--full``, never a re-index. Nobody should
be told to throw away an index to gain a page.

**A missing row is not the same as a missing slot.** Three of microdot's five
registered slots are refused by their own signal gates on every run, fresh or
full; the glossary, for one, mines no house vocabulary there at all.  Reporting
those would promise a page that no command can deliver.  So the comparison is
against :data:`~repowise.cli.helpers.ONBOARDING_SLOTS_OFFERED_KEY`, the slots
the last whole-repo run actually evaluated, and only a slot that has never been
evaluated here is reported.  An index written before that key existed has
no such record, and there the persisted rows are the only evidence available;
the notice degrades to naming what has no row, and one ``--full`` resolves it
either way by writing the record.

Driven by ``iter_specs()`` rather than a second list, for the reason the
retirement sweep reads the retirement tables: a slot added without this
following is exactly the state the notice exists to report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from repowise.cli.helpers import ONBOARDING_SLOTS_OFFERED_KEY, console, load_state
from repowise.cli.upgrade import SHOWN_NOTICES_KEY

log = structlog.get_logger(__name__)

#: Ledger entry recording that this missing-slot set has been surfaced.  Shares
#: the reindex recommendation's ledger, since it is the same question ("has
#: this store been told?"), namespaced so the two entries cannot collide.
_LEDGER_PREFIX = "onboarding_slots:"


def _registered_slots() -> list[str]:
    from repowise.core.generation.onboarding import iter_specs

    return [spec.slot for spec in iter_specs()]


def _slot_title(slot: str) -> str:
    from repowise.core.generation.onboarding.slots import SLOT_TITLES

    return SLOT_TITLES.get(slot, slot)


async def _persisted_slots(repo_path: Path) -> set[str]:
    """Onboarding slots this store holds a page for.

    Only consulted for an index written before the offered-slots record
    existed, where it is the sole evidence of what was ever built.

    Scoped to this repository rather than to the whole store: the database is
    repo-local by default but ``REPOWISE_DB_URL`` can point several repos at
    one, and an unscoped read would let another repo's Glossary silence this
    one's notice.
    """
    from sqlalchemy import select

    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.generation.onboarding import target_path
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.models import Page, Repository

    by_target = {target_path(slot): slot for slot in _registered_slots()}
    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        session_factory = create_session_factory(engine)
        async with get_session(session_factory) as session:
            repo_id = (
                await session.execute(
                    select(Repository.id).where(Repository.local_path == str(repo_path))
                )
            ).scalar_one_or_none()
            if repo_id is None:
                # No repository row: this store has never been written for this
                # path, so it holds no evidence either way.
                raise LookupError(f"no repository row for {repo_path}")
            rows = (
                await session.execute(
                    select(Page.target_path).where(
                        Page.repository_id == repo_id,
                        Page.page_type == "onboarding",
                    )
                )
            ).scalars().all()
    finally:
        await engine.dispose()
    return {by_target[target] for target in rows if target in by_target}


def missing_slots(repo_path: Path) -> list[str]:
    """Registered onboarding slots this index has never been offered.

    Returns them in ``ONBOARDING_ORDER``, which is reading order, so a notice
    listing several names them the way the wiki would.
    """
    from repowise.cli.helpers import load_config, run_async

    if not bool(load_config(repo_path).get("enable_onboarding", True)):
        # The user turned the collection off.  Every slot is "missing" and none
        # of it is news.
        return []

    registered = _registered_slots()
    state = load_state(repo_path)
    offered = state.get(ONBOARDING_SLOTS_OFFERED_KEY)
    if isinstance(offered, list):
        known = {str(slot) for slot in offered}
    else:
        try:
            known = run_async(_persisted_slots(repo_path))
        except Exception as exc:
            # No record and no readable store: nothing can be claimed honestly.
            log.debug("onboarding_slot_notice.store_unreadable", error=str(exc))
            return []
    return [slot for slot in registered if slot not in known]


def surface_missing_slots(repo_path: Path, *, emitter: Any, dry_run: bool) -> None:
    """Name the orientation pages this index has never been offered, once.

    Read-only except for the shown-notice ledger, so it is safe on the no-op
    ("already up to date") path as well as the main one.  That path is the one
    that matters most here, because a repository with no new commits never
    reaches the generation code at all and would otherwise never be told.

    Interactive-terminal only, matching the reindex recommendation: a
    background post-commit update must not burn the one-shot into a log nobody
    reads.  The ledger is keyed by the missing set itself, so registering a
    further slot later says so again rather than staying quiet.
    """
    try:
        missing = missing_slots(repo_path)
    except Exception as exc:  # a notice must never break an update
        log.debug("onboarding_slot_notice.skipped", error=str(exc))
        return
    if not missing:
        return
    if not (console.is_terminal and emitter is None):
        return

    key = _LEDGER_PREFIX + ",".join(sorted(missing))
    state = load_state(repo_path)
    shown = state.get(SHOWN_NOTICES_KEY)
    if isinstance(shown, list) and key in shown:
        return

    titles = ", ".join(_slot_title(slot) for slot in missing)
    noun = "page" if len(missing) == 1 else "pages"
    console.print(f"[yellow]Orientation {noun} not in this index:[/yellow] {titles}")
    # "the ones this repository has the signal for", not "these": on a store
    # with no offered-slots record the rows are the only evidence, and a slot
    # can be absent because its gate refused the repository rather than because
    # nothing ever offered it. Promising the page outright would be a lie on
    # every such repo, and there is no way to tell the two apart from here.
    console.print(
        "[dim]A routine update only regenerates what changed, so it cannot build "
        "these. [bold]repowise update --full[/bold] re-reads the whole repository "
        "and writes the ones it has the signal for. It keeps the index.[/dim]"
    )

    if dry_run:
        return
    try:
        from repowise.cli.helpers import save_state

        state = load_state(repo_path)
        shown = state.get(SHOWN_NOTICES_KEY)
        shown = list(shown) if isinstance(shown, list) else []
        if key not in shown:
            shown.append(key)
            state[SHOWN_NOTICES_KEY] = shown
            save_state(repo_path, state)
    except Exception as exc:  # a ledger write must never break a command
        log.debug("onboarding_slot_notice.record_failed", error=str(exc))
