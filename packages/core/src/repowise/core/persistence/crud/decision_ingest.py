"""Batch ingest of extracted decisions: grouping, dedupe and provenance.

:func:`bulk_upsert_decisions` is the path every extraction lane writes
through. It merges contributions that describe one decision into one
record and keeps every contributor as evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.provenance import rank_for_source

from ..decision_graph import sync_links_from_record
from ..models import DecisionRecord, _now_utc
from .decision_evidence import (
    _evidence_quote,
    _first_commit,
    _rederive_headline,
    _upsert_decision_evidence,
    list_decision_evidence,
)
from .decision_identity import (
    _extraction_kind,
    _extraction_status,
    _merge_status,
    derive_decision_id,
)
from .decision_review_meta import _write_candidate_meta


def _normalize_title(title: str) -> str:
    """Normalize a decision title for cross-source dedup comparison."""
    import re as _re

    t = title.lower().strip()
    t = _re.sub(r"[^a-z0-9\s]", "", t)
    t = _re.sub(r"\s+", " ", t)
    return t

#: Sources whose evidence file is itself a reviewable, version-controlled
#: statement of the decision. Only these can accept without a person present.
_TRACKED_ARTIFACT_SOURCES: frozenset[str] = frozenset({"adr"})


async def _accept_from_tracked_artifact(
    session: AsyncSession, rec: DecisionRecord, headline: dict
) -> None:
    """Grant acceptance to an ADR the repository has committed as accepted.

    Silent on anything that is not one: a missing rationale or scope means the
    document did not say enough to bind future work, and a refusal there is
    correct rather than an error to report. Re-running is a no-op because the
    acceptance already exists.
    """
    if headline.get("status") != "active":
        return
    if headline.get("source") not in _TRACKED_ARTIFACT_SOURCES:
        return
    artifact = headline.get("evidence_file") or ""
    if not artifact:
        return

    from repowise.core.analysis.decisions.accepter import is_tracked

    from ..models import Repository
    from .authority import AcceptanceRefusedError, is_accepted, record_acceptance

    if await is_accepted(session, rec.id):
        return
    repo = await session.get(Repository, rec.repository_id)
    if repo is None or not is_tracked(repo.local_path, artifact):
        # An uncommitted document is one machine's file. Letting it accept would
        # replace the human acceptance event with a file an agent can write.
        return
    try:
        await record_acceptance(
            session,
            rec,
            action="accepted",
            currency="active",
            artifact=artifact,
            kind="import",
            note="accepted by a tracked decision record",
        )
    except AcceptanceRefusedError as exc:
        structlog.get_logger(__name__).debug(
            "adr_acceptance_refused", decision_id=rec.id, reason=str(exc)
        )


async def bulk_upsert_decisions(
    session: AsyncSession,
    repository_id: str,
    decisions: list[dict],
    *,
    vector_store: Any | None = None,
) -> list[str]:
    """Upsert decisions, accreting provenance instead of discarding losers.

    Decisions with near-identical normalized titles are merged into a single
    :class:`DecisionRecord`; every contributing source becomes a
    :class:`DecisionEvidence` row. The record's headline fields come from the
    highest-``source_rank`` contributor, and its confidence is recomputed from
    the best rank + the number of independently corroborating sources + the
    strongest surviving verification verdict.

    Idempotent: a full re-index converges (evidence rows upsert on their
    natural key); an incremental update adds new evidence and re-derives the
    headline + confidence from the union.

    When *vector_store* (the shared page-generator store) is supplied, a
    Phase-2C semantic pass augments the cheap normalized-title match: an
    incoming group that matched no existing record by title is looked up in the
    store, and if its nearest ``decision:`` neighbour clears the cosine
    threshold it is folded into that record as additional evidence — so
    paraphrases ("Use Redis" vs "Adopt Redis cache") collapse into one record.
    Every touched record is (re-)embedded into the store, so decisions are
    matchable next run *and* discoverable via ``search_codebase``.

    Returns the ids of every record touched (created or updated) this call, so
    a caller can run the Phase-3 supersession/conflict detection over just the
    records that changed.
    """
    groups = _group_by_normalized_title(decisions)
    if not groups:
        return []

    existing_by_norm = await _existing_records_by_title(session, repository_id)
    # A store hit (which returns a decision id) resolves back to the live
    # record through this map, and it is grown as records are created so
    # paraphrases *within* one batch also collapse.
    id_to_rec: dict[str, DecisionRecord] = {rec.id: rec for rec in existing_by_norm.values()}

    # Headline candidate per group: highest source rank, tie-break by
    # confidence. Hoisted so the batched embedding can see every group's
    # match text before the loop runs.
    headline_by_norm: dict[str, dict] = {
        norm: max(
            members,
            key=lambda d: (rank_for_source(d.get("source", "")), d.get("confidence", 0.0)),
        )
        for norm, members in groups.items()
    }
    dedup = await _SemanticDedup.prepare(vector_store, headline_by_norm, group_count=len(groups))

    touched_ids: list[str] = []
    captured: dict[str, tuple[str, bool]] = {}

    for norm, members in groups.items():
        headline = headline_by_norm[norm]
        rec = existing_by_norm.get(norm)

        # No title match → ask the store whether a semantically-equivalent
        # decision already exists, and fold into it if so (cheap title dedup
        # stays the first pass; this only runs on the residual).
        # A computed record must not fold into a prose record that merely
        # sounds alike; title dedup still applies to it.
        if rec is None and headline.get("source") != "conventions":
            match_id = await dedup.find_match(norm, headline)
            if match_id is not None and match_id in id_to_rec:
                rec = id_to_rec[match_id]
                existing_by_norm[norm] = rec

        # A dismissed record is a tombstone: leave it untouched (no headline
        # promotion, no new evidence, not re-embedded, not in touched_ids) so
        # dismissals survive every reindex.
        if rec is not None and rec.status == "dismissed":
            continue

        created = False
        if rec is None:
            identity = _headline_identity(repository_id, headline, members)
            # Identity is evidence, so two titles for one decision derive one
            # id. Folding into the record that id already names is the point of
            # the key: inserting instead would collide on the primary key, and
            # skipping would leave the duplicate this is meant to collapse.
            folded = id_to_rec.get(identity.id) or await session.get(DecisionRecord, identity.id)
            if folded is not None and folded.repository_id == repository_id:
                if folded.status == "dismissed":
                    continue
                rec = folded
            else:
                rec = _new_record(repository_id, identity, headline)
                session.add(rec)
                await session.flush()
                created = True
            existing_by_norm[norm] = rec
            id_to_rec[rec.id] = rec
        if not created:
            _maybe_promote_headline(rec, headline)

        await _accrete_evidence(session, rec.id, members)

        # A committed ADR that says "accepted" is the one non-human acceptance
        # the contract allows, because the document is version controlled and
        # reviewable. Recorded here, where the parsed heading is still in hand.
        await _accept_from_tracked_artifact(session, rec, headline)

        # Re-derive headline confidence + verification from the FULL evidence
        # set (existing + just-added), so corroboration accrues across runs.
        _rederive_headline(rec, await list_decision_evidence(session, rec.id))
        rec.updated_at = _now_utc()
        touched_ids.append(rec.id)
        _record_capture(captured, rec.id, headline, members)

        await sync_links_from_record(session, rec)
        await dedup.index_record(rec, norm)

    await dedup.flush(touched_ids, id_to_rec)
    await _write_candidate_meta(session, repository_id, captured)

    await session.flush()
    return touched_ids


def _group_by_normalized_title(decisions: list[dict]) -> dict[str, list[dict]]:
    """Incoming decisions keyed by normalized title; untitled ones are dropped."""
    groups: dict[str, list[dict]] = {}
    for d in decisions:
        norm = _normalize_title(d.get("title", ""))
        if not norm:
            continue
        groups.setdefault(norm, []).append(d)
    return groups


async def _existing_records_by_title(
    session: AsyncSession, repository_id: str
) -> dict[str, DecisionRecord]:
    """This repository's records by normalized title, so cross-run merges land on one row.

    On a title collision the most authoritative existing row is canonical.
    """
    existing_rows = await session.execute(
        select(DecisionRecord).where(DecisionRecord.repository_id == repository_id)
    )
    existing_by_norm: dict[str, DecisionRecord] = {}
    for rec in existing_rows.scalars().all():
        norm = _normalize_title(rec.title)
        prior = existing_by_norm.get(norm)
        if prior is None or rank_for_source(rec.source) > rank_for_source(prior.source):
            existing_by_norm[norm] = rec
    return existing_by_norm


@dataclass(frozen=True)
class _HeadlineIdentity:
    """The derived id of a group that matched no record, and the quote it pins."""

    id: str
    quote: str


def _headline_identity(
    repository_id: str, headline: dict, members: list[dict]
) -> _HeadlineIdentity:
    # Pinned now and never revised: the identity is keyed on it, and a later
    # extraction rewording the same sentence must not move the id.
    quote = _evidence_quote(headline)
    derived = derive_decision_id(
        repository_id,
        headline.get("title", ""),
        source=headline.get("source", "cli"),
        evidence_file=headline.get("evidence_file"),
        affected_files=headline.get("affected_files") or [],
        evidence_line=headline.get("evidence_line"),
        identity_quote=quote,
        needs_split=any(bool(d.get("needs_split")) for d in members),
    )
    return _HeadlineIdentity(id=derived, quote=quote)


def _new_record(
    repository_id: str, identity: _HeadlineIdentity, headline: dict
) -> DecisionRecord:
    """A new record whose every field comes from the group's headline."""
    # A source that measures its own conformance supplies the score;
    # otherwise the git-diff recompute fills it in later.
    headline_staleness = headline.get("staleness_score")
    return DecisionRecord(
        id=identity.id,
        identity_quote=identity.quote,
        repository_id=repository_id,
        title=headline.get("title", ""),
        status=_extraction_status(headline.get("status", "proposed")),
        kind=_extraction_kind(headline.get("kind")),
        context=headline.get("context") or "",
        decision=headline.get("decision") or "",
        rationale=headline.get("rationale") or "",
        alternatives_json=json.dumps(headline.get("alternatives") or []),
        consequences_json=json.dumps(headline.get("consequences") or []),
        affected_files_json=json.dumps(headline.get("affected_files") or []),
        # Rides with the file list it describes.
        scope_basis=headline.get("scope_basis") or "",
        affected_modules_json=json.dumps(headline.get("affected_modules") or []),
        tags_json=json.dumps(headline.get("tags") or []),
        evidence_commits_json=json.dumps(headline.get("evidence_commits") or []),
        source=headline.get("source", "cli"),
        evidence_file=headline.get("evidence_file"),
        evidence_line=headline.get("evidence_line"),
        confidence=headline.get("confidence", 0.5),
        staleness_score=(float(headline_staleness) if headline_staleness is not None else 0.0),
    )


