"""Sweeps of generated pages a run no longer produces."""

from __future__ import annotations

import json
from typing import Any

import structlog

from repowise.core.generation.models import (
    STRUCTURALLY_KEYED_PAGE_TYPES,
    STUB_FALLBACK_ERROR,
)

from .prune import _PRUNE_CHUNK

logger = structlog.get_logger("repowise.core.pipeline.persist")


# Generated page types keyed on run-scoped structure: module/scc pages on
# clustering ordinals, layer pages on display names. Those keys shift between
# runs, so re-runs mint fresh page ids and the previous rows linger as
# duplicates unless swept against the current run's output.
#
# Same list generation stamps ``structural_key`` from, imported rather than
# repeated: a type present in one and missing from the other is exactly the
# bug the sweep exists to prevent.
_SWEPT_GENERATED_PAGE_TYPES = STRUCTURALLY_KEYED_PAGE_TYPES


async def sweep_retired_pages(session: Any, repo_id: str) -> list[str]:
    """Delete every row of a page that no longer exists, by type or by id.

    Distinct from the two sweeps below, and simpler than either: those ask what
    *this* run produced, because a page of a live type may legitimately be
    absent from a scoped run. A retired page has no legitimate rows at all —
    nothing emits one and no failure mode can make anything emit one — so the
    question never arises and this is safe on every path, full or scoped.

    The retirement tables are the source of truth rather than a list repeated
    here. A page is retired exactly when a reader following its id gets sent
    somewhere else, and that is what those tables say; keeping a second list
    would let a page be redirected without being swept, which is the state this
    function was written to clear.

    That state is the reason this exists. The architecture diagram merged into
    the overview and layer pages became grouping rows in the tree, both with
    redirects registered — but ``architecture_diagram`` is not structurally
    keyed, so no sweep ever covered it, and ``layer_page`` was only swept by a
    full run. An index that has only been updated incrementally since therefore
    still serves both. On this repository the retired diagram was the
    second-ranked retrieval hit for "how does the ingestion pipeline work",
    competing with the overview it had been merged into.

    Retirement by *id* is the second half of the same argument, and the reason
    this is no longer named for types. Three orientation slots retired out of
    the onboarding collection while five stayed, so their rows cannot be
    reached by page type: ``onboarding`` still has legitimate rows and always
    will. Sweeping the exact ids is the only way those leave a store, and the
    safety argument is unchanged — a retired id names a page nothing emits.

    Returns the swept page ids so the caller can drop them from the vector
    store and from FTS. A row deleted here but left in either of those is worse
    than one never swept: search still answers from the FTS copy, with the full
    title and snippet of a page the reader can no longer open.
    """
    from sqlalchemy import delete, or_, select

    from repowise.core.generation.page_redirects import (
        RETIRED_IDS,
        SUPERSEDED_TO_REPO_WIDE,
        SUPERSEDED_TYPES,
    )
    from repowise.core.persistence.models import Page, PageVersion

    retired_types = sorted(set(SUPERSEDED_TYPES) | set(SUPERSEDED_TO_REPO_WIDE))
    retired_ids = sorted(RETIRED_IDS)
    if not retired_types and not retired_ids:
        return []

    # Either rule can match, and a page matched by both is one row either way.
    match_clauses = []
    if retired_types:
        match_clauses.append(Page.page_type.in_(retired_types))
    if retired_ids:
        match_clauses.append(Page.id.in_(retired_ids))

    stale = (
        (
            await session.execute(
                select(Page.id).where(Page.repository_id == repo_id, or_(*match_clauses))
            )
        )
        .scalars()
        .all()
    )
    for i in range(0, len(stale), _PRUNE_CHUNK):
        batch = stale[i : i + _PRUNE_CHUNK]
        await session.execute(delete(PageVersion).where(PageVersion.page_id.in_(batch)))
        await session.execute(delete(Page).where(Page.repository_id == repo_id, Page.id.in_(batch)))
    if stale:
        logger.info(
            "retired_pages_swept",
            repo_id=repo_id,
            count=len(stale),
            types=retired_types,
            ids=retired_ids,
        )
    return list(stale)


