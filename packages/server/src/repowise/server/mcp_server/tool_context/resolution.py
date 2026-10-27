"""The get_context resolution ladder: which kind of thing a target names.

Each resolver either answers (a ``ResolvedTarget`` to build a card from, or a
finished error/degrade payload) or returns None to fall through. They run in
``_RESOLVERS`` order and the first answer wins; ``_not_found`` answers last.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import (
    GitMetadata,
    GraphNode,
    Page,
    Repository,
    WikiSymbol,
)
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._directory import _dir_prefix, _indexed_under
from repowise.server.mcp_server._helpers import (
    LIKE_ESCAPE,
    escape_like,
    filter_path_list,
    filter_rows_by_attr,
    is_excluded,
)
from repowise.server.mcp_server._references import symbol_identity
from repowise.server.mcp_server._symbol_lookup import (
    nearest_symbol_ids,
    resolve_symbol_rows,
    symbol_id_variants,
)
from repowise.server.mcp_server.tool_context.preview import _file_preview


@dataclass(frozen=True)
class TargetQuery:
    """One get_context target and the call options a resolver may need."""

    session: AsyncSession
    repository: Repository
    target: str
    include: set[str] | None
    compact: bool = False
    exclude_spec: Any = None
    repo_root: Any = None
    collector: OmissionCollector | None = None
    as_of_ts: datetime | None = None

    @property
    def repo_id(self) -> str:
        return self.repository.id

    @property
    def clean_target(self) -> str:
        """The target with a directory's trailing slash dropped."""
        return self.target.rstrip("/")


@dataclass
class ResolvedTarget:
    """What a target resolved to; the card is built from these fields."""

    target_type: str
    page: Page | None = None
    # The file whose git history, freshness and layer the card reads.
    file_path: str | None = None
    sym_matches: list[Any] = field(default_factory=list)
    # The resolved symbol's graph id. Graph queries key on it, never on the
    # caller's spelling, so ``Class.method`` and ``Class::method`` agree.
    symbol_node_id: str | None = None
    # Set only when a symbol target resolved through the call graph rather than
    # the symbol index (index-only mode); carries the fields the node has.
    graph_symbol: GraphNode | None = None
    live_file_meta: GitMetadata | None = None
    live_unindexed_file: bool = False
    # A module's directory: its page's path, or the directory itself with no page.
    dir_path: str | None = None


Resolution = ResolvedTarget | dict[str, Any]
Resolver = Callable[[TargetQuery], Awaitable[Resolution | None]]


async def _did_you_mean(
    session: AsyncSession,
    repo_id: str,
    target: str,
    file_path: str | None = None,
    exclude_spec: Any = None,
) -> dict[str, list[str]]:
    """``{"did_you_mean": [...]}``: the nearest symbol ids to a missed name, or ``{}``.

    The missed name follows the file head when ``file_path`` is given or the
    head is a path (``src/a.py::sned``), else it is the whole target
    (``Clinet.send``, ``httpx.Client::send``); a plain path has none. A path
    has a separator, as in ``resolve_symbol_rows``' path-less rung.
    """
    head, sep, tail = target.partition("::")
    path_head = any(c in head for c in "/\\")
    if file_path or (sep and path_head):
        query = tail
    elif path_head:
        return {}
    else:
        query = target
    ids = await nearest_symbol_ids(session, repo_id, query, file_path, exclude_spec)
    return {"did_you_mean": [symbol_identity(sid) for sid in ids]} if ids else {}


def _size_exclusion_note(repo_root: Any, target: str) -> str | None:
    """Explain a missing file that ingestion dropped on size, else None.

    Computed live from the file on disk rather than read from the index,
    because a file excluded from ingestion leaves no row to read. Delegates the
    actual rule to ``size_verdict`` so this answer cannot drift from the code
    that produced the exclusion.
    """
    if repo_root is None:
        return None
    try:
        from repowise.core.ingestion.traverser import size_verdict

        root = Path(str(repo_root))
        abs_path = (root / target).resolve()
        # An index row is not a trust boundary; refuse anything outside the repo.
        abs_path.relative_to(root.resolve())
        size_bytes = abs_path.stat().st_size
        verdict = size_verdict(abs_path, size_bytes)
    except (OSError, ValueError):
        return None
    if verdict is None or not verdict.is_source:
        return None
    detail = {
        "minified": "it looks minified (machine-packed, not hand-written)",
        "unreadable": "it could not be read",
    }.get(verdict.reason, "it exceeds the maximum size for an indexed source file")
    return (
        f"'{target}' is not indexed: {detail} "
        f"({size_bytes // 1024:,} KB). It has no page and no symbols, and "
        "`repowise update` will not create them. Read the file directly."
    )