def _maybe_promote_headline(rec: DecisionRecord, headline: dict) -> None:
    """Promote *headline*'s fields onto *rec* if its source is at least as authoritative.

    Provenance still accretes either way; this only decides whose wording the
    record shows.
    """
    if rank_for_source(headline.get("source", "")) < rank_for_source(rec.source):
        return
    rec.title = headline.get("title", rec.title)
    rec.status = _merge_status(rec.status, headline.get("status", rec.status))
    # ``kind`` is deliberately absent: the noun is decided when the
    # record is created and re-extraction does not revisit it. Most
    # sources default the field rather than deciding it, so an incoming
    # value is usually the absence of a judgement; and this branch runs
    # on accepted records too, where flipping the noun would change what
    # a record governs behind the person who accepted it. The migration
    # refuses that for the same reason, and the manifest is where a
    # person changes it deliberately.
    rec.context = headline.get("context") or rec.context
    rec.decision = headline.get("decision") or rec.decision
    rec.rationale = headline.get("rationale") or rec.rationale
    rec.alternatives_json = json.dumps(headline.get("alternatives") or [])
    rec.consequences_json = json.dumps(headline.get("consequences") or [])
    rec.affected_files_json = json.dumps(headline.get("affected_files") or [])
    rec.scope_basis = headline.get("scope_basis") or ""
    rec.affected_modules_json = json.dumps(headline.get("affected_modules") or [])
    rec.tags_json = json.dumps(headline.get("tags") or [])
    rec.evidence_commits_json = json.dumps(headline.get("evidence_commits") or [])
    rec.source = headline.get("source", rec.source)
    rec.evidence_file = headline.get("evidence_file")
    rec.evidence_line = headline.get("evidence_line")
    # Only a source that measures its own conformance supplies this.
    if headline.get("staleness_score") is not None:
        rec.staleness_score = float(headline["staleness_score"])


