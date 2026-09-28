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


def internal(provider: Contract, consumer: Contract) -> bool:
    """One program calling itself, which no pass links."""
    return same_service(provider.repo, provider.service, consumer.repo, consumer.service)


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

    @classmethod
    def build(cls, contracts: list[Contract]) -> MatchState:
        """Index providers by normalized id; consumers resolved to a third party are dropped."""
        provider_index: dict[str, list[Contract]] = defaultdict(list)
        consumers: list[Contract] = []
        for c in contracts:
            if c.role == "provider":
                provider_index[normalize_contract_id(c.contract_id)].append(c)
            elif not c.meta.get("external"):
                consumers.append(c)
        return cls(provider_index, consumers)

    def unmatched(self, contract_type: str) -> list[Contract]:
        """Consumers of *contract_type* no pass has linked yet."""
        return [
            c
            for c in self.consumers
            if c.contract_type == contract_type and id(c) not in self.matched
        ]

    def add(
        self,
        consumer: Contract,
        provider: Contract,
        match_type: str,
        confidence: float,
        *,
        via_alias: bool = False,
    ) -> bool:
        """Record one link unless an identical one exists; True when it was added.

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
            return False
        self.seen.add(dedup_key)
        self.links.append(
            ContractLink(
                contract_id=provider.contract_id,
                contract_type=consumer.contract_type,
                match_type=match_type,
                confidence=confidence,
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

    # gRPC wildcard: grpc::service/* matches grpc::service/Method
    if normalized.endswith("/*"):
        prefix = normalized[:-1]  # "grpc::service/"
        return [k for k in provider_index if k.startswith(prefix)]

    return []


def prefer_target_repo(providers: list[Contract], consumer: Contract) -> list[Contract]:
    """Narrow *providers* to the consumer's resolved ``target_repo`` when set.

    Falls back to the full list if the target declares no matching provider, so
    a stale/typo'd ``service_bases`` entry never silently drops a real link.
    """
    target = consumer.meta.get("target_repo")
    if not target:
        return providers
    preferred = [p for p in providers if p.repo == target]
    return preferred or providers


__all__ = [
    "MatchState",
    "find_matching_keys",
    "internal",
    "prefer_target_repo",
]