async def _excluded(q: TargetQuery) -> Resolution | None:
    """Reject excluded file / ``path::Name`` targets outright (bare symbol names
    aren't path-matchable here and fall through to neighbor filtering)."""
    gate_path = q.target.split("::", 1)[0] if "::" in q.target else q.target
    if is_excluded(gate_path, q.exclude_spec):
        return _excluded_error(q)
    return None


def _excluded_error(q: TargetQuery) -> Resolution:
    return {
        "target": q.target,
        "error": f"'{q.target}' is excluded by exclude_patterns configuration",
    }


async def _file_page_by_id(q: TargetQuery) -> Resolution | None:
    """A file page, the most common target."""
    page = await q.session.get(Page, f"file_page:{q.target}")
    if page and page.repository_id == q.repo_id:
        return ResolvedTarget("file", page=page, file_path=q.target)
    return None


async def _module_page_at(q: TargetQuery, target_path: str) -> Page | None:
    res = await q.session.execute(
        select(Page).where(
            Page.repository_id == q.repo_id,
            Page.page_type == "module_page",
            Page.target_path == target_path,
        )
    )
    return res.scalar_one_or_none()


async def _module_page_partial(q: TargetQuery) -> Page | None:
    """Partial module match, only on a path-segment boundary ("api" matches
    "src/api", not "apiclient"). Skipped when the target is a known file, so a
    module match cannot shadow the file rungs below."""
    clean_target = q.clean_target
    file_meta_res = await q.session.execute(
        select(GitMetadata.file_path).where(
            GitMetadata.repository_id == q.repo_id,
            GitMetadata.file_path == clean_target,
        )
    )
    if file_meta_res.scalar_one_or_none() is not None:
        return None
    esc = escape_like(clean_target)
    res = await q.session.execute(
        select(Page).where(
            Page.repository_id == q.repo_id,
            Page.page_type == "module_page",
            or_(
                Page.target_path == clean_target,
                Page.target_path.like(f"%/{esc}", escape=LIKE_ESCAPE),
                Page.target_path.like(f"{esc}/%", escape=LIKE_ESCAPE),
                Page.target_path.like(f"%/{esc}/%", escape=LIKE_ESCAPE),
            ),
        )
    )
    # Deterministic pick among several matches: shortest path, then
    # lexicographic.
    candidates = sorted(res.scalars().all(), key=lambda p: (len(p.target_path), p.target_path))
    return candidates[0] if candidates else None


async def _directory(q: TargetQuery) -> Resolution | None:
    """A directory with no module page, known from the indexed files under it."""
    prefix = _dir_prefix(q.target)
    if "::" in q.target or not prefix:
        return None
    if await _indexed_under(q.session, q.repo_id, q.target, q.exclude_spec):
        return ResolvedTarget("module", dir_path=prefix.rstrip("/"))
    return None


async def _module_page(q: TargetQuery) -> Resolution | None:
    """A module page: exact, then without a trailing slash; then the directory
    itself when files are indexed under it, so a partial match cannot hand a
    parent directory its child's card; then partial."""
    page = await _module_page_at(q, q.target)
    if page is None and q.clean_target != q.target:
        page = await _module_page_at(q, q.clean_target)
    if page is None:
        directory = await _directory(q)
        if directory is not None:
            return directory
        page = await _module_page_partial(q)
    return ResolvedTarget("module", page=page, dir_path=page.target_path) if page else None


