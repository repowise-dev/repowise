"""Breaking-change guard — diff a workspace's provider contracts across updates.

When ``repowise update --workspace`` re-extracts contracts, this module diffs the
freshly-extracted set against the *previously persisted* one and reports the
provider changes that break consumers across repos: a removed endpoint, a removed
or retyped request/response field, a reused field number, a newly-required field.
Each :class:`BreakingChange` resolves its impacted consumers from the matched
contract links — the same provider→consumer evidence the system graph's edges are
built from — so a break is reported with the exact consumer files that call it.

**Honest non-goals.** A *non*-breaking change (an added optional field, a widened
type, a new endpoint) produces no record. Impact is intentionally *direct*: a
contract break endangers the consumers of that contract, which is exactly the
first hop the cross-repo blast radius would surface — so we read it straight from
the links rather than re-walking the graph. Transitive ripple stays the job of
``get_blast_radius`` / the map overlay.

**Extensibility (the D10 / PR #505 bar).** Diff rules live in two registries —
contract-level (:data:`_CONTRACT_RULES`) and field-level (:data:`_FIELD_RULES`).
Adding a new breaking-change kind is a new ``@contract_rule`` / ``@field_rule``
function, never an ``if/elif`` in the engine. The engine only walks providers,
dispatches to the registries, and attaches impact.

Pure and I/O-free except the thin ``save`` / ``load`` helpers and the
``run_breaking_change_detection`` orchestrator at the bottom.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repowise.core.workspace.config import WORKSPACE_DATA_DIR, ensure_workspace_data_dir
from repowise.core.workspace.contract_schema import ContractSchema, SchemaField
from repowise.core.workspace.contracts import (
    Contract,
    ContractLink,
    ContractStore,
    normalize_contract_id,
)

_log = logging.getLogger("repowise.workspace.breaking_change")

# ---------------------------------------------------------------------------
# Constants (single source of truth)
# ---------------------------------------------------------------------------

BREAKING_CHANGES_FILENAME = "breaking_changes.json"

#: Severity levels. ``breaking`` = wire/contract-incompatible (consumers break);
#: ``warning`` = source-compatibility risk (e.g. a removed request field a proto3
#: server simply ignores) that still merits a look.
SEVERITY_BREAKING = "breaking"
SEVERITY_WARNING = "warning"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


def _node_id(repo: str, service: str | None) -> str:
    """System-graph node id for a (repo, service) pair — mirrors system_graph."""
    return repo if not service else f"{repo}::{service}"


@dataclass
class ImpactedConsumer:
    """A consumer endangered by a provider's breaking change.

    Resolved from the matched contract link, so ``file``/``symbol`` point at the
    exact code that calls the changed contract. ``node_id`` is the system-graph
    node (for map badging); ``match_type`` carries how confidently the link was
    matched (an ``exact`` consumer is more certainly broken than a ``candidate``).
    """

    repo: str
    service: str | None
    node_id: str
    file: str
    symbol: str
    match_type: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "service": self.service,
            "node_id": self.node_id,
            "file": self.file,
            "symbol": self.symbol,
            "match_type": self.match_type,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImpactedConsumer:
        return cls(
            repo=data.get("repo", ""),
            service=data.get("service"),
            node_id=data.get("node_id", ""),
            file=data.get("file", ""),
            symbol=data.get("symbol", ""),
            match_type=data.get("match_type", "exact"),
            confidence=data.get("confidence", 0.0),
        )


@dataclass
class BreakingChange:
    """One incompatible provider change and the consumers it endangers."""

    kind: str  # registry key — removed_endpoint | removed_field | ...
    severity: str  # SEVERITY_BREAKING | SEVERITY_WARNING
    contract_id: str
    contract_type: str  # http | grpc | topic
    provider_repo: str
    provider_file: str
    provider_symbol: str
    provider_service: str | None
    detail: str  # human-readable one-liner
    field_name: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    impacted_consumers: list[ImpactedConsumer] = field(default_factory=list)

    @property
    def provider_node_id(self) -> str:
        return _node_id(self.provider_repo, self.provider_service)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.kind,
            "severity": self.severity,
            "contract_id": self.contract_id,
            "contract_type": self.contract_type,
            "provider_repo": self.provider_repo,
            "provider_file": self.provider_file,
            "provider_symbol": self.provider_symbol,
            "provider_service": self.provider_service,
            "provider_node_id": self.provider_node_id,
            "detail": self.detail,
            "impacted_consumers": [c.to_dict() for c in self.impacted_consumers],
        }
        if self.field_name is not None:
            d["field_name"] = self.field_name
        if self.old_value is not None:
            d["old_value"] = self.old_value
        if self.new_value is not None:
            d["new_value"] = self.new_value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BreakingChange:
        return cls(
            kind=data["kind"],
            severity=data.get("severity", SEVERITY_BREAKING),
            contract_id=data.get("contract_id", ""),
            contract_type=data.get("contract_type", ""),
            provider_repo=data.get("provider_repo", ""),
            provider_file=data.get("provider_file", ""),
            provider_symbol=data.get("provider_symbol", ""),
            provider_service=data.get("provider_service"),
            detail=data.get("detail", ""),
            field_name=data.get("field_name"),
            old_value=data.get("old_value"),
            new_value=data.get("new_value"),
            impacted_consumers=[
                ImpactedConsumer.from_dict(c) for c in data.get("impacted_consumers", [])
            ],
        )


@dataclass
class BreakingChangeReport:
    """The full set of breaking changes from one workspace update + rollups."""

    version: int = 1
    generated_at: str = ""
    changes: list[BreakingChange] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)

    @property
    def breaking_count(self) -> int:
        return sum(1 for c in self.changes if c.severity == SEVERITY_BREAKING)

    @property
    def warning_count(self) -> int:
        return sum(1 for c in self.changes if c.severity == SEVERITY_WARNING)

    @property
    def impacted_repos(self) -> list[str]:
        repos: set[str] = set()
        for c in self.changes:
            for ic in c.impacted_consumers:
                repos.add(ic.repo)
        return sorted(repos)

    @property
    def impacted_services(self) -> list[str]:
        services: set[str] = set()
        for c in self.changes:
            for ic in c.impacted_consumers:
                services.add(ic.node_id)
        return sorted(services)

    @property
    def total_impacted_consumers(self) -> int:
        return sum(len(c.impacted_consumers) for c in self.changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "changes": [c.to_dict() for c in self.changes],
            "total": len(self.changes),
            "breaking_count": self.breaking_count,
            "warning_count": self.warning_count,
            "impacted_repos": self.impacted_repos,
            "impacted_services": self.impacted_services,
            "total_impacted_consumers": self.total_impacted_consumers,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BreakingChangeReport:
        return cls(
            version=data.get("version", 1),
            generated_at=data.get("generated_at", ""),
            changes=[BreakingChange.from_dict(c) for c in data.get("changes", [])],
        )


# ---------------------------------------------------------------------------
# Rule registries (plugin shape — add a kind = add a decorated function)
# ---------------------------------------------------------------------------


@dataclass
class _RawChange:
    """A rule's verdict before provider/consumer context is attached."""

    kind: str
    severity: str
    detail: str
    field_name: str | None = None
    old_value: str | None = None
    new_value: str | None = None