async def _sweep_stale_generated_pages(
    session: Any,
    repo_id: str,
    generated_pages: list[Any] | None,
    authoritative_page_types: set[str] | None = None,
    preserved_page_ids: set[str] | None = None,
) -> list[str]:
    """Delete structurally-keyed generated pages this run did not produce.

    Sweeps a page type when the run either produced at least one page of it OR
    declared itself authoritative for it (``authoritative_page_types`` — set by
    the generation layer when it fully decided the type, even if that decision
    was "emit none"; e.g. a curated run whose modules all collapsed into their
    layers via ``wholeLayer``). A type that is neither produced nor authoritative
    is left untouched, so a skipped/failed/degraded level never wipes the last
    good set. When authoritative-but-empty, the current set is empty and every
    prior row of that type is retired. Page versions go with their page (FK
    enforcement requires it, and a retired structural id never comes back to
    claim its history). Returns the swept page ids so the caller can drop them
    from FTS after the session closes (the FTS store must not be touched
    in-session).

    ``preserved_page_ids`` are ids a ``--resume`` run skipped *because they
    already exist* (see ``_GenerationRun._emit``). They are absent from
    ``generated_pages`` by design, which is exactly what "stale" means to the
    rest of this function, so without this argument a resume that regenerated
    the missing half of a page type deleted the half it had just protected
    (issue #1089). A preserved id is as current as a produced one: the id did
    not drift, which is the only thing this sweep exists to catch.
    """
    from sqlalchemy import delete, select

    from repowise.core.persistence.models import Page, PageVersion

    produced: dict[str, set[str]] = {}
    for page in generated_pages or []:
        produced.setdefault(page.page_type, set()).add(page.page_id)
    authoritative = authoritative_page_types or set()
    preserved = preserved_page_ids or set()

    swept: list[str] = []
    for page_type in _SWEPT_GENERATED_PAGE_TYPES:
        current = produced.get(page_type)
        if not current and page_type not in authoritative:
            continue
        # Preserved ids join the current set rather than being subtracted from
        # the stale one: an authoritative type sweeps even when the run emitted
        # none of it, and that branch must still keep what a resume stood on.
        # (Defensive today — no caller sets both — but the two are independent
        # inputs and the failure mode if they ever meet is a deleted wiki.)
        # Filtered by type because ``preserved`` spans every page type while
        # this loop compares against one. Ids happen to be ``type:target`` so
        # a cross-type match is impossible, but the sweep is the wrong place to
        # depend on an id format nothing here enforces.
        prefix = f"{page_type}:"
        current = (current or set()) | {p for p in preserved if p.startswith(prefix)}
        existing = (
            (
                await session.execute(
                    select(Page.id).where(
                        Page.repository_id == repo_id, Page.page_type == page_type
                    )
                )
            )
            .scalars()
            .all()
        )
        stale = [pid for pid in existing if pid not in current]
        for i in range(0, len(stale), _PRUNE_CHUNK):
            batch = stale[i : i + _PRUNE_CHUNK]
            await session.execute(delete(PageVersion).where(PageVersion.page_id.in_(batch)))
            await session.execute(
                delete(Page).where(Page.repository_id == repo_id, Page.id.in_(batch))
            )
        swept.extend(stale)

    if swept:
        logger.info("stale_generated_pages_swept", repo_id=repo_id, count=len(swept))
    return swept


