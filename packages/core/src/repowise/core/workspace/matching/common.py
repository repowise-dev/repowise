"""The state every matching pass shares, and the helpers they all use."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from repowise.core.workspace.contracts import (
    Contract,
    ContractLink,
    normalize_contract_id,
    same_service,
)
from repowise.core.workspace.origins import LinkStrategy, link_confidence, match_type_for

#: ``meta`` key on a consumer two or more repos could serve, none of them
#: placed by its host or base: it links to none of them (see :meth:`MatchState.refuse`).
AMBIGUOUS_PROVIDER = "ambiguous_provider"


def internal(provider: Contract, consumer: Contract) -> bool:
    """One program calling itself, which no pass links."""
    return same_service(provider.repo, provider.service, consumer.repo, consumer.service)


def spans_repos(providers: list[Contract]) -> bool:
    """Whether *providers* sit in two or more repos, so the route alone cannot pick one."""
    return len({p.repo for p in providers}) > 1


@dataclass
class MatchState:
    """The provider index, the consumers to place, and the links placed so far.

    Built once per :func:`..match_contracts` call and threaded through every
    pass, so a later pass sees which consumers an earlier one already linked.
    """

    provider_index: dict[str, list[Contract]]
    consumers: list[Contract]
    links: list[ContractLink] = field(default_factory=list)
    seen: set[tuple[str, ...]] = field(default_factory=set)
    matched: set[int] = field(default_factory=set)
    #: Service-level edges for consumers whose target repo is known but whose
    #: route is not (:func:`.http.service_pass`); never mixed into ``links``.
    service_links: list[ContractLink] = field(default_factory=list)

    @classmethod
    def build(cls, contracts: list[Contract]) -> MatchState:
        """Index providers by normalized id; consumers resolved to a third party are dropped.

        A vendored ``.proto`` copy (see :func:`shared_idl`) is not indexed.
        """
        provider_index: dict[str, list[Contract]] = defaultdict(list)
        consumers: list[Contract] = []
        vendored = shared_idl(contracts)
        for c in contracts:
            if c.role == "provider":
                if id(c) not in vendored:
                    provider_index[normalize_contract_id(c.contract_id)].append(c)
            else:
                # Stamped by this run's passes, never carried from a stored row.
                c.meta.pop(AMBIGUOUS_PROVIDER, None)
                if not c.meta.get("external"):
                    consumers.append(c)
        return cls(provider_index, consumers)

    def unmatched(self, contract_type: str) -> list[Contract]:
        """Consumers of *contract_type* no pass has linked yet."""
        return [
            c
            for c in self.consumers
            if c.contract_type == contract_type and id(c) not in self.matched
        ]

    def mark_served(self, consumer: Contract) -> None:
        """Mark *consumer* as served internally so later matching passes skip it."""
        self.matched.add(id(consumer))

    def refuse(self, consumer: Contract) -> None:
        """Link *consumer* to nothing: its route is served by two or more repos and
        neither its host nor its base says which one it calls. Later passes skip
        it, and diagnostics count it as an ambiguous provider, also when its own
        service declares the route too."""
        consumer.meta[AMBIGUOUS_PROVIDER] = True
        self.matched.add(id(consumer))

    def add(
        self,
        consumer: Contract,
        provider: Contract,
        strategy: LinkStrategy,
        *,
        via: Contract | None = None,
        via_alias: bool = False,
    ) -> bool:
        """Record one link made by *strategy* unless an identical one exists; True when added.

        Its confidence is the weakest of the two ends (and *via*, the binding a
        queue was reached through) scaled by the strategy's, and its
        ``match_type`` is the strategy's (see :mod:`..origins`).

        A link's ``contract_id`` is the provider's id, and ``consumer_contract_id``
        the consumer's whenever it is spelled differently (another case, a
        wildcard method, a prefix the candidate pass collapsed), so each side
        finds its links under its own id. *via_alias* marks a consumer that
        reached the provider under another name (a queue bound to an exchange).
        One queue bound to two exchanges is two links, so an aliased key names
        the provider's id too.
        """
        dedup_key = (
            normalize_contract_id(consumer.contract_id),
            consumer.repo,
            consumer.file_path,
            provider.repo,
            provider.file_path,
            normalize_contract_id(provider.contract_id) if via_alias else "",
        )
        if dedup_key in self.seen:
            # The same call written twice in one file: already linked, so later
            # passes must not place it again.
            self.matched.add(id(consumer))
            return False
        self.seen.add(dedup_key)
        self.links.append(
            ContractLink(
                contract_id=provider.contract_id,
                contract_type=consumer.contract_type,
                match_type=match_type_for(strategy),
                confidence=link_confidence(
                    strategy,
                    provider.confidence,
                    consumer.confidence,
                    *((via.confidence,) if via is not None else ()),
                ),
                provider_repo=provider.repo,
                provider_file=provider.file_path,
                provider_symbol=provider.symbol_name,
                provider_service=provider.service,
                consumer_repo=consumer.repo,
                consumer_file=consumer.file_path,
                consumer_symbol=consumer.symbol_name,
                provider_symbol_id=provider.symbol_id,
                consumer_symbol_id=consumer.symbol_id,
                consumer_service=consumer.service,
                consumer_contract_id=(
                    consumer.contract_id if consumer.contract_id != provider.contract_id else None
                ),
                strategy=strategy,
            )
        )
        self.matched.add(id(consumer))
        return True


def find_matching_keys(consumer_id: str, provider_index: dict[str, list[Contract]]) -> list[str]:
    """Provider index keys that match *consumer_id*, wildcards included."""
    normalized = normalize_contract_id(consumer_id)

    if normalized in provider_index:
        return [normalized]

    # HTTP wildcard: consumer http::*::/path matches any method on that path
    if normalized.startswith("http::*::"):
        path_suffix = normalized[len("http::*::") :]
        return [
            k for k in provider_index if k.startswith("http::") and k.endswith(f"::{path_suffix}")
        ]

    # HTTP: check for wildcard providers (http::*::/path from Go HandleFunc)
    if normalized.startswith("http::"):
        parts = normalized.split("::", 2)
        if len(parts) == 3:
            wildcard_key = f"http::*::{parts[2]}"
            if wildcard_key in provider_index:
                return [wildcard_key]

    if normalized.startswith("grpc::"):
        return _grpc_keys(normalized, provider_index)

    # Any other wildcard: kind::name/* matches kind::name/Member
    if normalized.endswith("/*"):
        prefix = normalized[:-1]  # "grpc::service/"
        return [k for k in provider_index if k.startswith(prefix)]

    return []


def _same_grpc_service(provider: str, consumer: str) -> bool:
    """Whether two lower-cased service names are one service.

    A generated stub often names its service without the proto package
    (``CurrencyService``) while the ``.proto`` names it with one
    (``hipstershop.CurrencyService``): a bare name meets a qualified one on its
    last segment. Two different packages are two services.
    """
    if provider == consumer:
        return True
    if "." in provider and "." in consumer:
        return False
    return provider.rpartition(".")[2] == consumer.rpartition(".")[2]


def _grpc_keys(normalized: str, provider_index: dict[str, list[Contract]]) -> list[str]:
    """gRPC index keys *normalized* reaches: the same method, or any for ``/*``."""
    service, _, method = normalized[len("grpc::") :].rpartition("/")
    if not service:
        return []
    keys = []
    for k in provider_index:
        if not k.startswith("grpc::"):
            continue
        k_service, _, k_method = k[len("grpc::") :].rpartition("/")
        if (method == "*" or k_method == method) and _same_grpc_service(k_service, service):
            keys.append(k)
    return keys


def _grpc_service(contract: Contract) -> str:
    """The lower-cased service segment of a gRPC contract id."""
    return normalize_contract_id(contract.contract_id)[len("grpc::") :].rpartition("/")[0]


def shared_idl(contracts: list[Contract]) -> set[int]:
    """The ``id()`` of each ``.proto`` provider row that is a vendored copy, not the server.

    Services keep a copy of the protos they call (often the whole estate's
    file), so a service declared in 2+ repos is served by whichever of them
    registers it in code. With no code server anywhere, the one copy outside
    the repos that call the service stands; when that is not exactly one,
    no copy is trusted. A service declared in one repo is untouched.
    """
    copies: dict[str, list[Contract]] = defaultdict(list)
    servers: dict[str, set[str]] = defaultdict(set)
    callers: dict[str, set[str]] = defaultdict(set)
    for c in contracts:
        if c.contract_type != "grpc":
            continue
        service = _grpc_service(c)
        bare = service.rpartition(".")[2]
        if c.role == "provider" and c.meta.get("source") == "proto":
            copies[service].append(c)
        elif c.role == "provider":
            servers[bare].add(c.repo)
        elif c.role == "consumer":
            callers[bare].add(c.repo)
    vendored: set[int] = set()
    for service, rows in copies.items():
        repos = {c.repo for c in rows}
        if len(repos) < 2:
            continue
        bare = service.rpartition(".")[2]
        if servers.get(bare):
            keep = repos & servers[bare]
        else:
            keep = repos - callers.get(bare, set())
            if len(keep) != 1:
                keep = set()
        vendored.update(id(c) for c in rows if c.repo not in keep)
    return vendored


def prefer_target_repo(providers: list[Contract], consumer: Contract) -> list[Contract]:
    """Narrow *providers* to the consumer's resolved ``target_repo`` when set.

    Falls back to the full list if the target declares no matching provider, so
    a stale/typo'd ``service_bases`` entry never silently drops a real link. A
    target the service-name map placed (it carries ``target_evidence``) does
    not fall back: the file named that repo, so a same-path route elsewhere is
    not the call's destination.
    """
    target = consumer.meta.get("target_repo")
    if not target:
        return providers
    preferred = [p for p in providers if p.repo == target]
    if preferred or consumer.meta.get("target_evidence"):
        # A target the workspace's own config placed does not fall back to
        # another repo that happens to serve the path; the service pass draws
        # the dependency instead.
        return preferred
    return providers


__all__ = [
    "AMBIGUOUS_PROVIDER",
    "MatchState",
    "find_matching_keys",
    "internal",
    "prefer_target_repo",
    "spans_repos",
]
