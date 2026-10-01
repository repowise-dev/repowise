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

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from repowise.core.workspace.contracts import normalize_contract_id

from . import data, http, topic
from .common import MatchState, find_matching_keys, internal, prefer_target_repo
from .http import annotate_consumer_targets

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract, ContractLink


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


MATCHERS: dict[str, TypeMatcher] = {
    "http": TypeMatcher(
        deferred=http.is_deferred,
        passes=(http.base_resolved_pass, http.candidate_pass),
    ),
    "topic": TypeMatcher(
        deferred=topic.is_binding,
        accepts=topic.accepts,
        passes=(topic.binding_pass,),
        keys=topic.matching_keys,
    ),
    "data": TypeMatcher(passes=(data.shared_table_pass,)),
}

_NO_RULES = TypeMatcher()


def _exact_pass(state: MatchState) -> None:
    for consumer in state.consumers:
        rules = MATCHERS.get(consumer.contract_type, _NO_RULES)
        if rules.deferred is not None and rules.deferred(consumer):
            continue
        own = normalize_contract_id(consumer.contract_id)
        if rules.keys is not None:
            keys = rules.keys(consumer, state.provider_index)
        else:
            keys = find_matching_keys(consumer.contract_id, state.provider_index)
        providers = [p for k in keys for p in state.provider_index[k]]
        for provider in prefer_target_repo(providers, consumer):
            if internal(provider, consumer):
                continue
            if rules.accepts is not None and not rules.accepts(provider, consumer):
                continue
            state.add(
                consumer,
                provider,
                "exact",
                min(provider.confidence, consumer.confidence),
                # A key the type's own rule reached under another name (a
                # pattern subscription) links under the provider's name.
                via_alias=rules.keys is not None
                and normalize_contract_id(provider.contract_id) != own,
            )


def match_contracts(contracts: list[Contract]) -> list[ContractLink]:
    """Match providers to consumers across repos.

    The exact pass runs first for every type, then each registered type's own
    passes in registry order. Target resolution for HTTP consumers is read from
    ``meta`` (see :func:`annotate_consumer_targets`).
    """
    state = MatchState.build(contracts)
    _exact_pass(state)
    for rules in MATCHERS.values():
        for run in rules.passes:
            run(state)
    return state.links


__all__ = [
    "MATCHERS",
    "TypeMatcher",
    "annotate_consumer_targets",
    "match_contracts",
]
