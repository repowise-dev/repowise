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
  when measured, else ``None``; then one ``{outside: true, source, files}`` row
  for the changed files no component holds (``source`` is ``declared`` when
  a catalog is declared, else ``derived``). A reader of components skips it;
* ``reaching_tests``: one row per changed source file, ``{path, tests, ...}``;
  ``tests`` lists test ids, ``[]`` only for a measured absence, ``None`` when
  nothing knows;
* ``contracts``: ``{path, breaking, ...}``;
* ``provenance``: one row per commit, ``{sha, label, tier}``, plus ``agent`` and
  ``model`` on an agent's commit when stored;
* ``decisions_crossed``: ``{id, title, authority, status, paths}``;
* ``outcomes``: one row per later link, ``{kind, sha, change_id, target_sha,
  rule, evidence, basis}`` (``kind`` is ``revert`` or ``fix_reference``; a
  revert adds ``in_force``), then one ``{kind: "file_fix_rate", basis:
  "measured", files, files_fixed, days, window_complete, fixed}`` row, or
  ``{kind: "file_fix_rate", basis: "unresolved", reason}`` when it could not
  be counted. An unknown item's one row carries ``as_of`` and ``days``. Unlike
  the rest, this item follows HEAD: it is rebuilt when new commits arrive.
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
    "outcomes",
)

#: Kinds a checklist's status counts leave out. Health leads a change row from
#: its commits' stored deltas, so counting the checklist's own health row would
#: flag the same fact twice, from a second source.
SUMMARY_SKIPPED_KINDS: frozenset[str] = frozenset({"health_delta"})


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
        """Every reader of a kept checklist comes through here (``collect.cached_evidence``),
        so a stored status that a rule now derives differently is read as derived today."""
        status = data["status"]
        if data["kind"] == "reaching_tests" and data["basis"] != "unresolved":
            # Derived from the rows, so a kept checklist reads under the current rule.
            status = _tests_status(data.get("details") or [])
        elif data["kind"] == "health_delta" and isinstance(data.get("value"), Mapping):
            value = data["value"]
            status = _health_status(
                (value.get("introduced") or 0) + (value.get("worsened") or 0),
                bool(value.get("complete")),
            )
        return cls(
            kind=data["kind"],
            label=data["label"],
            status=status,
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
    #: The component catalog's digest when components were resolved
    #: (``components.catalog.catalog_digest``); ``None`` when not recorded.
    catalog_digest: str | None = None

    def item(self, kind: str) -> EvidenceItem | None:
        return next((i for i in self.items if i.kind == kind), None)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "schema_version": self.schema_version,
            "change_id": self.change_id,
            "revspec": self.revspec,
            "generated_at": self.generated_at,
            "index_commit": self.index_commit,
            "items": [i.to_dict() for i in self.items],
        }
        if self.catalog_digest is not None:
            out["catalog_digest"] = self.catalog_digest
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ChangeEvidence:
        return cls(
            schema_version=int(data.get("schema_version", EVIDENCE_SCHEMA_VERSION)),
            change_id=data.get("change_id"),
            revspec=data.get("revspec"),
            generated_at=data["generated_at"],
            index_commit=data.get("index_commit"),
            catalog_digest=data.get("catalog_digest"),
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
    #: ``{as_of, days, links: [link row], file_fix_rate: row | None,
    #: file_fix_rate_unknown: reason | None}`` for a merged change
    #: (:mod:`.outcomes`), or ``{as_of, days, unknown: reason}``.
    outcomes: Mapping[str, Any] | Unavailable | None = None


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
        _outcomes(inputs),
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
        [r["name"] for r in rows if not r.get("outside")],
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
    map_current = _map_current(tests.get("coverage") or {})
    rows = [_test_row(path, files.get(path), map_current) for path in sorted(set(code))]
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
        _tests_status(rows),
        "measured" if rows and all(r["measured"] for r in rows) else "inferred",
        value,
        rows,
        source,
    )


def _map_current(coverage: Mapping[str, Any]) -> bool:
    """An empty answer is a known absence only against a current measured map."""
    return bool(coverage.get("map_present")) and (
        (coverage.get("freshness") or {}).get("status") == "current"
    )


def _test_row(path: str, row: Mapping[str, Any] | None, map_current: bool) -> dict[str, Any]:
    """One changed file's reaching tests: found, a known absence (``[]``), or ``None``."""
    measured = list(row.get("measured_tests") or []) if row else []
    found = sorted(set(measured) | set(row.get("inferred_tests") or [])) if row else []
    reach: list[str] | None
    if found:
        reach = found
    elif row is not None and map_current:
        reach = []
    else:
        reach = None
    return {
        "path": path,
        "tests": reach,
        "measured": bool(measured) or reach == [],
        "measured_tests": len(measured),
        "inferred_tests": (row or {}).get("inferred_tests_total", 0),
    }


