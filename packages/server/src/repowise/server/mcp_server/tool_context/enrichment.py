"""Optional enrichment blocks for get_context.

Each helper resolves one ``include=`` block and attaches it to ``result_data``
in place: call graph in ``call_graph``, metrics / community / health in
``signals``, skeleton in ``skeleton``, document references here. This module
re-exports the others so callers keep one import path.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.doc_drift.constants import (
    REFERENCE_BASIS,
    UNAVAILABLE_NO_TABLE,
    UNAVAILABLE_NOT_COMPUTED,
    UNAVAILABLE_READ_FAILED,
)
from repowise.core.analysis.doc_drift.serialize import (
    collapse_reference_sites,
    documents_with_drift,
)
from repowise.core.persistence.crud import (
    doc_drift_references_stored,
    get_doc_drift_findings,
    get_doc_drift_references,
    serialize_doc_drift_reference_row,
)
from repowise.core.persistence.models import Repository
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._helpers import filter_rows_by_attr, is_missing_table
from repowise.server.mcp_server.tool_context.call_graph import (  # noqa: F401
    _CALL_EDGE_TYPES,
    _MIN_CALL_CONFIDENCE,
    _RELATION_ROW_CAP,
    _SYMBOL_NEIGHBOR_LIMIT,
    _SYMBOL_USE_EDGE_TYPES,
    _count_neighbors_by_edge_type,
    _resolve_call_graph,
    _resolve_file_level_callers,
    _unique_by_symbol,
)
from repowise.server.mcp_server.tool_context.signals import (  # noqa: F401
    _FILE_DEPENDENCY_EDGE_TYPES,
    _resolve_community,
    _resolve_health,
    _resolve_metrics,
)
from repowise.server.mcp_server.tool_context.skeleton import (  # noqa: F401
    _FOCUS_CONTAINER_MAX_CHARS,
    _resolve_skeleton,
)

#: Where a resolved target path waits between its card being built and the
#: batched doc-drift read. Popped by that pass, so it never reaches a response.
_DOC_DRIFT_PATH = "_doc_drift_path"

#: Reference rows per target before the cap takes over. The same figure as
#: ``targets._MAX_USED_BY``, for the same "who points at this" question.
_MAX_DOC_REFERENCES = 20


async def attach_doc_references(
    session: AsyncSession,
    repository: Repository,
    cards: dict[str, dict[str, Any]],
    *,
    exclude_spec: Any = None,
    collector: OmissionCollector | None = None,
) -> None:
    """Attach, to every card, the documents that name its file.

    The drift pass files a finding against the document, so these stored rows
    are the only thing that can answer a question asked about a code file.
    Served rather than recomputed: see :class:`DocDriftReference`.

    Runs once for the whole call, after the targets resolve. Savepoints opened
    per target nest on the session they share, and the first to exit closes
    the others.

    Two claims, kept apart: ``references`` says a document names this file,
    ``documents_with_drift`` says a listed document has some assertion that no
    longer holds -- anywhere in it, not necessarily about this file.
    """
    wanted = {
        name: card.pop(_DOC_DRIFT_PATH, None)
        for name, card in cards.items()
        if _DOC_DRIFT_PATH in card
    }
    if not wanted:
        return

    paths = {path for path in wanted.values() if path}
    rows: list[Any] = []
    finding_rows: list[Any] = []
    stored = True
    if paths:
        try:
            # Not decoration: this read raises on an index older than the
            # table, and on Postgres a failed statement poisons the whole
            # transaction.
            async with session.begin_nested():
                rows = await get_doc_drift_references(
                    session, repository.id, target_paths=sorted(paths)
                )
                # An empty answer is the strong claim "no document
                # mentions this file", and an empty store cannot support it.
                # Asked only when the answer would otherwise be empty.
                stored = bool(rows) or await doc_drift_references_stored(
                    session, repository.id
                )
                # Read whole: the findings table is the defect list and is
                # bounded by design. Ceiling for a repo with thousands of
                # them: a plural filter on ``get_doc_drift_findings``.
                if rows:
                    finding_rows = await get_doc_drift_findings(session, repository.id)
        except (SQLAlchemyError, OSError, LookupError) as exc:
            # Refuse rather than serve an empty list, which reads as a
            # clean bill that was never taken -- and name which failure, since
            # "your index is old" is wrong advice for a transient one.
            reason = (
                UNAVAILABLE_NO_TABLE
                if is_missing_table(exc)
                else UNAVAILABLE_READ_FAILED
            )
            for name in wanted:
                cards[name]["doc_drift"] = {"unavailable": reason}
            return

    kept = filter_rows_by_attr(rows, "document_path", exclude_spec)
    excluded_by_target = Counter(r.target_path for r in rows) - Counter(
        r.target_path for r in kept
    )
    by_target: dict[str, list[Any]] = {}
    for row in kept:
        by_target.setdefault(row.target_path, []).append(row)

    drift_by_document = Counter(f.file_path for f in finding_rows)

    for name, path in wanted.items():
        if not path:
            # A module target resolves to a directory, and a reference does
            # not resolve to one.
            cards[name]["doc_drift"] = None
        elif not stored:
            cards[name]["doc_drift"] = {"unavailable": UNAVAILABLE_NOT_COMPUTED}
        else:
            cards[name]["doc_drift"] = _doc_reference_block(
                by_target.get(path, []),
                drift_by_document,
                excluded=excluded_by_target.get(path, 0),
                target=path,
                collector=collector,
            )


def _doc_reference_block(
    rows: list[Any],
    drift_by_document: Counter[str],
    *,
    excluded: int,
    target: str,
    collector: OmissionCollector | None,
) -> dict[str, Any]:
    """One target's reverse-view block, built from its own rows."""
    block: dict[str, Any] = {}
    emitted = collapse_reference_sites(
        [serialize_doc_drift_reference_row(row) for row in rows]
    )
    cap_collection(
        block,
        "references",
        emitted,
        _MAX_DOC_REFERENCES,
        collector,
        label=f"documents naming {target}",
    )
    block["documents"] = len({r.document_path for r in rows})
    if excluded:
        # Otherwise a tree whose naming documents are all excluded reads as a
        # file nothing mentions.
        block["references_excluded"] = excluded

    # Only the documents this answer matched; the repo-wide total is what
    # ``get_health(include=["doc_drift"])`` is for. Read off the uncapped list,
    # or a document past the display cap reports as clean.
    drifted = documents_with_drift(emitted, drift_by_document)
    if drifted:
        block["documents_with_drift"] = drifted

    # Emitted on an empty answer too: that is the one most likely to be
    # read as proof that nothing documents this file.
    block["references_basis"] = REFERENCE_BASIS
    return block