#: Contract-level rule: sees the previous and current provider contract for one
#: normalized contract id (either may be ``None`` for added/removed endpoints).
ContractRule = Callable[["Contract | None", "Contract | None"], Iterable[_RawChange]]

#: Field-level rule: sees one field's previous + current state on a given side.
FieldRule = Callable[["_FieldDiff"], "_RawChange | None"]

_CONTRACT_RULES: list[ContractRule] = []
_FIELD_RULES: list[FieldRule] = []


def contract_rule(fn: ContractRule) -> ContractRule:
    """Register a contract-level diff rule."""
    _CONTRACT_RULES.append(fn)
    return fn


def field_rule(fn: FieldRule) -> FieldRule:
    """Register a field-level diff rule."""
    _FIELD_RULES.append(fn)
    return fn


@dataclass
class _FieldDiff:
    """One field's previous/current state on a request or response side."""

    side: str  # "request" | "response"
    prev: SchemaField | None
    curr: SchemaField | None


# -- contract-level rules ---------------------------------------------------


@contract_rule
def _removed_endpoint(prev: Contract | None, curr: Contract | None) -> list[_RawChange]:
    """A provider contract that existed before and is gone now.

    Covers a removed route/method (HTTP), a removed ``service/method`` (gRPC),
    and a removed topic — the contract id encodes all three, so a vanished id is
    a removed surface regardless of transport.
    """
    if prev is not None and curr is None:
        return [
            _RawChange(
                kind="removed_endpoint",
                severity=SEVERITY_BREAKING,
                detail=f"{prev.contract_id} was removed",
            )
        ]
    return []


# -- field-level rules ------------------------------------------------------


@field_rule
def _removed_field(diff: _FieldDiff) -> _RawChange | None:
    """A field present before and gone now.

    A removed *response* field breaks consumers that read it; a removed *request*
    field is a source-compat warning (a server simply stops reading it).
    """
    if diff.prev is not None and diff.curr is None:
        severity = SEVERITY_BREAKING if diff.side == "response" else SEVERITY_WARNING
        return _RawChange(
            kind="removed_field",
            severity=severity,
            detail=f"{diff.side} field '{diff.prev.name}' was removed",
            field_name=diff.prev.name,
            old_value=diff.prev.type,
        )
    return None