async def sweep_absent_cycle_pages(session: Any, repo_id: str, graph_builder: Any) -> list[str]:
    """Delete ``scc_page`` rows whose cycle no longer exists in the graph.

    The other sweeps ask "did this run *produce* this page?", which a scoped run
    cannot answer: ``repowise update`` runs with ``file_pages_only`` and never
    reaches level 3, so it emits no ``scc_page`` at all and every prior row
    looks stale to that question. ``_sweep_stale_generated_pages`` therefore
    gates on the run declaring itself authoritative, and only a keyless
    (``deterministic``) run does.

    Between them those two rules leave a cycle page immortal on the paths that
    matter: an update never regenerates it, and a keyed full re-index of a repo
    whose cycles all disappeared never claims authority to retire it. A user
    upgrading into a build that fixes a cycle-detection bug keeps being served
    the cycles it fixed.

    This asks a different question — "does the graph still contain this cycle?"
    — which the rebuilt graph answers directly and identically on every path,
    with no dependence on what generation chose to emit or what budget it had.
    A cycle's page id is a hash of its sorted members
    (:func:`~repowise.core.generation.models.scc_page_slug`), so the current
    cycle set names exactly the ids that may survive.

    Returns the deleted page ids so the caller can drop them from FTS and the
    vector store after the session closes.
    """
    from sqlalchemy import delete, select

    from repowise.core.generation.models import compute_page_id, scc_page_slug
    from repowise.core.persistence.models import Page, PageVersion

    if graph_builder is None:
        return []
    try:
        sccs = graph_builder.strongly_connected_components()
    except Exception:  # a released graph has no cycles to speak for
        return []

    valid = {
        compute_page_id("scc_page", scc_page_slug(sorted(scc))) for scc in sccs if len(scc) > 1
    }
    existing = (
        (
            await session.execute(
                select(Page.id).where(Page.repository_id == repo_id, Page.page_type == "scc_page")
            )
        )
        .scalars()
        .all()
    )
    stale = [pid for pid in existing if pid not in valid]
    for i in range(0, len(stale), _PRUNE_CHUNK):
        batch = stale[i : i + _PRUNE_CHUNK]
        await session.execute(delete(PageVersion).where(PageVersion.page_id.in_(batch)))
        await session.execute(delete(Page).where(Page.repository_id == repo_id, Page.id.in_(batch)))
    if stale:
        logger.info("absent_cycle_pages_swept", repo_id=repo_id, count=len(stale))
    return stale


def _symbol_names_by_file(graph_builder: Any) -> dict[str, set[str]]:
    """``file path -> names of the symbols it defines`` in the rebuilt graph.

    Empty when there is no graph to ask. The synthetic ``__module__`` symbol is
    left out, so a file that failed to parse has no entry at all.
    """
    if graph_builder is None:
        return {}
    try:
        graph = graph_builder.graph()
    except Exception:  # a released graph cannot speak for any symbol
        return {}
    names_by_file: dict[str, set[str]] = {}
    for _node, data in graph.nodes(data=True):
        if data.get("node_type") != "symbol" or data.get("kind") == "module":
            continue
        path, name = data.get("file_path"), data.get("name")
        if path and name:
            names_by_file.setdefault(path, set()).add(name)
    return names_by_file


async def sweep_absent_symbol_spotlights(
    session: Any, repo_id: str, graph_builder: Any
) -> list[str]:
    """Delete ``symbol_spotlight`` rows whose symbol left a file that still exists.

    A spotlight targets ``<path>::<name>``. Deleting the file tombstones it
    (:func:`mark_tombstone_pages`), but moving or removing the symbol while the
    file stays left the row ``fresh``: nothing regenerates a page for a symbol
    the parser no longer emits, so it kept serving the old body and the old
    importer list.

    Same question as :func:`sweep_absent_cycle_pages`, asked of the rebuilt
    graph: does this file still define a symbol of that name? Only files the
    graph holds with at least one named symbol are judged, so a file outside
    the traversal or one that failed to parse keeps its pages.

    Returns the deleted page ids so the caller can drop them from FTS and the
    vector store after the session closes.
    """
    from sqlalchemy import delete, select

    from repowise.core.persistence.models import Page, PageVersion

    names_by_file = _symbol_names_by_file(graph_builder)
    if not names_by_file:
        return []

    rows = (
        await session.execute(
            select(Page.id, Page.target_path).where(
                Page.repository_id == repo_id,
                Page.page_type == "symbol_spotlight",
                Page.freshness_status != "tombstone",
            )
        )
    ).all()
    stale: list[str] = []
    for page_id, target in rows:
        path, sep, name = (target or "").partition("::")
        names = names_by_file.get(path)
        if sep and names is not None and name not in names:
            stale.append(page_id)
    for i in range(0, len(stale), _PRUNE_CHUNK):
        batch = stale[i : i + _PRUNE_CHUNK]
        await session.execute(delete(PageVersion).where(PageVersion.page_id.in_(batch)))
        await session.execute(delete(Page).where(Page.repository_id == repo_id, Page.id.in_(batch)))
    if stale:
        logger.info("absent_symbol_spotlights_swept", repo_id=repo_id, count=len(stale))
    return stale