async def _symbol_rows(q: TargetQuery) -> Any:
    """Symbol rows for the target. A "{path}::{Name}" id goes through
    get_symbol's lookup so ids resolve the same in both tools; the name rungs
    can never match a path-qualified id."""
    if "::" in q.target:
        return await resolve_symbol_rows(q.session, q.repo_id, q.target)
    res = await q.session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == q.repo_id,
            WikiSymbol.name == q.target,
        )
    )
    sym_matches = list(res.scalars().all())
    if not sym_matches:
        # "Client.send": a qualified name with no file.
        sym_matches = await resolve_symbol_rows(q.session, q.repo_id, q.target)
    if not sym_matches:
        res = await q.session.execute(
            select(WikiSymbol)
            .where(
                WikiSymbol.repository_id == q.repo_id,
                WikiSymbol.name.ilike(f"%{escape_like(q.target)}%", escape=LIKE_ESCAPE),
            )
            .limit(10)
        )
        sym_matches = list(res.scalars().all())
    return sym_matches


async def _symbol(q: TargetQuery) -> Resolution | None:
    sym_matches = await _symbol_rows(q)
    if not sym_matches:
        return None
    # A bare name isn't path-matchable in ``_excluded``, so drop the rows in
    # excluded files here; if none is left, answer as a ``path::Name`` would.
    sym_matches = filter_rows_by_attr(sym_matches, "file_path", q.exclude_spec)
    if not sym_matches:
        return _excluded_error(q)
    return ResolvedTarget(
        "symbol",
        file_path=sym_matches[0].file_path,
        sym_matches=sym_matches,
        symbol_node_id=sym_matches[0].symbol_id,
    )


async def _file_page_by_path(q: TargetQuery) -> Resolution | None:
    """A file page found by its target_path rather than its id."""
    res = await q.session.execute(
        select(Page).where(
            Page.repository_id == q.repo_id,
            Page.page_type == "file_page",
            Page.target_path == q.target,
        )
    )
    page = res.scalar_one_or_none()
    if page:
        return ResolvedTarget("file", page=page, file_path=q.target)
    return None


async def _graph_node(q: TargetQuery) -> Resolution | None:
    """Index-only mode (no wiki pages): the graph node, typed by what it is.

    A symbol node is a symbol target whose file is the node's file, not its
    id. Every separator form is tried, as the symbol rung does. Among several,
    the verbatim id wins, then a symbol node, then the id order, so the pick
    never depends on row order.
    """
    res = await q.session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == q.repo_id,
            GraphNode.node_id.in_(symbol_id_variants(q.target)),
        )
    )
    gnode = min(
        res.scalars().all(),
        key=lambda g: (g.node_id != q.target, g.node_type != "symbol", g.node_id),
        default=None,
    )
    if gnode is None:
        return None
    if gnode.node_type == "symbol":
        return ResolvedTarget(
            "symbol",
            graph_symbol=gnode,
            file_path=gnode.file_path,
            symbol_node_id=gnode.node_id,
        )
    # No wiki page; the card sections guard for this.
    return ResolvedTarget("file", file_path=q.target)


async def _git_metadata(q: TargetQuery) -> Resolution | None:
    """A file with no wiki page and no graph node, known from git_metadata."""
    res = await q.session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == q.repo_id,
            GitMetadata.file_path == q.target,
        )
    )
    meta = res.scalar_one_or_none()
    if not meta:
        return None
    # A file excluded on size will never get a page, so regenerating docs
    # cannot help; say so instead of the generic answer.
    size_note = _size_exclusion_note(q.repo_root, q.target)
    if size_note:
        return {"target": q.target, "error": size_note, "exists_in_git": True}
    return ResolvedTarget("file", file_path=q.target, live_file_meta=meta)


async def _live_file(q: TargetQuery) -> Resolution | None:
    """A live file never indexed into git_metadata is still a valid public
    path; resolve it from the checkout."""
    if _file_preview(q.repo_root, q.target) is not None:
        return ResolvedTarget("file", file_path=q.target, live_unindexed_file=True)
    return None