async def _accrete_evidence(session: AsyncSession, decision_id: str, members: list[dict]) -> None:
    """Upsert one evidence row per contributing source occurrence."""
    for d in members:
        src = d.get("source", "cli")
        await _upsert_decision_evidence(
            session,
            decision_id,
            source=src,
            source_rank=rank_for_source(src),
            evidence_file=d.get("evidence_file"),
            evidence_line=d.get("evidence_line"),
            evidence_commit=_first_commit(d),
            source_quote=_evidence_quote(d),
            confidence=d.get("confidence", 0.5),
            verification=d.get("verification", "unverified"),
        )


def _record_capture(
    captured: dict[str, tuple[str, bool]],
    decision_id: str,
    headline: dict,
    members: list[dict],
) -> None:
    """Accumulate the lane and split flag this run's candidate-meta row carries.

    Two title groups can fold onto one record, so this accumulates: the later
    group must not drop what the earlier one raised.

    The split flag is read from every member, not from the headline alone. The
    headline is the highest-ranked source in the group, and the lane that
    notices a claim bundles two decisions is usually not the highest-ranked
    one: a session-mined candidate that flags itself loses its flag the moment
    a CLI-authored record shares its title. A flag raised by any contributor is
    a flag on the record.
    """
    prior_lane, prior_split = captured.get(decision_id, ("", False))
    captured[decision_id] = (
        prior_lane or headline.get("lane") or "",
        prior_split or any(bool(d.get("needs_split")) for d in members),
    )