@field_rule
def _field_type_changed(diff: _FieldDiff) -> _RawChange | None:
    """A field whose type changed — wire-incompatible on either side."""
    if diff.prev is not None and diff.curr is not None and diff.prev.type != diff.curr.type:
        return _RawChange(
            kind="field_type_changed",
            severity=SEVERITY_BREAKING,
            detail=(
                f"{diff.side} field '{diff.curr.name}' type changed "
                f"{diff.prev.type} → {diff.curr.type}"
            ),
            field_name=diff.curr.name,
            old_value=diff.prev.type,
            new_value=diff.curr.type,
        )
    return None


@field_rule
def _field_number_changed(diff: _FieldDiff) -> _RawChange | None:
    """A field whose wire number (proto tag) changed — silently corrupts data."""
    if (
        diff.prev is not None
        and diff.curr is not None
        and diff.prev.number is not None
        and diff.curr.number is not None
        and diff.prev.number != diff.curr.number
    ):
        return _RawChange(
            kind="field_number_changed",
            severity=SEVERITY_BREAKING,
            detail=(
                f"{diff.side} field '{diff.curr.name}' number changed "
                f"{diff.prev.number} → {diff.curr.number}"
            ),
            field_name=diff.curr.name,
            old_value=str(diff.prev.number),
            new_value=str(diff.curr.number),
        )
    return None


@field_rule
def _field_required_tightened(diff: _FieldDiff) -> _RawChange | None:
    """A field that newly requires a value — an added or made-required field.

    An optional→required change, or a brand-new required field, forces callers to
    supply something they previously omitted. An added *optional* field never
    fires here (the non-breaking case the guard must stay quiet about).
    """
    if (
        diff.curr is not None
        and diff.curr.required
        and (diff.prev is None or not diff.prev.required)
    ):
        action = "added as required" if diff.prev is None else "became required"
        return _RawChange(
            kind="field_required",
            severity=SEVERITY_BREAKING,
            detail=f"{diff.side} field '{diff.curr.name}' {action}",
            field_name=diff.curr.name,
            new_value=diff.curr.type,
        )
    return None


# ---------------------------------------------------------------------------
# Schema diffing (pure)
# ---------------------------------------------------------------------------


def _diff_field_side(
    side: str, prev: list[SchemaField], curr: list[SchemaField]
) -> list[_RawChange]:
    """Run every field rule over the union of field names on one side."""
    prev_by_name = {f.name: f for f in prev}
    curr_by_name = {f.name: f for f in curr}
    raws: list[_RawChange] = []
    for name in list(prev_by_name) + [n for n in curr_by_name if n not in prev_by_name]:
        diff = _FieldDiff(side=side, prev=prev_by_name.get(name), curr=curr_by_name.get(name))
        for rule in _FIELD_RULES:
            raw = rule(diff)
            if raw is not None:
                raws.append(raw)
    return raws


def _diff_schemas(prev: ContractSchema, curr: ContractSchema) -> list[_RawChange]:
    """Diff request and response field sets of two schemas."""
    return _diff_field_side("request", prev.request_fields, curr.request_fields) + _diff_field_side(
        "response", prev.response_fields, curr.response_fields
    )


# ---------------------------------------------------------------------------
# Impact resolution (reuses the contract links the graph edges are built from)
# ---------------------------------------------------------------------------


def _index_providers(contracts: list[Contract]) -> dict[str, Contract]:
    """Map normalized contract id → provider contract (last write wins)."""
    out: dict[str, Contract] = {}
    for c in contracts:
        if c.role == "provider":
            out[normalize_contract_id(c.contract_id)] = c
    return out


def _index_links(links: list[ContractLink]) -> dict[str, list[ContractLink]]:
    """Map normalized contract id → matched links (provider↔consumer)."""
    out: dict[str, list[ContractLink]] = {}
    for lk in links:
        out.setdefault(normalize_contract_id(lk.contract_id), []).append(lk)
    return out