def _tests_status(rows: Sequence[Mapping[str, Any]]) -> Status:
    """Only a measured absence (``tests: []``) asks for a look; a file nothing
    knows about (``tests: None``) leaves the item partly known."""
    tests = [r.get("tests") for r in rows if isinstance(r, Mapping) and "path" in r]
    if any(t == [] for t in tests):
        return "attention"
    return "unknown" if any(t is None for t in tests) else "ok"


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
    complete = delta.status == "available"
    return EvidenceItem(
        kind,
        label,
        _health_status(worse, complete),
        "measured",
        {
            "introduced": delta.introduced_total,
            "worsened": delta.worsened_total,
            "resolved": delta.resolved_total,
            "complete": complete,
        },
        details,
        source,
    )


def _health_status(worse: int, complete: bool) -> Status:
    """A regression found is attention either way; finding none clears the
    change only when the comparison covered all of it."""
    if worse:
        return "attention"
    return "ok" if complete else "unknown"


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
                # The commit the sample runs back from: the change's parent
                # (a range's merge-base), so the rank is fixed at the change
                # and matches ``repowise risk`` however the change is spelled.
                "baseline_as_of": risk.baseline_as_of,
                "note": "ranks diff size against the commits before it; not a defect probability",
            }
        ],
        source,
    )


def _outcomes(inputs: EvidenceInputs) -> EvidenceItem:
    kind, label, source = "outcomes", "Outcomes since merge", "git history"
    if reason := _missing(inputs.outcomes):
        return _unknown(kind, label, reason, source)
    block: Mapping[str, Any] = inputs.outcomes  # type: ignore[assignment]
    stamp = {"as_of": block.get("as_of"), "days": block.get("days")}
    if block.get("unknown"):
        # Stamped, so a kept unknown is read again only once HEAD moves.
        return EvidenceItem(
            kind, label, "unknown", "unresolved", None, [{"reason": block["unknown"], **stamp}], source
        )
    links = [dict(r) for r in block.get("links") or ()]
    rate = block.get("file_fix_rate")
    value = _outcomes_value(block, links, rate)
    # A rate never asks for a look on its own: it says where fixes landed, not why.
    return EvidenceItem(
        kind,
        label,
        "attention" if value["reverted"] or value["fix_references"] else "ok",
        "declared" if links or rate is None else "measured",
        value,
        links + _rate_rows(rate, block.get("file_fix_rate_unknown")),
        source,
    )


def _outcomes_value(
    block: Mapping[str, Any], links: list[dict[str, Any]], rate: Mapping[str, Any] | None
) -> dict[str, Any]:
    reverts = [r for r in links if r["kind"] == "revert"]
    return {
        "reverted": any(r.get("in_force") for r in reverts),
        "reverts": len(reverts),
        "fix_references": sum(r["kind"] == "fix_reference" for r in links),
        "files_fixed": rate["files_fixed"] if rate else None,
        "files": rate["files"] if rate else None,
        "days": block.get("days"),
        "window_complete": rate["window_complete"] if rate else None,
        "as_of": block.get("as_of"),
    }


def _rate_rows(rate: Mapping[str, Any] | None, unknown: str | None) -> list[dict[str, Any]]:
    """The file fix rate as one detail row, or why it could not be counted."""
    if rate:
        return [{"kind": "file_fix_rate", "basis": "measured", **rate}]
    if unknown:
        return [{"kind": "file_fix_rate", "basis": "unresolved", "reason": unknown}]
    return []


# -- value lines -------------------------------------------------------------

#: Names shown in a line before the rest are counted.
_LINE_NAMES = 6


def _names(names: Sequence[Any]) -> dict[str, Any]:
    shown = [str(n) for n in names[:_LINE_NAMES]]
    return {"names": ", ".join(shown), "more": max(len(names) - len(shown), 0)}


_Line = tuple[str, dict[str, Any]]


def _named_list(kind: str, value: Any, rows: list[Mapping[str, Any]]) -> _Line | None:
    return (kind, {"count": len(value), **_names(value)}) if isinstance(value, list) else None


def _components_line(kind: str, value: Any, rows: list[Mapping[str, Any]]) -> _Line | None:
    if value == []:
        files = sum(len(r.get("files") or ()) for r in rows if r.get("outside"))
        return "components_outside", {"files": files}
    return _named_list(kind, value, rows)


