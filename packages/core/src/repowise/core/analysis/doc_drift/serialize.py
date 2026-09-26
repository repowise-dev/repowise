"""Session-free serialization and read-time rules for documentation drift.

One place where a drift finding becomes a dict, whatever produced it. Two
producers exist and they hold different objects: a store holds ORM rows, while
a pipeline run holds the analyzer's :class:`~.models.DocDriftReport` before
anything is written. Both reach the same keys through the builders here, so the
two shapes are identical by construction rather than by a test that notices
afterwards.

Nothing in this module touches a session, a query or an ORM class. That is what
lets a consumer serialize a run it never persisted --- and it is why the
read-time rules live here too. A rule left inside a route handler is forked by
the next surface that needs it, which is the drift
``tests/unit/dead_code/test_confidence_parity.py`` exists to remember.

The no-serializer rule in :mod:`~.models` still holds: it is about the
analyzer's dataclasses carrying their own ``to_dict``, and these are free
functions over them.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .constants import DETECTION_BASIS, bucket_confidences

#: Namespace for :func:`derive_doc_drift_id`, mirroring ``derive_decision_id``.
#: Versioned so a future change to the key can be told from a hash collision.
_ID_NAMESPACE = "repowise.doc_drift.id.v1"

#: Field separator for the digest. NUL cannot occur in a path, a kind or a
#: target, so no two distinct keys can join to the same string.
_FIELD_SEP = "\x00"


def _kind_str(kind: Any) -> str:
    """A ``DriftKind`` or a stored string, as the string both sides write."""
    return str(kind.value) if hasattr(kind, "value") else str(kind)


def derive_doc_drift_id(
    file_path: str,
    kind: Any,
    line_number: int,
    target: str,
) -> str:
    """The id a finding at this site has, in every store, on every run.

    Deliberately the four columns ``uq_doc_drift_finding_site`` names minus the
    repository, so a finding keeps one id across the surfaces that serve it.
    ``DocDriftFinding.id`` is an autoincrement integer, which is re-minted
    whenever a store is rebuilt rather than updated and means nothing to a
    consumer holding a report instead of rows; this is what a client keys
    triage on.

    The repository is left out on purpose, and that is the difference from
    :func:`~repowise.core.persistence.crud.decisions.derive_decision_id`. Two
    deployments index the same tree under different repository ids, so
    including it would give the same finding two ids and split the triage a
    reader thinks is one list. The consequence is accepted: ids are unique
    within a repository and are not a global key.

    32 lowercase hex, the house width.
    """
    parts = (
        _ID_NAMESPACE,
        file_path,
        _kind_str(kind),
        str(int(line_number)),
        target,
    )
    return hashlib.sha256(_FIELD_SEP.join(parts).encode("utf-8")).hexdigest()[:32]


def finding_dict(
    *,
    file_path: str,
    kind: Any,
    line_number: int,
    target: str,
    confidence: float,
    origin: str,
    reason: str,
    raw: str,
    context: str,
    evidence: Sequence[str] | None = None,
) -> dict:
    """One finding as a dict, from primitives either producer can supply.

    The parameters are the finding's fields, which is the point: a store holds
    an ORM row and a pipeline run holds a dataclass, and neither should have to
    build the other's type to be serialized. Taking the analyzer's dataclass
    instead would also force the evidence decode that ``evidence=None`` exists
    to skip.

    ``evidence=None`` drops the key for a caller under a response budget: its
    first line restates ``file_path``, ``line_number`` and ``raw``, and the
    rest is the resolver's own trace.
    """
    out = {
        "file_path": file_path,
        "line_number": int(line_number),
        "kind": _kind_str(kind),
        "target": target,
        # Rounded in one place, so a future three-decimal tier cannot make one
        # surface report 0.925 where the other reports 0.93.
        "confidence": round(float(confidence), 2),
        "origin": origin,
        "reason": reason,
        "raw": raw,
        "context": context,
    }
    if evidence is not None:
        out["evidence"] = [str(line) for line in evidence]
    return out


def reference_dict(
    *,
    document_path: str,
    kind: Any,
    line_number: int,
    section: str = "",
) -> dict:
    """One resolved reference as a dict, from primitives.

    ``target_path`` is absent because every row in a reverse answer shares it.
    The keys are ``document``/``line`` rather than the finding builder's
    ``file_path``/``line_number``: here a bare ``file_path`` would read as the
    file that was asked about, which is the one thing it is not.
    """
    out = {
        "document": document_path,
        "line": int(line_number),
        "kind": _kind_str(kind),
    }
    if section:
        out["section"] = section
    return out


def serialize_finding(finding: Any, *, evidence: bool = True) -> dict:
    """A ``DocDriftFindingData`` as a dict, identical to the stored row's.

    The dataclass carries ``evidence`` as a list where the row carries it as a
    JSON blob; that difference ends here.
    """
    return finding_dict(
        file_path=finding.file_path,
        kind=finding.kind,
        line_number=finding.line_number,
        target=finding.target,
        confidence=finding.confidence,
        origin=finding.origin,
        reason=finding.reason,
        raw=finding.raw,
        context=finding.context,
        evidence=list(finding.evidence or []) if evidence else None,
    )


def serialize_reference(reference: Any) -> dict:
    """A ``ResolvedDocReference`` as a dict, identical to the stored row's."""
    return reference_dict(
        document_path=reference.doc_path,
        kind=reference.kind,
        line_number=reference.line,
        section=reference.section,
    )


