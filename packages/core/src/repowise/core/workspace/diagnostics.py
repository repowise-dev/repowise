"""Extraction diagnostics — explain the cross-repo contract link count.

Derived purely from the contracts + matched links already produced by
:mod:`repowise.core.workspace.contracts`. Answers the question a workspace
owner actually asks: *"why are there so few links?"* — by reporting, per repo
and contract type, how many providers and consumers were found, which consumers
went unmatched and why, and which providers have no consumer at all.

This module performs no I/O and has no DB dependency. It consumes the same
:class:`Contract` / :class:`ContractLink` objects the matcher emits, so it is
cheap to compute alongside contract extraction and trivial to unit test.

The serialized :class:`ExtractionDiagnostics` is embedded in the system-graph
artifact (see :mod:`repowise.core.workspace.system_graph`) and surfaced through
``GET /api/workspace/diagnostics`` and ``repowise workspace diagnostics``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from repowise.core.workspace.contracts import (
    Contract,
    ContractLink,
    normalize_contract_id,
)

# ---------------------------------------------------------------------------
# Constants (single source of truth)
# ---------------------------------------------------------------------------

#: Links at or below this confidence are reported as "weak" — a candidate match
#: or a low-confidence extraction a reviewer may want to eyeball. Kept here so
#: every consumer (core, server, CLI) reads one cutoff.
WEAK_LINK_CONFIDENCE_THRESHOLD = 0.4


class UnmatchedReason:
    """Why a consumer contract never formed a cross-repo link."""

    #: No provider anywhere declares a route/service/topic with this id.
    NO_PROVIDER = "no_provider"
    #: The only matching provider(s) live in the same repo + service, so the
    #: call is intra-service and intentionally not surfaced as a cross-repo link.
    INTERNAL_ONLY = "internal_only"
    #: A cross-service provider with a matching id exists, but no link was
    #: formed (e.g. an HTTP path that only the candidate pass could bridge and
    #: did not). Rare; flags a potential matcher gap worth inspecting.
    UNLINKED = "unlinked"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class RepoDiagnostics:
    """Per-repo provider/consumer breakdown by contract type."""

    repo: str
    providers_by_type: dict[str, int] = field(default_factory=dict)
    consumers_by_type: dict[str, int] = field(default_factory=dict)
    provider_count: int = 0
    consumer_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UnmatchedConsumer:
    """A consumer contract that did not link to any provider, with the reason."""

    repo: str
    file_path: str
    contract_id: str
    contract_type: str
    reason: str  # one of UnmatchedReason

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OrphanProvider:
    """A provider contract that no consumer (in any repo) calls."""

    repo: str
    file_path: str
    contract_id: str
    contract_type: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractionDiagnostics:
    """Aggregate explanation of contract extraction + matching coverage."""

    total_providers: int = 0
    total_consumers: int = 0
    total_links: int = 0
    weak_link_count: int = 0
    repo_breakdown: list[RepoDiagnostics] = field(default_factory=list)
    unmatched_consumers: list[UnmatchedConsumer] = field(default_factory=list)
    unmatched_by_reason: dict[str, int] = field(default_factory=dict)
    orphan_providers: list[OrphanProvider] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_providers": self.total_providers,
            "total_consumers": self.total_consumers,
            "total_links": self.total_links,
            "weak_link_count": self.weak_link_count,
            "repo_breakdown": [r.to_dict() for r in self.repo_breakdown],
            "unmatched_consumers": [u.to_dict() for u in self.unmatched_consumers],
            "unmatched_by_reason": self.unmatched_by_reason,
            "orphan_providers": [o.to_dict() for o in self.orphan_providers],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractionDiagnostics:
        return cls(
            total_providers=data.get("total_providers", 0),
            total_consumers=data.get("total_consumers", 0),
            total_links=data.get("total_links", 0),
            weak_link_count=data.get("weak_link_count", 0),
            repo_breakdown=[
                RepoDiagnostics(
                    repo=r.get("repo", ""),
                    providers_by_type=r.get("providers_by_type", {}),
                    consumers_by_type=r.get("consumers_by_type", {}),
                    provider_count=r.get("provider_count", 0),
                    consumer_count=r.get("consumer_count", 0),
                )
                for r in data.get("repo_breakdown", [])
            ],
            unmatched_consumers=[
                UnmatchedConsumer(
                    repo=u.get("repo", ""),
                    file_path=u.get("file_path", ""),
                    contract_id=u.get("contract_id", ""),
                    contract_type=u.get("contract_type", ""),
                    reason=u.get("reason", UnmatchedReason.NO_PROVIDER),
                )
                for u in data.get("unmatched_consumers", [])
            ],
            unmatched_by_reason=data.get("unmatched_by_reason", {}),
            orphan_providers=[
                OrphanProvider(
                    repo=o.get("repo", ""),
                    file_path=o.get("file_path", ""),
                    contract_id=o.get("contract_id", ""),
                    contract_type=o.get("contract_type", ""),
                )
                for o in data.get("orphan_providers", [])
            ],
        )


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _contract_key(repo: str, file_path: str, contract_id: str) -> tuple[str, str, str]:
    """Stable identity for a contract endpoint, normalized for matching."""
    return (repo, file_path, normalize_contract_id(contract_id))


def _classify_unmatched(
    consumer: Contract,
    providers_by_norm_id: dict[str, list[Contract]],
) -> str:
    """Return the :class:`UnmatchedReason` for an unmatched *consumer*."""
    candidates = providers_by_norm_id.get(normalize_contract_id(consumer.contract_id))
    if not candidates:
        return UnmatchedReason.NO_PROVIDER
    # A matching provider exists. If every one shares this consumer's repo AND
    # service boundary, the call is intra-service and was filtered on purpose.
    all_internal = all(
        p.repo == consumer.repo and p.service == consumer.service for p in candidates
    )
    return UnmatchedReason.INTERNAL_ONLY if all_internal else UnmatchedReason.UNLINKED


def build_diagnostics(
    contracts: list[Contract],
    links: list[ContractLink],
) -> ExtractionDiagnostics:
    """Compute extraction diagnostics from contracts and matched links.

    Pure and O(contracts + links). The reported orphan/unmatched lists let a
    workspace owner see exactly which endpoints failed to connect and why,
    rather than just a bare link total.
    """
    providers = [c for c in contracts if c.role == "provider"]
    consumers = [c for c in contracts if c.role == "consumer"]

    # Per-repo breakdown ----------------------------------------------------
    repos = sorted({c.repo for c in contracts})
    breakdown: list[RepoDiagnostics] = []
    for repo in repos:
        prov_by_type: dict[str, int] = defaultdict(int)
        cons_by_type: dict[str, int] = defaultdict(int)
        for c in contracts:
            if c.repo != repo:
                continue
            if c.role == "provider":
                prov_by_type[c.contract_type] += 1
            elif c.role == "consumer":
                cons_by_type[c.contract_type] += 1
        breakdown.append(
            RepoDiagnostics(
                repo=repo,
                providers_by_type=dict(sorted(prov_by_type.items())),
                consumers_by_type=dict(sorted(cons_by_type.items())),
                provider_count=sum(prov_by_type.values()),
                consumer_count=sum(cons_by_type.values()),
            )
        )

    # Matched endpoint sets (normalized) ------------------------------------
    matched_consumers: set[tuple[str, str, str]] = set()
    matched_providers: set[tuple[str, str, str]] = set()
    weak_links = 0
    for lk in links:
        matched_consumers.add(_contract_key(lk.consumer_repo, lk.consumer_file, lk.contract_id))
        matched_providers.add(_contract_key(lk.provider_repo, lk.provider_file, lk.contract_id))
        if lk.confidence <= WEAK_LINK_CONFIDENCE_THRESHOLD:
            weak_links += 1

    providers_by_norm_id: dict[str, list[Contract]] = defaultdict(list)
    for p in providers:
        providers_by_norm_id[normalize_contract_id(p.contract_id)].append(p)

    # Unmatched consumers, grouped by reason --------------------------------
    unmatched: list[UnmatchedConsumer] = []
    by_reason: dict[str, int] = defaultdict(int)
    for c in consumers:
        key = _contract_key(c.repo, c.file_path, c.contract_id)
        if key in matched_consumers:
            continue
        reason = _classify_unmatched(c, providers_by_norm_id)
        by_reason[reason] += 1
        unmatched.append(
            UnmatchedConsumer(
                repo=c.repo,
                file_path=c.file_path,
                contract_id=c.contract_id,
                contract_type=c.contract_type,
                reason=reason,
            )
        )

    # Orphan providers — declared but never consumed ------------------------
    orphans: list[OrphanProvider] = []
    for p in providers:
        key = _contract_key(p.repo, p.file_path, p.contract_id)
        if key in matched_providers:
            continue
        orphans.append(
            OrphanProvider(
                repo=p.repo,
                file_path=p.file_path,
                contract_id=p.contract_id,
                contract_type=p.contract_type,
            )
        )

    return ExtractionDiagnostics(
        total_providers=len(providers),
        total_consumers=len(consumers),
        total_links=len(links),
        weak_link_count=weak_links,
        repo_breakdown=breakdown,
        unmatched_consumers=unmatched,
        unmatched_by_reason=dict(sorted(by_reason.items())),
        orphan_providers=orphans,
    )