def _owners_line(kind: str, value: Any, rows: list[Mapping[str, Any]]) -> _Line | None:
    if not isinstance(value, list):
        return None
    unowned = sum(len(r.get("paths") or ()) for r in rows if "owner" in r and r["owner"] is None)
    return kind, {"count": len(value), "unowned": unowned, **_names(value)}


def _tests_line(kind: str, value: Any, rows: list[Mapping[str, Any]]) -> _Line:
    files = [r for r in rows if "path" in r]
    return kind, {
        "files": len(files),
        "reached": sum(bool(r.get("tests")) for r in files),
        "absent": sum(r.get("tests") == [] for r in files),
        "unknown": sum(r.get("tests") is None for r in files),
    }


def _contracts_line(kind: str, value: Any, rows: list[Mapping[str, Any]]) -> _Line:
    breaking = value.get("breaking_symbols")
    key = "contracts" if breaking is not None else "contracts_unchecked"
    return key, {"touched": value.get("touched", 0), "breaking": breaking or 0}


def _consumers_line(kind: str, value: Any, rows: list[Mapping[str, Any]]) -> _Line:
    return kind, {"links": value.get("links", 0), **_names(value.get("repos") or [])}


def _decisions_line(kind: str, value: Any, rows: list[Mapping[str, Any]]) -> _Line:
    params = {"accepted": value.get("accepted", 0), "candidate": value.get("candidate", 0)}
    return (kind if any(params.values()) else "decisions_none"), params


def _health_line(kind: str, value: Any, rows: list[Mapping[str, Any]]) -> _Line:
    params = {k: value.get(k, 0) for k in ("introduced", "worsened", "resolved")}
    return ("health_delta" if value.get("complete") else "health_delta_partial"), params


def _provenance_line(kind: str, value: Any, rows: list[Mapping[str, Any]]) -> _Line:
    known = int(value.get("agent_commits") or 0) + int(value.get("human_attested_commits") or 0)
    commits = known + int(value.get("unknown_commits") or 0)
    # The label in the same plain words the change's facts use; agents and
    # models are named as provenance, never set against anything.
    label = str(value.get("label") or "unknown").replace("_", " ")
    named = sorted({str(r[k]) for r in rows for k in ("agent", "model") if r.get(k)})
    params = {"label": label, "known": known, "commits": commits}
    if named:
        return "provenance_named", {**params, "names": ", ".join(named)}
    return kind, params


def _risk_line(kind: str, value: Any, rows: list[Mapping[str, Any]]) -> _Line:
    params = {"percentile": value.get("percentile")}
    row = rows[0] if rows else {}
    # A checklist kept before the sample was stamped ranked against HEAD at
    # build time, so it keeps the unlabelled line rather than claim a cohort.
    if not row.get("baseline_as_of"):
        return kind, params
    sample = {"sample": row.get("baseline_sample_size", 0), "as_of": row["baseline_as_of"][:10]}
    return "risk_score_before", {**params, **sample}


def _outcomes_line(kind: str, value: Any, rows: list[Mapping[str, Any]]) -> _Line:
    params = {"reverts": value.get("reverts", 0), "references": value.get("fix_references", 0)}
    if value.get("files") is None:
        return "outcomes", params
    rate = {"fixed": value["files_fixed"], "files": value["files"], "days": value.get("days")}
    return "outcomes_rate", {**params, **rate}


#: Each kind's line from its value and rows; ``None`` reads as unknown.
_LINES: dict[str, Any] = {
    "components_touched": _components_line,
    "suggested_reviewers": _named_list,
    "owners": _owners_line,
    "reaching_tests": _tests_line,
    "contracts": _contracts_line,
    "cross_repo_consumers": _consumers_line,
    "decisions_crossed": _decisions_line,
    "health_delta": _health_line,
    "provenance": _provenance_line,
    "risk_score": _risk_line,
    "outcomes": _outcomes_line,
}


def item_line(item: Mapping[str, Any]) -> dict[str, Any]:
    """An item's value as ``{key, params}``, derived from its value and rows.

    Each surface words the line itself: the web from its message catalog, the
    CLI with :func:`line_text`. Derived on read, so a kept checklist gets it
    too. An item with no value reads ``unknown`` with the reason it carries.
    """
    kind, value = item["kind"], item.get("value")
    rows = [r for r in item.get("details") or () if isinstance(r, Mapping)]
    if value is None:
        reason = next((r["reason"] for r in rows if r.get("reason")), "")
        return {"key": "unknown", "params": {"reason": str(reason)}}
    build = _LINES.get(kind)
    line = build(kind, value, rows) if build else None
    if line is None:
        return {"key": "unknown", "params": {"reason": ""}}
    return {"key": line[0], "params": line[1]}


