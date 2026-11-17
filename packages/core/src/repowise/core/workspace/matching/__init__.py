"""Provider-to-consumer matching across repos.

Every contract type shares one exact pass over normalized ids (with the HTTP and
gRPC wildcards). A type whose two ends can name one thing differently registers
a :class:`TypeMatcher` in :data:`MATCHERS`: consumers the exact pass must leave
alone, a routing check the exact pass must honour, and the extra passes that
run after it. Adding a transport's rules is one module and one registry entry.

Same-repo same-service pairs, and consumers resolved to a third-party host
(``meta['external']``), are filtered from every pass.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from repowise.core.workspace.contracts import normalize_contract_id
from repowise.core.workspace.origins import LinkStrategy

from . import data, http, topic
from .common import MatchState, find_matching_keys, internal, prefer_target_repo, spans_repos
from .http import annotate_consumer_targets

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract, ContractLink
    from repowise.core.workspace.service_names import GatewayRoute


@dataclass(frozen=True)
class TypeMatcher:
    """What one contract type adds to the shared exact pass.

    ``keys`` replaces :func:`.common.find_matching_keys` for the type: the
    provider index keys a consumer reaches (a pattern subscription reaches
    every name its pattern matches).
    """

    deferred: Callable[[Contract], bool] | None = None
    accepts: Callable[[Contract, Contract], bool] | None = None
    passes: tuple[Callable[[MatchState], None], ...] = ()
    keys: Callable[[Contract, dict[str, list[Contract]]], list[str]] | None = None
    #: A consumer of the type reaches one provider: when its host or base does
    #: not place it and two or more repos serve its id, it links to none of
    #: them (:meth:`.common.MatchState.refuse`). Off for broadcasts and shared
    #: tables, where every repo on the id is a true end.
    one_provider_repo: bool = False


MATCHERS: dict[str, TypeMatcher] = {
    "http": TypeMatcher(
        deferred=http.is_deferred,
        accepts=http.accepts,
        one_provider_repo=True,
        passes=(
            http.gateway_pass,
            http.base_resolved_pass,
            http.candidate_pass,
            http.segment_pass,
            http.service_pass,
        ),
    ),
    "grpc": TypeMatcher(one_provider_repo=True),
    "topic": TypeMatcher(
        deferred=topic.is_binding,
        accepts=topic.accepts,
        passes=(topic.binding_pass,),
        keys=topic.matching_keys,
    ),
    "data": TypeMatcher(accepts=data.accepts, passes=(data.shared_table_pass,)),
}

_NO_RULES = TypeMatcher()


def _servers(state: MatchState, consumer: Contract, rules: TypeMatcher) -> list[Contract]:
    """Providers outside *consumer*'s own service that may serve it on its id.

    A provider in its own service marks it served instead.
    """
    if rules.keys is not None:
        keys = rules.keys(consumer, state.provider_index)
    else:
        keys = find_matching_keys(consumer.contract_id, state.provider_index)
    providers = [p for k in keys for p in state.provider_index[k]]
    servers: list[Contract] = []
    for provider in prefer_target_repo(providers, consumer):
        if internal(provider, consumer):
            state.mark_served(consumer)
        elif rules.accepts is None or rules.accepts(provider, consumer):
            servers.append(provider)
    return servers


def _exact_pass(state: MatchState) -> None:
    for consumer in state.consumers:
        rules = MATCHERS.get(consumer.contract_type, _NO_RULES)
        if rules.deferred is not None and rules.deferred(consumer):
            continue
        servers = _servers(state, consumer, rules)
        if rules.one_provider_repo and spans_repos(servers):
            state.refuse(consumer)
            continue
        own = normalize_contract_id(consumer.contract_id)
        # A host or base token that names the provider's repo is what joined them.
        target = consumer.meta.get("target_repo") if consumer.meta.get("target_via") else None
        for provider in servers:
            # A key the type's own rule reached under another name (a pattern
            # subscription, the only `keys` rule today) links under the
            # provider's name. A new `keys` rule with another meaning names its
            # own strategy here.
            aliased = rules.keys is not None and normalize_contract_id(provider.contract_id) != own
            if aliased:
                strategy = LinkStrategy.PATTERN_MATCHED
            elif provider.repo == target:
                strategy = LinkStrategy.SERVICE_MAPPED
            else:
                strategy = LinkStrategy.EXACT_ID
            state.add(consumer, provider, strategy, via_alias=aliased)


def run_matching(contracts: list[Contract], routes: Iterable[GatewayRoute] = ()) -> MatchState:
    """Match providers to consumers across repos; the state holds links and service links.

    The exact pass runs first for every type, then each registered type's own
    passes in registry order. Target resolution for HTTP consumers is read from
    ``meta`` (see :func:`annotate_consumer_targets`). Each gateway route in
    *routes* adds its gateway-to-service edge to the service links.
    """
    state = MatchState.build(contracts)
    _exact_pass(state)
    for rules in MATCHERS.values():
        for run in rules.passes:
            run(state)
    state.service_links.extend(http.gateway_route_links(routes))
    return state


def match_contracts(contracts: list[Contract]) -> list[ContractLink]:
    """The route-level links :func:`run_matching` makes."""
    return run_matching(contracts).links


__all__ = [
    "MATCHERS",
    "TypeMatcher",
    "annotate_consumer_targets",
    "match_contracts",
    "run_matching",
]
