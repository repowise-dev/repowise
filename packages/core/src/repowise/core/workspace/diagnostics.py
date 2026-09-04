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

from repowise.core.workspace.code_api import CODE_CONTRACT_TYPE
from repowise.core.workspace.contracts import (
    Contract,
    ContractLink,
    normalize_contract_id,
)
from repowise.core.workspace.extractors.from_index import EXTRACTION_LAYER_KEY, LAYER_REGEX
from repowise.core.workspace.signature_schema import SCHEMA_SOURCE

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
    #: The call targets a literal third-party host (Stripe, Formspree, …) that is
    #: not a workspace service, so it is intentionally excluded from matching.
    EXTERNAL_HOST = "external_host"


#: The closed set, for the wire copies that have to agree with it.
UNMATCHED_REASON_VALUES = (
    UnmatchedReason.NO_PROVIDER,
    UnmatchedReason.INTERNAL_ONLY,
    UnmatchedReason.UNLINKED,
    UnmatchedReason.EXTERNAL_HOST,
)


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
    #: Contracts by the tier that produced them — ``index`` (the parsed symbol
    #: table) or ``regex`` (a text dialect). The regex share is where recall is
    #: least certain, so it is worth seeing per repo.
    providers_by_layer: dict[str, int] = field(default_factory=dict)
    consumers_by_layer: dict[str, int] = field(default_factory=dict)
    #: Calls that reached a confirmed HTTP wrapper but whose path could not be
    #: resolved statically. Real endpoint calls, located and then not named.
    http_consumers_unresolved: int = 0

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
class SymbolIdentity:
    """How many of one role's contracts bound to a symbol id, and out of what.

    Two denominators because they answer different questions. *total* is the
    honest workspace-wide share. *bound_ratio_indexed* excludes the files with
    no parsed symbols at all — ``.sql``, ``.proto``, anything in a repo without
    an index — which is what says whether the binding rule itself is working:
    no rule can reach a file the parser never saw.
    """

    total: int = 0
    bound: int = 0
    unindexed_file: int = 0

    def _ratio(self, denominator: int) -> float | None:
        return self.bound / denominator if denominator > 0 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "bound": self.bound,
            "unindexed_file": self.unindexed_file,
            "bound_ratio": self._ratio(self.total),
            "bound_ratio_indexed": self._ratio(self.total - self.unindexed_file),
        }


@dataclass
class SchemaCoverage:
    """How many providers recovered a request schema, and out of what.

    Two denominators, for the same reason :class:`SymbolIdentity` carries two.
    *total* is the workspace-wide share. *recovered_ratio_eligible* excludes the
    providers no mapper could reach whatever it did — one bound to nothing, to a
    route-registration site, to a symbol with no parameter list, or to a language
    with no parameter grammar — and is what says whether the mapper is working.
    """

    total: int = 0
    bound: int = 0
    recovered: int = 0
    shared_symbol: int = 0
    unsupported_language: int = 0
    non_callable: int = 0

    @property
    def eligible(self) -> int:
        return (
            self.bound - self.shared_symbol - self.unsupported_language - self.non_callable
        )

    def _ratio(self, denominator: int) -> float | None:
        return self.recovered / denominator if denominator > 0 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "bound": self.bound,
            "recovered": self.recovered,
            "shared_symbol": self.shared_symbol,
            "unsupported_language": self.unsupported_language,
            "non_callable": self.non_callable,
            "eligible": self.eligible,
            "recovered_ratio": self._ratio(self.total),
            "recovered_ratio_eligible": self._ratio(self.eligible),
        }