def _n(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def _listed(p: Mapping[str, Any]) -> str:
    return p["names"] + (f" and {p['more']} more" if p["more"] else "")


def _owners_text(key: str, p: Mapping[str, Any]) -> str:
    named = _listed(p) if p["count"] else "No declared owner"
    return named + (f"; {_n(p['unowned'], 'changed file')} without one" if p["unowned"] else "")


def _tests_text(key: str, p: Mapping[str, Any]) -> str:
    if not p["files"]:
        return "No changed source file"
    parts = [f"{p['reached']} of {_n(p['files'], 'changed file')} reached by a test"]
    parts += [f"{p['absent']} with none"] * bool(p["absent"])
    parts += [f"{p['unknown']} not known"] * bool(p["unknown"])
    return "; ".join(parts)


def _contracts_text(key: str, p: Mapping[str, Any]) -> str:
    touched = (
        _n(p["touched"], "workspace contract") + " touched"
        if p["touched"]
        else "No workspace contract touched"
    )
    if key == "contracts_unchecked":
        return f"{touched}; signatures not compared"
    broke = (
        _n(p["breaking"], "breaking signature change") if p["breaking"] else "no breaking signature change"
    )
    return f"{touched}; {broke}"


def _health_text(key: str, p: Mapping[str, Any]) -> str:
    text = f"Findings: {p['introduced']} introduced, {p['worsened']} worsened, {p['resolved']} resolved"
    return text + (" (partial comparison)" if key == "health_delta_partial" else "")


def _provenance_text(key: str, p: Mapping[str, Any]) -> str:
    text = f"provenance: {p['label']}, known for {p['known']} of {_n(p['commits'], 'commit')}"
    return text + (f" ({p['names']})" if key == "provenance_named" else "")


def _outcomes_text(key: str, p: Mapping[str, Any]) -> str:
    text = f"{_n(p['reverts'], 'revert')}, {_n(p['references'], 'cause reference')}"
    if key == "outcomes_rate":
        text += f"; {p['fixed']} of {_n(p['files'], 'file')} fixed within {p['days']} days"
    return text


#: Each line key in plain English, the CLI's wording of the web's catalog.
_TEXT: dict[str, Any] = {
    "unknown": lambda k, p: f"Not known: {p['reason']}" if p["reason"] else "Not known",
    "components_touched": lambda k, p: f"{_n(p['count'], 'component')}: {_listed(p)}",
    "components_outside": lambda k, p: f"Outside every component ({_n(p['files'], 'changed file')})",
    "suggested_reviewers": lambda k, p: _listed(p),
    "owners": _owners_text,
    "reaching_tests": _tests_text,
    "contracts": _contracts_text,
    "contracts_unchecked": _contracts_text,
    "cross_repo_consumers": lambda k, p: (
        f"{_n(p['links'], 'consumer')} in {_listed(p)}" if p["links"] else "No consumer in another repo"
    ),
    "decisions_none": lambda k, p: "No decision crossed",
    "decisions_crossed": lambda k, p: (
        f"{_n(p['accepted'], 'accepted decision')}, {_n(p['candidate'], 'candidate')}"
    ),
    "health_delta": _health_text,
    "health_delta_partial": _health_text,
    "provenance": _provenance_text,
    "provenance_named": _provenance_text,
    "risk_score": lambda k, p: f"Larger than {p['percentile']}% of recent commits",
    "risk_score_before": lambda k, p: (
        f"Larger than {p['percentile']}% of the {_n(p['sample'], 'commit')} before this change "
        f"(as of {p['as_of']})"
    ),
    "outcomes": _outcomes_text,
    "outcomes_rate": _outcomes_text,
}


def line_text(line: Mapping[str, Any]) -> str:
    """A :func:`item_line` in plain English, the CLI's wording of the web's catalog."""
    word = _TEXT.get(line["key"])
    return word(line["key"], line["params"]) if word else ""


#: The basis in words; ``unresolved`` is what a reader calls not known.
BASIS_TEXT = {
    "measured": "measured",
    "inferred": "inferred",
    "declared": "declared",
    "unresolved": "not known",
}


__all__ = [
    "BASIS_TEXT",
    "EVIDENCE_SCHEMA_VERSION",
    "KINDS",
    "ChangeEvidence",
    "EvidenceInputs",
    "EvidenceItem",
    "Unavailable",
    "build_change_evidence",
    "item_line",
    "line_text",
]