# Hysteresis: a spotlight is retired only past this multiple of the selection's cut.
SPOTLIGHT_RETIRE_MARGIN = 1.5


async def sweep_deselected_pages(
    session: Any,
    repo_id: str,
    parsed_files: list[Any] | None,
    graph_builder: Any,
    config: Any,
    git_meta_map: dict | None = None,
) -> list[str]:
    """Delete template file pages and spotlights the selection no longer picks.

    An update renders only changed files, so nothing else revisits these. Only
    files in this run's parse are judged; model-written pages are left alone.
    *git_meta_map* (this run's rows) overrides the stored hotspot flags.
    """
    from sqlalchemy import delete, select

    from repowise.core.generation.page_generator.helpers import _select_clone_representatives
    from repowise.core.generation.selection.selector import (
        SelectionInputs,
        _is_code_file,
        file_page_eligible,
        rank_symbol_candidates,
    )
    from repowise.core.persistence.models import GitMetadata, Page, PageVersion

    parsed_by_path = {p.file_info.path: p for p in parsed_files or []}
    if not parsed_by_path or graph_builder is None:
        return []
    try:
        pagerank = graph_builder.pagerank()
    except Exception:  # a released graph cannot rank anything
        return []

    rows = (
        await session.execute(
            select(Page.id, Page.page_type, Page.target_path).where(
                Page.repository_id == repo_id,
                Page.page_type.in_(("file_page", "symbol_spotlight")),
                Page.freshness_status != "tombstone",
                Page.provider_name == "template",
            )
        )
    ).all()
    file_rows = [(pid, t) for pid, kind, t in rows if kind == "file_page" and t in parsed_by_path]
    spot_rows = [(pid, t) for pid, kind, t in rows if kind == "symbol_spotlight"]

    retired: list[str] = []
    if file_rows:
        hotspots = set(
            (
                await session.execute(
                    select(GitMetadata.file_path).where(
                        GitMetadata.repository_id == repo_id,
                        GitMetadata.is_hotspot.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        for path, meta in (git_meta_map or {}).items():
            if meta.get("is_hotspot"):
                hotspots.add(path)
            else:
                hotspots.discard(path)
        retired += [
            pid
            for pid, path in file_rows
            if not file_page_eligible(parsed_by_path[path], is_hotspot=path in hotspots)
        ]

    if spot_rows:
        # The same candidate pool generation ranks: near-clone losers out first.
        parsed = list(parsed_by_path.values())
        if getattr(config, "dedupe_near_clones", True):
            losers = _select_clone_representatives(
                [p for p in parsed if _is_code_file(p)], pagerank
            )
            parsed = [p for p in parsed if p.file_info.path not in losers]
        ranked, keep = rank_symbol_candidates(
            SelectionInputs(
                parsed_files=parsed,
                pagerank=pagerank,
                betweenness={},
                community={},
                community_info=None,
                sccs=[],
                git_meta_map={},
                config=config,
            )
        )
        kept = {target for _, target in ranked[: int(keep * SPOTLIGHT_RETIRE_MARGIN)]}
        for pid, target in spot_rows:
            path, sep, name = (target or "").partition("::")
            if sep and path in parsed_by_path and (path, name) not in kept:
                retired.append(pid)

    for i in range(0, len(retired), _PRUNE_CHUNK):
        batch = retired[i : i + _PRUNE_CHUNK]
        await session.execute(delete(PageVersion).where(PageVersion.page_id.in_(batch)))
        await session.execute(delete(Page).where(Page.repository_id == repo_id, Page.id.in_(batch)))
    if retired:
        logger.info("deselected_pages_swept", repo_id=repo_id, count=len(retired))
    return retired


async def sweep_superseded_generated_pages(
    session: Any,
    repo_id: str,
    generated_pages: list[Any] | None,
) -> list[str]:
    """Retire structurally-keyed rows whose coverage a scoped run just took over.

    :func:`_sweep_stale_generated_pages` cannot be used on the update path. It
    deletes every row of a page type the run did not reproduce, which is right
    for a full index and catastrophic for a scoped one: regenerating a single
    page would wipe the other fifty.

    That left a real gap. A structurally-keyed page's id is its
    ``target_path``, and a page that groups files can legitimately change which
    directory names it when its membership changes. ``repowise update`` goes
    through ``scoped_generation`` and never swept, so the regenerated page was
    written under its new id and the old row stayed behind as a duplicate until
    somebody ran a full index. Harmless while module and cycle pages were never
    regenerated incrementally, and not harmless once concept pages are.

    The rule here is narrower and does not need to know what the caller asked
    for: a prior row is superseded when **every file it covered is now covered
    by pages this run produced**, of the same type. That is exactly the
    condition under which the old row documents nothing that is not documented
    elsewhere. A page that failed to generate keeps files no produced page
    claims, so it survives — which matters, because deleting a page because its
    generation errored would be a worse bug than the one this fixes.

    Returns the swept page ids so the caller can drop them from FTS after the
    session closes.
    """
    from sqlalchemy import delete, select

    from repowise.core.persistence.models import Page, PageVersion

    produced_ids: dict[str, set[str]] = {}
    covered: dict[str, set[str]] = {}
    for page in generated_pages or []:
        page_type = page.page_type
        if page_type not in _SWEPT_GENERATED_PAGE_TYPES:
            continue
        # A stub standing in for a failed provider call is the "failed to
        # generate" case this function's contract already carves out: it claims
        # no files, so a prior row covering them is not superseded. Counting its
        # members would retire the very page whose regeneration just failed, and
        # take its version history with it.
        if STUB_FALLBACK_ERROR in (getattr(page, "metadata", None) or {}):
            continue
        produced_ids.setdefault(page_type, set()).add(page.page_id)
        metadata = getattr(page, "metadata", None) or {}
        members = metadata.get("file_paths") or metadata.get("files") or []
        covered.setdefault(page_type, set()).update(m for m in members if isinstance(m, str))

    swept: list[str] = []
    for page_type, current in produced_ids.items():
        union = covered.get(page_type) or set()
        if not union:
            # Nothing to compare against: a run that recorded no membership
            # cannot prove it superseded anything, so it retires nothing.
            continue
        rows = (
            await session.execute(
                select(Page.id, Page.metadata_json).where(
                    Page.repository_id == repo_id, Page.page_type == page_type
                )
            )
        ).all()
        stale: list[str] = []
        for page_id, raw in rows:
            if page_id in current:
                continue
            try:
                metadata = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (TypeError, ValueError):
                continue
            if not isinstance(metadata, dict):
                continue
            members = metadata.get("file_paths") or metadata.get("files") or []
            members = {m for m in members if isinstance(m, str)}
            # No recorded membership means no proof of supersession. Those rows
            # are the pre-membership ones (backlog B20) and they are left for a
            # full index to retire.
            if members and members <= union:
                stale.append(page_id)
        for i in range(0, len(stale), _PRUNE_CHUNK):
            batch = stale[i : i + _PRUNE_CHUNK]
            await session.execute(delete(PageVersion).where(PageVersion.page_id.in_(batch)))
            await session.execute(
                delete(Page).where(Page.repository_id == repo_id, Page.id.in_(batch))
            )
        swept.extend(stale)

    if swept:
        logger.info("superseded_generated_pages_swept", repo_id=repo_id, count=len(swept))
    return swept
