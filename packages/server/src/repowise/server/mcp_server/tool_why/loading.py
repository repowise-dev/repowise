"""Database reads the modes share: corpus, git metadata, evidence rows."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select

from repowise.core.persistence.crud.authority import accepted_decision_ids
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DecisionEvidence, GitMetadata, GraphNode, WikiSymbol
from repowise.core.test_paths import is_test_related_path
from repowise.server.mcp_server._helpers import (
    LIKE_ESCAPE,
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    decision_is_excluded,
    escape_like,
    is_excluded,
)
from repowise.server.mcp_server._query_shape import _WORD_CHAIN_RE, _identifier_shaped, path_tokens
from repowise.server.mcp_server.tool_why.caps import _DECISION_CORPUS_LIMIT


async def _git_metadata_for(session: Any, repository_id: Any, file_path: str) -> Any | None:
    """One file's git metadata row, or ``None`` when git never indexed it."""
    git_res = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.file_path == file_path,
        )
    )
    return git_res.scalar_one_or_none()


async def _named_file(query: str, repo: str | None) -> str | None:
    """The one indexed file a question names, by path or file name, or ``None``.

    An exact path wins; the first token naming one file wins. A name several
    files end with anchors nothing (ceiling: the question's words could pick one).
    """
    tokens = [t.replace("\\", "/").removeprefix("./") for t in path_tokens(query)]
    if not tokens:
        return None
    ctx = await _resolve_repo_context(repo)
    spec = _get_exclude_spec(ctx.path)
    node = func.lower(GraphNode.node_id)
    lows = [t.lower() for t in tokens]
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        res = await session.execute(
            select(GraphNode.node_id).where(
                GraphNode.repository_id == repository.id,
                GraphNode.node_type == "file",
                or_(
                    *(
                        or_(node == low, node.like(f"%/{escape_like(low)}", escape=LIKE_ESCAPE))
                        for low in lows
                    )
                ),
            )
        )
        paths = {p for (p,) in res.all() if not is_excluded(p, spec)}
    for token, low in zip(tokens, lows, strict=True):
        if token in paths:
            return token
        found = [p for p in paths if (q := p.lower()) == low or q.endswith(f"/{low}")]
        if len(found) == 1:
            return found[0]
    return None


#: Defining files a refusal names. More than this and the names are too common to anchor on.
_MAX_DEFINING_FILES = 3


def _code_names(query: str) -> list[str]:
    """Identifier-shaped words of *query*, dotted paths split into their parts."""
    names = {
        c
        for tok in _WORD_CHAIN_RE.findall(query)
        for c in (tok, *tok.split("."))
        if _identifier_shaped(c)
    }
    return sorted(names)


async def _defining_files(ctx: Any, repository_id: Any, query: str) -> list[dict[str, Any]]:
    """``{name, path, line}`` for each code name in *query* the index defines once.

    For a question that named no file and found nothing: where the code it
    names lives, so the refusal can say which file to ask about. A name defined
    in several files anchors nothing; production files only.
    """
    names = _code_names(query)
    if not names:
        return []
    spec = _get_exclude_spec(ctx.path)
    async with get_session(ctx.session_factory) as session:
        res = await session.execute(
            select(WikiSymbol.name, WikiSymbol.file_path, WikiSymbol.start_line).where(
                WikiSymbol.repository_id == repository_id,
                WikiSymbol.name.in_(names),
            )
        )
        rows = [
            r for r in res.all() if not is_excluded(r[1], spec) and not is_test_related_path(r[1])
        ]
    out = _single_definitions(rows)
    return out if len({o["path"] for o in out}) <= _MAX_DEFINING_FILES else []


def _single_definitions(rows: list) -> list[dict[str, Any]]:
    """The first definition of each name that only one file defines."""
    paths_of: dict[str, set[str]] = {}
    for name, path, _ in rows:
        paths_of.setdefault(name, set()).add(path)
    out: dict[str, dict[str, Any]] = {}
    for name, path, line in sorted(rows, key=lambda r: (r[0], r[2] or 0)):
        if len(paths_of[name]) == 1:
            out.setdefault(name, {"name": name, "path": path, "line": line})
    return list(out.values())


async def _all_git_metadata(session: Any, repository_id: Any) -> list:
    """Every file's git metadata, for the archaeology cross-file search."""
    all_git_res = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repository_id,
        )
    )
    return all_git_res.scalars().all()


async def _load_target_git(
    session: Any, repository_id: Any, targets: list[str] | None
) -> dict[str, Any]:
    """Load per-target git metadata keyed by file path (only present ones)."""
    target_git: dict[str, Any] = {}
    if not targets:
        return target_git
    for t in targets:
        meta = await _git_metadata_for(session, repository_id, t)
        if meta:
            target_git[t] = meta
    return target_git


async def _decision_corpus(session: Any, repository_id: str, exclude_spec: Any) -> list:
    """The rankable decision records of one repository.

    Records anchored entirely in excluded paths, and dismissed ones, are
    filtered out. Shared with workspace search so every mode ranks the same
    corpus. Takes a session so a caller can share one.
    """
    from repowise.core.persistence.crud import list_decisions as _list_decisions

    records = await _list_decisions(
        session, repository_id, include_proposed=True, limit=_DECISION_CORPUS_LIMIT
    )
    return [d for d in records if not decision_is_excluded(d, exclude_spec)]


async def _attach_decision_evidence(session: Any, records: list) -> None:
    """Attach all persisted provenance rows with one bounded query.

    The child rows are the canonical evidence behind each ``DecisionRecord``
    and must travel with it when trust metadata is projected.
    """
    if not records:
        return
    ids = [record.id for record in records]
    rows = list(
        (
            await session.execute(
                select(DecisionEvidence)
                .where(DecisionEvidence.decision_id.in_(ids))
                .order_by(
                    DecisionEvidence.source_rank.desc(),
                    DecisionEvidence.created_at.asc(),
                    DecisionEvidence.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    by_decision: dict[str, list[DecisionEvidence]] = {}
    for row in rows:
        by_decision.setdefault(row.decision_id, []).append(row)
    for record in records:
        record._why_evidence_rows = by_decision.get(record.id, [])


async def _attach_response_decision_evidence(
    session: Any, result: dict[str, Any], records: list
) -> None:
    """Hydrate the decision rows *result* currently carries.

    Search mode calls it before its cap on purpose: rows the cap sheds go to
    the omission store and must carry their evidence by then.
    """
    ids: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            candidate = value.get("id") or value.get("decision_id")
            if isinstance(candidate, str):
                ids.add(candidate)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    await _attach_decision_evidence(
        session, [record for record in records if record.id in ids]
    )


async def _hydrate_response_decision_evidence(
    ctx: Any, result: dict[str, Any], records: list
) -> None:
    async with get_session(ctx.session_factory) as session:
        await _attach_response_decision_evidence(session, result, records)


async def _load_corpus(repo: str | None, targets: list[str] | None) -> tuple:
    """Repo context, corpus, git metadata for targets, and the acceptance set.

    The prologue both target-aware modes open with, so both answer from the
    same filtered corpus.
    """
    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        all_decisions = await _decision_corpus(session, repository.id, _get_exclude_spec(ctx.path))
        # ``governing_only``: what still binds, not what was ever accepted.
        accepted = await accepted_decision_ids(
            session, repository.id, governing_only=True
        )
        # Load git metadata for targets (for origin context in results)
        target_git = await _load_target_git(session, repository.id, targets)
    return ctx, repository, all_decisions, target_git, accepted
