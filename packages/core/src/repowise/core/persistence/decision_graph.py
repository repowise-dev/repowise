"""Decision-graph CRUD — edges + decision↔code links + lineage traversal.

Kept out of the monolithic ``crud.py`` (Phase 3 follows the Phase-2 pattern of
small focused modules). Depends only on the ORM models, so ``crud.py`` can
import it without a cycle and it can be re-exported from ``persistence``.

Two graphs live here:

- **decision → decision** (:class:`DecisionEdge`): typed, directed edges
  (``supersedes`` / ``refines`` / ``relates_to`` / ``conflicts_with``).
  :func:`build_lineage_chain` walks ``supersedes`` / ``refines`` back to roots
  (cycle-guarded) so ``get_why`` can render a chain (sessions → JWT → OAuth2)
  instead of a flat list.
- **decision → code** (:class:`DecisionNodeLink`): the governed file/module
  linkage, queryable both directions via :func:`get_governing_decisions`
  (file → decisions) and :func:`get_governed_nodes` (decision → code).
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DecisionEdge, DecisionNodeLink, DecisionRecord

__all__ = [
    "VALID_EDGE_KINDS",
    "build_lineage_chain",
    "get_decision_edges",
    "get_governed_nodes",
    "get_governing_decisions",
    "list_all_decision_edges",
    "list_conflict_edges",
    "list_decision_node_links",
    "sync_decision_node_links",
    "upsert_decision_edge",
]

VALID_EDGE_KINDS = frozenset({"supersedes", "refines", "relates_to", "conflicts_with"})

# Edge kinds that imply a lineage (a directed, time-ordered chain). ``src``
# supersedes/refines ``dst``, so following these from a node reaches its
# ancestors (the decisions it replaced).
_LINEAGE_KINDS = ("supersedes", "refines")


# ---------------------------------------------------------------------------
# Edge CRUD
# ---------------------------------------------------------------------------


async def upsert_decision_edge(
    session: AsyncSession,
    *,
    repository_id: str,
    src_decision_id: str,
    dst_decision_id: str,
    kind: str,
    confidence: float = 0.5,
    evidence: str = "",
) -> DecisionEdge | None:
    """Create or update one decision→decision edge, idempotent on (src,dst,kind).

    Self-edges and unknown kinds are rejected (returns ``None``) rather than
    raising — edge creation runs inside best-effort detection passes that must
    never abort an ingest. Re-detecting the same edge converges (confidence /
    evidence are refreshed) instead of duplicating.
    """
    if kind not in VALID_EDGE_KINDS:
        return None
    if src_decision_id == dst_decision_id:
        return None

    existing = (
        await session.execute(
            select(DecisionEdge).where(
                DecisionEdge.src_decision_id == src_decision_id,
                DecisionEdge.dst_decision_id == dst_decision_id,
                DecisionEdge.kind == kind,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        # Keep the strongest confidence seen; refresh evidence when supplied.
        existing.confidence = max(existing.confidence, confidence)
        if evidence:
            existing.evidence = evidence
        await session.flush()
        return existing

    edge = DecisionEdge(
        repository_id=repository_id,
        src_decision_id=src_decision_id,
        dst_decision_id=dst_decision_id,
        kind=kind,
        confidence=confidence,
        evidence=evidence,
    )
    session.add(edge)
    await session.flush()
    return edge


async def get_decision_edges(
    session: AsyncSession,
    decision_id: str,
    *,
    direction: str = "both",
    kinds: tuple[str, ...] | None = None,
) -> list[DecisionEdge]:
    """Return edges touching *decision_id*.

    ``direction``: ``"out"`` (decision is ``src``), ``"in"`` (decision is
    ``dst``), or ``"both"``. Optionally filter to *kinds*.
    """
    clauses = []
    if direction == "out":
        clauses.append(DecisionEdge.src_decision_id == decision_id)
    elif direction == "in":
        clauses.append(DecisionEdge.dst_decision_id == decision_id)
    else:
        clauses.append(
            (DecisionEdge.src_decision_id == decision_id)
            | (DecisionEdge.dst_decision_id == decision_id)
        )
    q = select(DecisionEdge).where(*clauses)
    if kinds:
        q = q.where(DecisionEdge.kind.in_(kinds))
    return list((await session.execute(q)).scalars().all())


async def list_conflict_edges(session: AsyncSession, repository_id: str) -> list[DecisionEdge]:
    """Return all ``conflicts_with`` edges in the repo (for health surfacing)."""
    return list(
        (
            await session.execute(
                select(DecisionEdge).where(
                    DecisionEdge.repository_id == repository_id,
                    DecisionEdge.kind == "conflicts_with",
                )
            )
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Decision → code links
# ---------------------------------------------------------------------------


async def sync_decision_node_links(
    session: AsyncSession,
    repository_id: str,
    decision_id: str,
    *,
    files: list[str] | None = None,
    modules: list[str] | None = None,
) -> None:
    """Replace a decision's node links to mirror its current file/module arrays.

    The JSON arrays remain the cheap read cache; these rows are the traversable
    truth. Called from ``bulk_upsert_decisions`` so the two never drift. A full
    replace (delete + insert) is simplest and correct at realistic link counts.
    """
    await session.execute(
        delete(DecisionNodeLink).where(DecisionNodeLink.decision_id == decision_id)
    )
    seen: set[tuple[str, str]] = set()
    for node_id in files or []:
        key = (node_id, "file")
        if not node_id or key in seen:
            continue
        seen.add(key)
        session.add(
            DecisionNodeLink(
                repository_id=repository_id,
                decision_id=decision_id,
                node_id=node_id,
                link_type="file",
            )
        )
    for node_id in modules or []:
        key = (node_id, "module")
        if not node_id or key in seen:
            continue
        seen.add(key)
        session.add(
            DecisionNodeLink(
                repository_id=repository_id,
                decision_id=decision_id,
                node_id=node_id,
                link_type="module",
            )
        )
    await session.flush()


async def get_governing_decisions(
    session: AsyncSession,
    repository_id: str,
    node_id: str,
) -> list[DecisionRecord]:
    """File/module → the decisions that govern it (reverse link traversal)."""
    rows = (
        await session.execute(
            select(DecisionRecord)
            .join(DecisionNodeLink, DecisionNodeLink.decision_id == DecisionRecord.id)
            .where(
                DecisionNodeLink.repository_id == repository_id,
                DecisionNodeLink.node_id == node_id,
            )
        )
    ).scalars()
    return list(rows)


async def get_governed_nodes(
    session: AsyncSession,
    decision_id: str,
) -> list[DecisionNodeLink]:
    """Decision → the files/modules it governs (forward link traversal)."""
    return list(
        (
            await session.execute(
                select(DecisionNodeLink).where(DecisionNodeLink.decision_id == decision_id)
            )
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Lineage traversal
# ---------------------------------------------------------------------------


async def build_lineage_chain(
    session: AsyncSession,
    decision_id: str,
    *,
    max_depth: int = 50,
) -> list[dict]:
    """Walk ``supersedes``/``refines`` edges from *decision_id* back to roots.

    Returns an ordered chain **root → … → current** of light decision dicts
    (id, title, status, source, kind-that-reached-it). ``src`` supersedes/refines
    ``dst``, so we follow outgoing lineage edges to reach ancestors. Cycle- and
    depth-guarded so a malformed graph can never spin. An isolated decision
    returns a single-element chain (itself), which the caller can treat as
    "no lineage".
    """
    # Collect ancestors by following src→dst lineage edges.
    visited: set[str] = set()
    # ordered list of (decision_id, kind_into_ancestor) from current downward.
    order: list[tuple[str, str | None]] = []
    current = decision_id
    kind_in: str | None = None
    depth = 0
    while current and current not in visited and depth < max_depth:
        visited.add(current)
        order.append((current, kind_in))
        edges = await get_decision_edges(session, current, direction="out", kinds=_LINEAGE_KINDS)
        # Prefer the highest-confidence supersedes, then refines, for a stable
        # single-parent walk (the common shape; branching is rare).
        edges.sort(key=lambda e: (e.kind == "supersedes", e.confidence), reverse=True)
        next_edge = next((e for e in edges if e.dst_decision_id not in visited), None)
        if next_edge is None:
            break
        current = next_edge.dst_decision_id
        kind_in = next_edge.kind
        depth += 1

    # ``order`` has the decision itself plus any ancestors; a length of 1 means
    # no lineage, so we still surface the decision on its own.
    ids = [decision_id] if len(order) <= 1 else [d for d, _ in order]

    # Load the records in one query, preserve root→current order.
    recs = {
        r.id: r
        for r in (await session.execute(select(DecisionRecord).where(DecisionRecord.id.in_(ids))))
        .scalars()
        .all()
    }
    chain: list[dict] = []
    # order is current→root; reverse for root→current.
    for did, kind in reversed(order or [(decision_id, None)]):
        rec = recs.get(did)
        if rec is None:
            continue
        chain.append(
            {
                "id": rec.id,
                "title": rec.title,
                "status": rec.status,
                "source": rec.source,
                # how the *newer* decision related to this one (None for the leaf)
                "relation": kind,
            }
        )
    return chain


# ---------------------------------------------------------------------------
# Repo-wide list queries (used by the graph REST endpoint)
# ---------------------------------------------------------------------------


async def list_all_decision_edges(
    session: AsyncSession,
    repository_id: str,
) -> list[DecisionEdge]:
    """Return all decision→decision edges for a repository.

    Used by the graph REST endpoint to render the full decision graph. Bounded
    by the natural size of a repo's decision graph (no pagination needed at
    realistic scales; the endpoint caps decisions at 200).
    """
    return list(
        (
            await session.execute(
                select(DecisionEdge).where(DecisionEdge.repository_id == repository_id)
            )
        )
        .scalars()
        .all()
    )


async def list_decision_node_links(
    session: AsyncSession,
    repository_id: str,
) -> list[DecisionNodeLink]:
    """Return all decision→code links for a repository.

    Used by the graph REST endpoint to render decision↔file/module edges. Each
    row maps one decision to one file or module path.
    """
    return list(
        (
            await session.execute(
                select(DecisionNodeLink).where(DecisionNodeLink.repository_id == repository_id)
            )
        )
        .scalars()
        .all()
    )