def summarize_findings(findings: Sequence[Mapping[str, Any]]) -> dict:
    """The rollup a surface shows above a finding list.

    Computed once, server-side, rather than by each client over the rows it
    happens to have been served: a client that recomputes it reports the page
    it holds as the repository. ``findings_total`` is therefore the count
    *before* any display cap, and a caller that caps says so beside it.

    ``findings_basis`` rides along because a count without it claims coverage
    this detector does not have: most references in a real tree are
    uncheckable, and a summary is exactly where that gets forgotten.
    """
    return {
        "findings_total": len(findings),
        "documents": len({f["file_path"] for f in findings}),
        "confidence": bucket_confidences(f["confidence"] for f in findings),
        "by_kind": _count_by(f["kind"] for f in findings),
        "findings_basis": DETECTION_BASIS,
    }


def _count_by(values) -> dict[str, int]:
    """Ordered counts, so two runs over equal data serialize equal bytes."""
    return dict(sorted(Counter(values).items()))


def collapse_reference_sites(
    references: Sequence[Mapping[str, Any]],
) -> list[dict]:
    """Drop the second reference at a document site, keeping the first.

    One markdown link with a fragment yields both a ``link`` row and an
    ``anchor`` row at the same line. The store keeps them apart because they
    drift apart, but a reader counting mentions should not be shown the same
    document and line twice, and a display cap should not be spent saying it
    twice.
    """
    seen: set[tuple[str, int]] = set()
    kept: list[dict] = []
    for reference in references:
        site = (reference["document"], reference["line"])
        if site in seen:
            continue
        seen.add(site)
        kept.append(dict(reference))
    return kept


def documents_with_drift(
    references: Sequence[Mapping[str, Any]],
    findings_by_document: Mapping[str, int],
) -> list[dict]:
    """Which of these documents carry drift, and how much.

    A weaker claim than it looks, and the easiest place for a surface to start
    lying: a drifted reference resolves to nothing, so no row here can say that
    a document's description of the target is wrong. It says only that a
    document naming the target has some assertion, anywhere in it, that no
    longer holds.

    Pass the full reference list, not a capped one, or a document past the cap
    reads as clean.
    """
    matched = sorted({r["document"] for r in references} & set(findings_by_document))
    return [{"document": path, "findings": findings_by_document[path]} for path in matched]


def serialize_report(report: Any) -> dict:
    """A whole ``DocDriftReport`` as one JSON-ready dict, with no session.

    For a consumer that holds a pipeline result rather than a store: it gets
    the same finding dicts a store would serve, so a surface reading one cannot
    disagree with a surface reading the other.

    References arrive pre-inverted by target path because that is the only
    question the reverse view asks, and a consumer without a database has no
    index to ask it with. Each list is ordered the way the store's own
    ``ORDER BY`` returns it, so the two agree row for row.
    """
    findings = []
    for finding in report.findings:
        # The id the route mints from a stored row, minted here too. Without it
        # a consumer serving drift from this artifact would emit findings the
        # wire contract marks as carrying one, and the table that keys its rows
        # on it would key every row on nothing.
        data = serialize_finding(finding)
        data["id"] = derive_doc_drift_id(
            data["file_path"], data["kind"], data["line_number"], data["target"]
        )
        findings.append(data)

    by_target: dict[str, list[dict]] = {}
    for reference in report.resolved_references:
        by_target.setdefault(reference.target_path, []).append(
            serialize_reference(reference)
        )
    for rows in by_target.values():
        rows.sort(key=lambda r: (r["document"], r["line"], r["kind"]))

    return {
        "findings": findings,
        "summary": summarize_findings(findings),
        "references_by_target": {k: by_target[k] for k in sorted(by_target)},
        "documents_scanned": report.documents_scanned,
        "references_checked": report.references_checked,
        "verdict_summary": dict(report.verdict_summary),
        "anchor_renderer": report.anchor_renderer,
    }