@dataclass
class CodeApiCoverage:
    """How much of the workspace's package surface became a ``code`` contract.

    Two denominators, as :class:`SchemaCoverage` carries two. *manifests* counts
    every manifest seen, so *published* against it says how much of the
    workspace declares a package at all; *linked_ratio* is over the providers
    actually emitted and says whether the consumer join is working.
    """

    manifests: int = 0
    published: int = 0
    unsupported_ecosystem: int = 0
    providers: int = 0
    consumers: int = 0
    linked_providers: int = 0

    def _ratio(self, numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator > 0 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifests": self.manifests,
            "published": self.published,
            "unsupported_ecosystem": self.unsupported_ecosystem,
            "providers": self.providers,
            "consumers": self.consumers,
            "linked_providers": self.linked_providers,
            "published_ratio": self._ratio(self.published, self.manifests),
            "linked_ratio": self._ratio(self.linked_providers, self.providers),
        }


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
    #: Workspace-wide rollups of the per-repo layer split.
    providers_by_layer: dict[str, int] = field(default_factory=dict)
    consumers_by_layer: dict[str, int] = field(default_factory=dict)
    #: HTTP client calls located but not resolvable to an endpoint, workspace
    #: wide. This is the only miss count extraction can actually observe.
    http_consumers_unresolved: int = 0
    #: Symbol-id binding coverage, keyed by role. Reported, never asserted: a
    #: contract with no symbol id still matches, it just cannot be traversed.
    symbol_identity: dict[str, SymbolIdentity] = field(default_factory=dict)
    #: Request-schema recovery over providers. Reported, never asserted: a
    #: provider without a schema keeps matching, it just cannot be field-diffed.
    schema_coverage: SchemaCoverage = field(default_factory=SchemaCoverage)
    #: Published-package surface coverage. Reported, never asserted.
    code_api: CodeApiCoverage = field(default_factory=CodeApiCoverage)

    @property
    def http_consumer_coverage(self) -> float | None:
        """Share of located HTTP client calls that became a contract.

        ``None`` when nothing was located, because 0/0 is not 100%. This is a
        genuine ratio over calls extraction *saw*: it says nothing about calls
        no dialect recognised, and must not be presented as total recall.
        """
        http_consumers = sum(
            r.consumers_by_type.get("http", 0) for r in self.repo_breakdown
        )
        denominator = http_consumers + self.http_consumers_unresolved
        if denominator == 0:
            return None
        return http_consumers / denominator

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
            "providers_by_layer": self.providers_by_layer,
            "consumers_by_layer": self.consumers_by_layer,
            "http_consumers_unresolved": self.http_consumers_unresolved,
            "http_consumer_coverage": self.http_consumer_coverage,
            "symbol_identity": {r: v.to_dict() for r, v in sorted(self.symbol_identity.items())},
            "schema_coverage": self.schema_coverage.to_dict(),
            "code_api": self.code_api.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractionDiagnostics:
        schema = data.get("schema_coverage") or {}
        code = data.get("code_api") or {}
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
                    providers_by_layer=r.get("providers_by_layer", {}),
                    consumers_by_layer=r.get("consumers_by_layer", {}),
                    http_consumers_unresolved=r.get("http_consumers_unresolved", 0),
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
            providers_by_layer=data.get("providers_by_layer", {}),
            consumers_by_layer=data.get("consumers_by_layer", {}),
            http_consumers_unresolved=data.get("http_consumers_unresolved", 0),
            symbol_identity={
                role: SymbolIdentity(
                    total=v.get("total", 0),
                    bound=v.get("bound", 0),
                    unindexed_file=v.get("unindexed_file", 0),
                )
                for role, v in (data.get("symbol_identity") or {}).items()
            },
            schema_coverage=SchemaCoverage(
                total=schema.get("total", 0),
                bound=schema.get("bound", 0),
                recovered=schema.get("recovered", 0),
                shared_symbol=schema.get("shared_symbol", 0),
                unsupported_language=schema.get("unsupported_language", 0),
                non_callable=schema.get("non_callable", 0),
            ),
            code_api=CodeApiCoverage(
                manifests=code.get("manifests", 0),
                published=code.get("published", 0),
                unsupported_ecosystem=code.get("unsupported_ecosystem", 0),
                providers=code.get("providers", 0),
                consumers=code.get("consumers", 0),
                linked_providers=code.get("linked_providers", 0),
            ),
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
    if consumer.meta.get("external"):
        return UnmatchedReason.EXTERNAL_HOST
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
    extraction_stats: dict[str, dict[str, int]] | None = None,
) -> ExtractionDiagnostics:
    """Compute extraction diagnostics from contracts and matched links.

    Pure and O(contracts + links). The reported orphan/unmatched lists let a
    workspace owner see exactly which endpoints failed to connect and why,
    rather than just a bare link total.

    *extraction_stats* is :attr:`ContractStore.extraction_stats` — the counters
    the extractors recorded for work they located but could not turn into a
    contract. Omitting it costs the coverage figures, not the rest.
    """
    stats_by_repo = extraction_stats or {}
    providers = [c for c in contracts if c.role == "provider"]
    consumers = [c for c in contracts if c.role == "consumer"]

    # Per-repo breakdown ----------------------------------------------------
    repos = sorted({c.repo for c in contracts})
    breakdown: list[RepoDiagnostics] = []
    prov_by_layer_all: dict[str, int] = defaultdict(int)
    cons_by_layer_all: dict[str, int] = defaultdict(int)
    for repo in repos:
        prov_by_type: dict[str, int] = defaultdict(int)
        cons_by_type: dict[str, int] = defaultdict(int)
        prov_by_layer: dict[str, int] = defaultdict(int)
        cons_by_layer: dict[str, int] = defaultdict(int)
        for c in contracts:
            if c.repo != repo:
                continue
            layer = str(c.meta.get(EXTRACTION_LAYER_KEY, LAYER_REGEX))
            if c.role == "provider":
                prov_by_type[c.contract_type] += 1
                prov_by_layer[layer] += 1
                prov_by_layer_all[layer] += 1
            elif c.role == "consumer":
                cons_by_type[c.contract_type] += 1
                cons_by_layer[layer] += 1
                cons_by_layer_all[layer] += 1
        breakdown.append(
            RepoDiagnostics(
                repo=repo,
                providers_by_type=dict(sorted(prov_by_type.items())),
                consumers_by_type=dict(sorted(cons_by_type.items())),
                provider_count=sum(prov_by_type.values()),
                consumer_count=sum(cons_by_type.values()),
                providers_by_layer=dict(sorted(prov_by_layer.items())),
                consumers_by_layer=dict(sorted(cons_by_layer.items())),
                http_consumers_unresolved=stats_by_repo.get(repo, {}).get(
                    "http_consumer_unresolved", 0
                ),
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

    identity = {
        role: SymbolIdentity(
            total=len(rows),
            bound=sum(1 for c in rows if c.symbol_id is not None),
            unindexed_file=sum(
                s.get(f"identity_unindexed_{role}", 0) for s in stats_by_repo.values()
            ),
        )
        for role, rows in (("provider", providers), ("consumer", consumers))
    }

    schema = SchemaCoverage(
        total=len(providers),
        bound=sum(1 for c in providers if c.symbol_id is not None),
        recovered=sum(
            1 for c in providers if c.schema is not None and c.schema.source == SCHEMA_SOURCE
        ),
        shared_symbol=sum(
            s.get("schema_shared_symbol_provider", 0) for s in stats_by_repo.values()
        ),
        unsupported_language=sum(
            s.get("schema_unsupported_lang_provider", 0) for s in stats_by_repo.values()
        ),
        non_callable=sum(
            s.get("schema_non_callable_provider", 0) for s in stats_by_repo.values()
        ),
    )

    code_providers = [c for c in providers if c.contract_type == CODE_CONTRACT_TYPE]
    published = sum(s.get("code_published_packages", 0) for s in stats_by_repo.values())
    unsupported = sum(s.get("code_unsupported_ecosystem", 0) for s in stats_by_repo.values())
    code_api = CodeApiCoverage(
        manifests=published
        + unsupported
        + sum(s.get("code_unpublished_manifest", 0) for s in stats_by_repo.values()),
        published=published,
        unsupported_ecosystem=unsupported,
        providers=len(code_providers),
        consumers=sum(1 for c in consumers if c.contract_type == CODE_CONTRACT_TYPE),
        linked_providers=sum(
            1
            for p in code_providers
            if _contract_key(p.repo, p.file_path, p.contract_id) in matched_providers
        ),
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
        providers_by_layer=dict(sorted(prov_by_layer_all.items())),
        consumers_by_layer=dict(sorted(cons_by_layer_all.items())),
        # Summed from the stats, not from the per-repo breakdown: a repo whose
        # every call went unresolved has no contracts and so no breakdown row,
        # and it is exactly the repo whose misses matter most.
        http_consumers_unresolved=sum(
            s.get("http_consumer_unresolved", 0) for s in stats_by_repo.values()
        ),
        symbol_identity=identity,
        schema_coverage=schema,
        code_api=code_api,
    )