class _SemanticDedup:
    """Phase-2C semantic dedup against the shared vector store.

    Augments the cheap normalized-title match: an incoming group that matched
    no record by title is looked up in the store and folded into its nearest
    ``decision:`` neighbour when that clears the cosine threshold, and every
    touched record is (re-)embedded so it is matchable next run and
    discoverable via ``search_codebase``. Inert when there is no store.

    Batch mode: the per-item path embeds one query per residual group and one
    upsert per touched record, thousands of serial network round-trips on a
    first index. Instead every group's match text is embedded in a few chunked
    requests up front, the store is searched by raw vector, dedup *within* the
    batch runs against a local pending index, and the store writes are
    deferred to one batched upsert. Stores that can't hand back vectors (no
    embedder, or ``search_by_vector`` left at the base-class default) keep the
    per-item flow.
    """

    def __init__(
        self,
        vector_store: Any | None,
        *,
        group_vecs: dict[str, list[float]] | None = None,
        group_texts: dict[str, str] | None = None,
    ) -> None:
        self._store = vector_store
        self._group_vecs = group_vecs or {}
        self._group_texts = group_texts or {}
        self._batch_mode = group_vecs is not None
        # Vectors of records touched this batch but not yet written to the
        # store: the in-batch side of dedup in batch mode. A record is only
        # registered when its final text equals the group text its vector came
        # from, so the index never matches against text the record doesn't
        # actually contain.
        self._pending: Any | None = None
        if self._batch_mode:
            from repowise.core.analysis.decision_semantic_match import PendingDecisionIndex

            self._pending = PendingDecisionIndex()

    @classmethod
    async def prepare(
        cls,
        vector_store: Any | None,
        headline_by_norm: dict[str, dict],
        *,
        group_count: int,
    ) -> _SemanticDedup:
        if vector_store is None:
            return cls(None)
        from repowise.core.analysis.decision_semantic_match import decision_match_text

        ordered = [
            (norm, decision_match_text(h.get("title", ""), h.get("decision") or ""))
            for norm, h in headline_by_norm.items()
        ]
        ordered = [(norm, text) for norm, text in ordered if text]
        vecs = None
        if ordered and _supports_vector_search(vector_store):
            try:
                vecs = await vector_store.embed_texts([text for _, text in ordered])
            except Exception:
                vecs = None
        batched = vecs is not None and len(vecs) == len(ordered)

        structlog.get_logger(__name__).info(
            "decision_dedup_embedding",
            groups=group_count,
            batched=batched,
        )
        if not batched:
            return cls(vector_store)
        return cls(
            vector_store,
            group_vecs=dict(zip((n for n, _ in ordered), vecs, strict=True)),
            group_texts=dict(ordered),
        )

    async def find_match(self, norm: str, headline: dict) -> str | None:
        """The id of a stored decision this group paraphrases, if any."""
        if self._store is None:
            return None
        from repowise.core.analysis.decision_semantic_match import (
            find_duplicate_decision,
            find_duplicate_decision_by_vector,
        )

        q_vec = self._group_vecs.get(norm)
        if self._batch_mode and q_vec is not None:
            return await find_duplicate_decision_by_vector(
                self._store, q_vec, pending=self._pending
            )
        return await find_duplicate_decision(
            self._store,
            title=headline.get("title", ""),
            decision=headline.get("decision") or "",
        )

    async def index_record(self, rec: DecisionRecord, norm: str) -> None:
        """Make *rec* matchable by later groups in this batch and by future runs.

        Best-effort, never blocks the SQL upsert. In batch mode the store write
        is deferred to :meth:`flush`; until then the group vector stands in for
        the record in the pending index, but only when the record's final text
        IS the group text (created, or promoted from this headline). A fold that
        kept the record's own fields must not be matchable under the incoming
        group's text.
        """
        if self._store is None:
            return
        from repowise.core.analysis.decision_semantic_match import (
            decision_match_text,
            upsert_decision_vector,
        )

        if not self._batch_mode:
            await upsert_decision_vector(
                self._store,
                rec.id,
                title=rec.title,
                decision=rec.decision or "",
                evidence_file=rec.evidence_file,
            )
            return
        q_vec = self._group_vecs.get(norm)
        final_text = decision_match_text(rec.title, rec.decision or "")
        if (
            q_vec is not None
            and self._pending is not None
            and rec.id not in self._pending.ids
            and final_text == self._group_texts.get(norm)
        ):
            self._pending.add(rec.id, q_vec)

    async def flush(self, touched_ids: list[str], id_to_rec: dict[str, DecisionRecord]) -> None:
        """Batched store write of every touched record's *final* text.

        Post merge/promotion, reusing the vectors already computed for
        unchanged texts and embedding only the delta.
        """
        if self._store is None or not self._batch_mode or not touched_ids:
            return
        from repowise.core.analysis.decision_semantic_match import (
            decision_vector_item,
            upsert_decision_vectors,
        )

        items: list[tuple[str, str, dict]] = []
        for rid in dict.fromkeys(touched_ids):
            rec = id_to_rec.get(rid)
            if rec is None:
                continue
            item = decision_vector_item(
                rec.id,
                title=rec.title,
                decision=rec.decision or "",
                evidence_file=rec.evidence_file,
            )
            if item is not None:
                items.append(item)
        vectors_by_text = {
            text: self._group_vecs[norm]
            for norm, text in self._group_texts.items()
            if norm in self._group_vecs
        }
        await upsert_decision_vectors(self._store, items, vectors_by_text=vectors_by_text)


def _supports_vector_search(vector_store: Any) -> bool:
    """Whether *vector_store* can be searched by a raw vector.

    Static capability check: the base-class ``search_by_vector`` is the
    "unsupported" sentinel. A runtime probe would conflate a transient store
    error with lack of support and silently fall back to the O(N)-round-trip
    path batch mode exists to avoid.
    """
    from repowise.core.persistence.vector_store import VectorStore
    from repowise.core.providers.embedding import store_has_semantic_vectors

    store_cls_search = getattr(type(vector_store), "search_by_vector", None)
    return (
        callable(store_cls_search)
        and store_cls_search is not VectorStore.search_by_vector
        # A keyless store supports the call and cannot answer it: every
        # matcher refuses on it. Without this the batch pays for a full embed
        # round whose vectors are then never read.
        and store_has_semantic_vectors(vector_store)
    )