def _resolve_impacted(
    norm_id: str,
    current_links: dict[str, list[ContractLink]],
    previous_links: dict[str, list[ContractLink]],
) -> list[ImpactedConsumer]:
    """Direct consumers of a changed contract, from the matched links.

    Prefers the current links; for a *removed* endpoint there is no current
    provider (hence no current link), so we fall back to the previous links,
    which still record who used to call it. De-duplicated by consumer node+file.
    """
    links = current_links.get(norm_id) or previous_links.get(norm_id) or []
    seen: set[tuple[str, str]] = set()
    consumers: list[ImpactedConsumer] = []
    for lk in links:
        node_id = _node_id(lk.consumer_repo, lk.consumer_service)
        key = (node_id, lk.consumer_file)
        if key in seen:
            continue
        seen.add(key)
        consumers.append(
            ImpactedConsumer(
                repo=lk.consumer_repo,
                service=lk.consumer_service,
                node_id=node_id,
                file=lk.consumer_file,
                symbol=lk.consumer_symbol,
                match_type=lk.match_type,
                confidence=lk.confidence,
            )
        )
    consumers.sort(key=lambda c: (-c.confidence, c.node_id, c.file))
    return consumers


# ---------------------------------------------------------------------------
# Engine (pure)
# ---------------------------------------------------------------------------


def detect_breaking_changes(
    previous: ContractStore,
    current: ContractStore,
    *,
    version: int = 1,
    generated_at: str = "",
) -> BreakingChangeReport:
    """Diff *previous* vs *current* provider contracts into a report (pure).

    Walks every provider contract id seen in either store, dispatches the
    contract-level and (when both sides carry a schema) field-level rules, and
    attaches each change's direct cross-repo consumers from the matched links.
    """
    prev_providers = _index_providers(previous.contracts)
    curr_providers = _index_providers(current.contracts)
    current_links = _index_links(current.contract_links)
    previous_links = _index_links(previous.contract_links)

    changes: list[BreakingChange] = []
    for norm_id in sorted(set(prev_providers) | set(curr_providers)):
        prev_c = prev_providers.get(norm_id)
        curr_c = curr_providers.get(norm_id)

        raws: list[_RawChange] = []
        for rule in _CONTRACT_RULES:
            raws.extend(rule(prev_c, curr_c))
        if prev_c is not None and curr_c is not None:
            prev_schema = prev_c.schema
            curr_schema = curr_c.schema
            if prev_schema is not None and curr_schema is not None:
                raws.extend(_diff_schemas(prev_schema, curr_schema))

        if not raws:
            continue

        rep = prev_c or curr_c
        assert rep is not None  # guaranteed: norm_id came from one of the maps
        impacted = _resolve_impacted(norm_id, current_links, previous_links)
        for raw in raws:
            changes.append(
                BreakingChange(
                    kind=raw.kind,
                    severity=raw.severity,
                    contract_id=rep.contract_id,
                    contract_type=rep.contract_type,
                    provider_repo=rep.repo,
                    provider_file=rep.file_path,
                    provider_symbol=rep.symbol_name,
                    provider_service=rep.service,
                    detail=raw.detail,
                    field_name=raw.field_name,
                    old_value=raw.old_value,
                    new_value=raw.new_value,
                    impacted_consumers=impacted,
                )
            )

    # Stable order: breaking before warning, then by contract id + kind.
    changes.sort(key=lambda c: (c.severity != SEVERITY_BREAKING, c.contract_id, c.kind))
    return BreakingChangeReport(version=version, generated_at=generated_at, changes=changes)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_breaking_change_report(report: BreakingChangeReport, workspace_root: Path) -> Path:
    """Write the report to ``.repowise-workspace/breaking_changes.json``."""
    data_dir = ensure_workspace_data_dir(workspace_root)
    out_path = data_dir / BREAKING_CHANGES_FILENAME
    out_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


def load_breaking_change_report(workspace_root: Path) -> BreakingChangeReport | None:
    """Load the report, or ``None`` if missing/unparseable."""
    path = workspace_root / WORKSPACE_DATA_DIR / BREAKING_CHANGES_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return BreakingChangeReport.from_dict(data)
    except Exception:
        _log.warning("Failed to load breaking-change report from %s", path, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_breaking_change_detection(
    workspace_root: Path,
    previous: ContractStore,
    current: ContractStore,
    *,
    generated_at: str = "",
) -> BreakingChangeReport:
    """Diff the previous vs current contract stores and persist the report.

    Called from ``run_cross_repo_hooks`` with the contract store as it was on
    disk *before* this update overwrote it (``previous``) and the freshly
    extracted store (``current``).
    """
    report = detect_breaking_changes(previous, current, generated_at=generated_at)
    out_path = save_breaking_change_report(report, workspace_root)
    _log.info(
        "Breaking-change detection complete: %d change(s) (%d breaking) impacting "
        "%d consumer(s) across %d repo(s) → %s",
        len(report.changes),
        report.breaking_count,
        report.total_impacted_consumers,
        len(report.impacted_repos),
        out_path,
    )
    return report
