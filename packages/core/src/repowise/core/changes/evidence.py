"""The evidence checklist on a Change Record, as versioned JSON.

One :class:`ChangeEvidence` per change: a fixed list of checkable items, each
saying what was found (``value``), whether it needs a reviewer's eye
(``status``) and how it is known (``basis``). The risk score is one item
among them, never the headline.

:func:`build_change_evidence` is the one builder every surface calls. It reads
a :class:`~repowise.core.analysis.change_review.ChangeReviewBundle` (health
delta, tests, contracts, risk) plus the index facts in :class:`EvidenceInputs`
and decides each item's status here, so the CLI, REST, MCP and a PR comment
cannot reach different verdicts from the same facts. Gathering those facts is
:mod:`.collect`'s job; nothing here does IO.

Detail rows a policy reads have fixed keys (the local change policy in
``repowise.core.policy`` evaluates them):

* ``components_touched``: ``{name, source, owner, criticality, tags, files,
  bus_factor}``; ``owner`` is a team name or ``None``, ``bus_factor`` an int
  when measured, else ``None``;
* ``reaching_tests``: one row per changed source file, ``{path, tests, ...}``;
  ``tests`` lists test ids, ``[]`` only for a measured absence, ``None`` when
  nothing knows;
* ``contracts``: ``{path, breaking, ...}``;
* ``provenance``: one row per commit, ``{sha, label, tier}``;
* ``decisions_crossed``: ``{id, title, authority, status, paths}``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from repowise.core.analysis.change_review import ChangeReviewBundle

#: Bumped when a field is renamed or removed or changes meaning; adding one does not.
EVIDENCE_SCHEMA_VERSION = 1

Status = Literal["ok", "attention", "unknown"]
Basis = Literal["measured", "inferred", "declared", "unresolved"]

#: Every item a record carries, in this order.
KINDS: tuple[str, ...] = (
    "components_touched",
    "owners",
    "suggested_reviewers",
    "reaching_tests",
    "contracts",
    "cross_repo_consumers",
    "decisions_crossed",
    "health_delta",
    "provenance",
    "risk_score",
)


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    kind: str
    label: str
    status: Status
    basis: Basis
    value: Any = None
    details: list[dict[str, Any]] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "basis": self.basis,
            "value": self.value,
            "details": list(self.details),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceItem:
        return cls(
            kind=data["kind"],
            label=data["label"],
            status=data["status"],
            basis=data["basis"],
            value=data.get("value"),
            details=list(data.get("details") or []),
            source=data.get("source", ""),
        )


@dataclass(frozen=True, slots=True)
class ChangeEvidence:
    change_id: str | None
    revspec: str | None
    generated_at: str
    items: list[EvidenceItem]
    schema_version: int = EVIDENCE_SCHEMA_VERSION
    #: The indexed commit the index facts were read at; ``None`` when unknown.
    index_commit: str | None = None

    def item(self, kind: str) -> EvidenceItem | None:
        return next((i for i in self.items if i.kind == kind), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "change_id": self.change_id,
            "revspec": self.revspec,
            "generated_at": self.generated_at,
            "index_commit": self.index_commit,
            "items": [i.to_dict() for i in self.items],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ChangeEvidence:
        return cls(
            schema_version=int(data.get("schema_version", EVIDENCE_SCHEMA_VERSION)),
            change_id=data.get("change_id"),
            revspec=data.get("revspec"),
            generated_at=data["generated_at"],
            index_commit=data.get("index_commit"),
            items=[EvidenceItem.from_dict(i) for i in data.get("items") or []],
        )


@dataclass(frozen=True, slots=True)
class Unavailable:
    """An input that could not be read, and why. Its item reads ``unknown``."""

    reason: str


@dataclass(frozen=True, slots=True)
class EvidenceInputs:
    """Index facts the bundle does not carry. ``None`` means nobody gathered it.

    Rows are plain dicts so a surface with its own stores can fill them.
    """

    #: Touched production paths (no tests), used to judge owner and test gaps.
    #: ``None`` takes every manifest path.
    code_paths: Sequence[str] | None = None
    #: ``{name, source, owner, criticality, tags, files, bus_factor}`` per
    #: touched component.
    components: Sequence[Mapping[str, Any]] | Unavailable | None = None
    #: ``{path: [owner, ...]}`` from CODEOWNERS and component owners; a path
    #: with no declared owner is absent. ``owner_sources`` names what was read.
    owners: Mapping[str, Sequence[str]] | Unavailable | None = None
    owner_sources: Sequence[str] = ()
    #: ``{name, paths, reasons}``, by name; no score, count or rank.
    reviewers: Sequence[Mapping[str, Any]] | Unavailable | None = None
    #: ``{path, contract_id, type, role}`` for workspace contracts in touched files.
    contracts: Sequence[Mapping[str, Any]] | Unavailable | None = None
    #: ``{repo, file, contract_id, provider_file}`` for other repos' consumers.
    consumers: Sequence[Mapping[str, Any]] | Unavailable | None = None
    #: ``{id, title, authority, status, path}`` per governing decision crossed.
    decisions: Sequence[Mapping[str, Any]] | Unavailable | None = None
    #: The provenance summary (``label``, counts, ``coverage``) plus
    #: ``commits``: ``[{sha, label, tier}]``.
    provenance: Mapping[str, Any] | Unavailable | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _unknown(kind: str, label: str, reason: str, source: str) -> EvidenceItem:
    return EvidenceItem(kind, label, "unknown", "unresolved", None, [{"reason": reason}], source)


def _missing(value: Any) -> str | None:
    """Why an input reads unknown, or ``None`` when it was gathered."""
    if value is None:
        return "not gathered for this change"
    if isinstance(value, Unavailable):
        return value.reason
    return None


def build_change_evidence(
    bundle: ChangeReviewBundle,
    inputs: EvidenceInputs | None = None,
    *,
    change_id: str | None,
    revspec: str | None,
    generated_at: str | None = None,
    index_commit: str | None = None,
) -> ChangeEvidence:
    """The checklist for one change, every kind in :data:`KINDS` present."""
    inputs = inputs or EvidenceInputs()
    paths = [e.path for e in bundle.manifest]
    code = list(inputs.code_paths) if inputs.code_paths is not None else paths
    items = [
        _components(inputs),
        _owners(inputs, code),
        _reviewers(inputs),
        _tests(bundle, code),
        _contracts(bundle, inputs),
        _consumers(inputs),
        _decisions(inputs),
        _health(bundle),
        _provenance(inputs),
        _risk(bundle),
    ]
    return ChangeEvidence(
        change_id=change_id,
        revspec=revspec,
        generated_at=generated_at or _now(),
        items=items,
        index_commit=index_commit,
    )


# -- items -------------------------------------------------------------------


def _components(inputs: EvidenceInputs) -> EvidenceItem:
    kind, label, source = "components_touched", "Components touched", "components"
    if reason := _missing(inputs.components):
        return _unknown(kind, label, reason, source)
    rows = [dict(r) for r in inputs.components]  # type: ignore[union-attr]
    if not rows:
        return _unknown(kind, label, "no component holds the changed files", source)
    declared = any(r.get("source") == "declared" for r in rows)
    return EvidenceItem(
        kind,
        label,
        "ok",
        "declared" if declared else "inferred",
        [r["name"] for r in rows],
        rows,
        source,
    )


def _owners(inputs: EvidenceInputs, code: list[str]) -> EvidenceItem:
    kind, label = "owners", "Declared owners"
    source = ", ".join(inputs.owner_sources) or "CODEOWNERS, component catalog"
    if reason := _missing(inputs.owners):
        return _unknown(kind, label, reason, source)
    owners: Mapping[str, Sequence[str]] = inputs.owners  # type: ignore[assignment]
    if not inputs.owner_sources:
        return _unknown(kind, label, "the repository declares no owners", source)
    by_owner: dict[str, list[str]] = {}
    for path, names in owners.items():
        for name in names:
            by_owner.setdefault(name, []).append(path)
    unowned = sorted(p for p in code if not owners.get(p))
    details = [{"owner": o, "paths": sorted(ps)} for o, ps in sorted(by_owner.items())]
    if unowned:
        details.append({"owner": None, "paths": unowned})
    return EvidenceItem(
        kind,
        label,
        "attention" if unowned else "ok",
        "declared",
        sorted(by_owner),
        details,
        source,
    )


def _reviewers(inputs: EvidenceInputs) -> EvidenceItem:
    kind, label, source = "suggested_reviewers", "Suggested reviewers", "git history"
    if reason := _missing(inputs.reviewers):
        return _unknown(kind, label, reason, source)
    rows = [dict(r) for r in inputs.reviewers]  # type: ignore[union-attr]
    if not rows:
        return _unknown(kind, label, "no one else has history in these files", source)
    return EvidenceItem(kind, label, "ok", "inferred", [r["name"] for r in rows], rows, source)


def _tests(bundle: ChangeReviewBundle, code: list[str]) -> EvidenceItem:
    kind, label, source = "reaching_tests", "Tests reaching the change", "test impact"
    tests = bundle.tests
    if tests is None:
        return _unknown(kind, label, bundle.lane("tests").reason or "not gathered", source)
    by_basis = tests.get("recommendations_by_primary_basis") or {}
    files = {row["source_file"]: row for row in tests.get("files") or []}
    coverage = tests.get("coverage") or {}
    # An empty answer is a known absence only against a current measured map.
    map_current = bool(coverage.get("map_present")) and (
        (coverage.get("freshness") or {}).get("status") == "current"
    )
    rows: list[dict[str, Any]] = []
    for path in sorted(set(code)):
        row = files.get(path)
        measured = list(row.get("measured_tests") or []) if row else []
        found = sorted(set(measured) | set(row.get("inferred_tests") or [])) if row else []
        if found:
            reach: list[str] | None = found
        elif row is not None and map_current:
            reach = []
        else:
            reach = None
        rows.append(
            {
                "path": path,
                "tests": reach,
                "measured": bool(measured) or reach == [],
                "measured_tests": len(measured),
                "inferred_tests": (row or {}).get("inferred_tests_total", 0),
            }
        )
    absent = sum(r["tests"] == [] for r in rows)
    unknown = sum(r["tests"] is None for r in rows)
    value = {
        "measured": int(by_basis.get("measured", 0)),
        "inferred": int(by_basis.get("inferred", 0)),
        "files_without_tests": absent,
        "files_unknown": unknown,
    }
    if rows and unknown == len(rows):
        return EvidenceItem(kind, label, "unknown", "unresolved", value, rows, source)
    return EvidenceItem(
        kind,
        label,
        "attention" if absent or unknown else "ok",
        "measured" if rows and all(r["measured"] for r in rows) else "inferred",
        value,
        rows,
        source,
    )


def _contracts(bundle: ChangeReviewBundle, inputs: EvidenceInputs) -> EvidenceItem:
    kind, label, source = "contracts", "Contracts touched", "workspace contracts"
    symbol = bundle.contracts if bundle.lane("contracts").state != "unsupported" else None
    breaking = symbol.breaking if symbol is not None else []
    reason = _missing(inputs.contracts)
    if reason and symbol is None:
        return _unknown(kind, label, reason, source)
    rows = [] if reason else [dict(r) for r in inputs.contracts]  # type: ignore[union-attr]
    details = [{**r, "breaking": False} for r in rows] + [
        {"path": c.file, "symbol_id": c.symbol_id, "change": c.change, "breaking": True}
        for c in breaking
    ]
    return EvidenceItem(
        kind,
        label,
        "attention" if rows or breaking else "ok",
        "measured" if symbol is not None and not rows else "inferred",
        {"touched": len(rows), "breaking_symbols": len(breaking) if symbol is not None else None},
        details,
        source if symbol is None else f"{source}, signature diff",
    )


def _consumers(inputs: EvidenceInputs) -> EvidenceItem:
    kind, label, source = "cross_repo_consumers", "Consumers in other repos", "workspace contracts"
    if reason := _missing(inputs.consumers):
        return _unknown(kind, label, reason, source)
    rows = [dict(r) for r in inputs.consumers]  # type: ignore[union-attr]
    repos = sorted({r["repo"] for r in rows if r.get("repo")})
    return EvidenceItem(
        kind,
        label,
        "attention" if rows else "ok",
        "inferred",
        {"repos": repos, "links": len(rows)},
        rows,
        source,
    )


def _decisions(inputs: EvidenceInputs) -> EvidenceItem:
    kind, label, source = "decisions_crossed", "Decisions crossed", "decision records"
    if reason := _missing(inputs.decisions):
        return _unknown(kind, label, reason, source)
    rows = [dict(r) for r in inputs.decisions]  # type: ignore[union-attr]
    accepted = sum(r.get("authority") == "accepted" for r in rows)
    return EvidenceItem(
        kind,
        label,
        "attention" if accepted else "ok",
        "declared" if accepted else ("inferred" if rows else "declared"),
        {"accepted": accepted, "candidate": len(rows) - accepted},
        rows,
        source,
    )


def _health(bundle: ChangeReviewBundle) -> EvidenceItem:
    kind, label, source = "health_delta", "Health delta", "change health"
    delta = bundle.health
    if delta is None or delta.status not in ("available", "partial"):
        reason = delta.explanation if delta is not None else bundle.lane("health").reason
        return _unknown(kind, label, reason or "not compared", source)
    worse = delta.introduced_total + delta.worsened_total
    details = [
        {
            "change": f.change_kind,
            "dimension": f.dimension,
            "severity": f.severity,
            "path": f.path,
            "symbol": f.symbol,
            "reason": f.reason,
        }
        for f in delta.findings
    ]
    if delta.status == "partial":
        details.append({"note": delta.explanation})
    return EvidenceItem(
        kind,
        label,
        "attention" if worse else "ok",
        "measured",
        {
            "introduced": delta.introduced_total,
            "worsened": delta.worsened_total,
            "resolved": delta.resolved_total,
            "complete": delta.status == "available",
        },
        details,
        source,
    )


def _provenance(inputs: EvidenceInputs) -> EvidenceItem:
    kind, label, source = "provenance", "AI provenance", "attribution"
    if reason := _missing(inputs.provenance):
        return _unknown(kind, label, reason, source)
    block = dict(inputs.provenance)  # type: ignore[arg-type]
    commits = [dict(c) for c in block.pop("commits", None) or []]
    # Unknown is never human: a change with any unattributed commit stays
    # unknown, and an unknown label answers nothing.
    unknown = block["label"] == "unknown"
    return EvidenceItem(
        kind,
        label,
        "unknown" if unknown else "ok",
        "unresolved" if unknown else "measured",
        block,
        commits,
        source,
    )


def _risk(bundle: ChangeReviewBundle) -> EvidenceItem:
    kind, label, source = "risk_score", "Diff size rank", "change risk"
    risk = bundle.risk
    if risk is None or risk.percentile is None:
        reason = bundle.lane("risk").reason or "not scored"
        return _unknown(kind, label, reason, source)
    # One row, and never "attention": the rank is not calibrated as a defect
    # probability, so it informs a reviewer and decides nothing.
    return EvidenceItem(
        kind,
        label,
        "ok",
        "measured",
        {"percentile": round(risk.percentile, 1)},
        [
            {
                "baseline_sample_size": risk.baseline_sample_size,
                "note": "ranks diff size against recent commits; not a defect probability",
            }
        ],
        source,
    )


__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "KINDS",
    "ChangeEvidence",
    "EvidenceInputs",
    "EvidenceItem",
    "Unavailable",
    "build_change_evidence",
]