async def _legacy_community_id(q: TargetQuery) -> Resolution | None:
    """Legacy "community-12" module ids; point them at the directory-path
    vocabulary."""
    if not re.fullmatch(r"community[-_]\d+", q.clean_target, re.IGNORECASE):
        return None
    res = await q.session.execute(
        select(Page.target_path)
        .where(
            Page.repository_id == q.repo_id,
            Page.page_type == "module_page",
        )
        .order_by(Page.target_path)
        .limit(10)
    )
    module_paths = filter_path_list([row[0] for row in res.all()], q.exclude_spec)
    return {
        "target": q.target,
        "error": (
            f"Target not found: '{q.target}'. Module pages are no longer "
            "keyed by community ordinal — pass the module's directory "
            "path instead (see suggestions)."
        ),
        "suggestions": module_paths,
    }


async def _symbol_in_known_file(q: TargetQuery) -> Resolution | None:
    """An unresolved "{path}::{Name}" whose file is real gets the file's card
    (its symbol list holds the right id), marked with ``resolved_to`` so the
    degrade is legible."""
    target = q.target
    if "::" not in target:
        return None
    file_part = target.split("::", 1)[0]
    if not file_part or file_part == target or is_excluded(file_part, q.exclude_spec):
        return None
    # Imported here: targets imports this module.
    from repowise.server.mcp_server.tool_context.targets import _resolve_one_target

    # file_part contains no "::", so this recursion is depth-1. The caller is
    # hunting for a name: ``did_you_mean`` picks the closest from every symbol
    # of the file, so the list keeps its cap.
    card = await _resolve_one_target(
        q.session,
        q.repository,
        file_part,
        q.include,
        q.compact,
        exclude_spec=q.exclude_spec,
        repo_root=q.repo_root,
        collector=q.collector,
        as_of_ts=q.as_of_ts,
    )
    # Only a file card is the degrade; a symbol card for the head ("Command"
    # of "Command::Nope") would claim the wrong symbol.
    if "error" in card or card.get("type") != "file":
        return None
    card["target"] = target
    card["resolved_to"] = file_part
    nearest = await _did_you_mean(q.session, q.repo_id, target, file_part)
    card.update(nearest)
    card["note"] = (
        f"No symbol matched {target.split('::', 1)[1]!r} in this file; "
        f"showing the card for {file_part!r} instead. "
        + (
            "Closest names are in did_you_mean."
            if nearest
            else "Pass include=['symbols'] for the full symbol list."
        )
    )
    return card


async def _not_found(q: TargetQuery) -> dict[str, Any]:
    """Fuzzy path suggestions: files under a directory-like target, else by
    filename or partial path; plus the nearest symbol names."""
    target = q.target
    dir_prefix = q.clean_target.rstrip("/") + "/"
    res = await q.session.execute(
        select(GitMetadata.file_path)
        .where(
            GitMetadata.repository_id == q.repo_id,
            GitMetadata.file_path.like(f"{escape_like(dir_prefix)}%", escape=LIKE_ESCAPE),
        )
        .limit(5)
    )
    suggestions = [row[0] for row in res.all()]
    if not suggestions:
        tail = target.rsplit("/", 1)[-1]
        res = await q.session.execute(
            select(GitMetadata.file_path)
            .where(
                GitMetadata.repository_id == q.repo_id,
                GitMetadata.file_path.contains(tail),
            )
            .limit(5)
        )
        suggestions = [row[0] for row in res.all() if row[0] != target]
    suggestions = filter_path_list(suggestions, q.exclude_spec)
    miss: dict[str, Any] = {"target": target, "error": f"Target not found: '{target}'"}
    if suggestions:
        miss["suggestions"] = suggestions
    miss.update(await _did_you_mean(q.session, q.repo_id, target, exclude_spec=q.exclude_spec))
    return miss


#: The ladder, in precedence order. The first four are the indexed kinds; the
#: rest are fallbacks for targets with no page and no symbol row.
_RESOLVERS: tuple[Resolver, ...] = (
    _excluded,
    _file_page_by_id,
    _module_page,
    _symbol,
    _file_page_by_path,
    _graph_node,
    _git_metadata,
    _live_file,
    _legacy_community_id,
    _symbol_in_known_file,
)


async def resolve_target(q: TargetQuery) -> Resolution:
    """Run the ladder; the first resolver that answers wins."""
    for resolver in _RESOLVERS:
        found = await resolver(q)
        if found is not None:
            return found
    return await _not_found(q)
