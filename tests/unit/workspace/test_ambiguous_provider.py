"""A call two or more repos could serve, with nothing naming one, links to none of them."""

from __future__ import annotations

from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.diagnostics import UnmatchedReason, build_diagnostics
from repowise.core.workspace.matching import annotate_consumer_targets, run_matching
from repowise.core.workspace.matching.common import AMBIGUOUS_PROVIDER
from repowise.core.workspace.origins import LinkStrategy


def _c(repo: str, role: str, contract_id: str, contract_type: str = "http", **meta) -> Contract:
    return Contract(
        repo=repo,
        contract_id=contract_id,
        contract_type=contract_type,
        role=role,
        file_path=f"{role}.ts",
        symbol_name="x",
        confidence=0.9,
        meta=dict(meta),
    )


def _match(contracts: list[Contract], service_bases: dict[str, str] | None = None):
    annotate_consumer_targets(contracts, service_bases)
    return run_matching(contracts).links


def test_a_route_two_repos_serve_links_to_neither_and_reads_as_ambiguous() -> None:
    contracts = [
        _c("shop-a", "provider", "http::GET::/cart"),
        _c("shop-b", "provider", "http::GET::/cart"),
        _c("web", "consumer", "http::GET::/cart"),
    ]
    links = _match(contracts)
    assert links == []
    assert contracts[2].meta[AMBIGUOUS_PROVIDER] is True
    diag = build_diagnostics(contracts, links)
    assert [u.reason for u in diag.unmatched_consumers] == [UnmatchedReason.AMBIGUOUS_PROVIDER]
    assert diag.unmatched_by_reason == {UnmatchedReason.AMBIGUOUS_PROVIDER: 1}


def test_a_host_that_names_one_repo_still_links_it() -> None:
    contracts = [
        _c("shop-a", "provider", "http::GET::/cart"),
        _c("shop-b", "provider", "http::GET::/cart"),
        _c("web", "consumer", "http::GET::/cart", host="cart-api"),
    ]
    links = _match(contracts, {"cart-api": "shop-a"})
    assert [(lk.provider_repo, lk.strategy) for lk in links] == [
        ("shop-a", LinkStrategy.SERVICE_MAPPED)
    ]
    assert AMBIGUOUS_PROVIDER not in contracts[2].meta


def test_two_providers_in_one_repo_still_link() -> None:
    other = _c("shop", "provider", "http::GET::/cart")
    other.file_path = "routes/cart.ts"
    contracts = [_c("shop", "provider", "http::GET::/cart"), other, _c("web", "consumer", "http::GET::/cart")]
    assert {lk.provider_file for lk in _match(contracts)} == {"provider.ts", "routes/cart.ts"}


def test_a_grpc_service_two_repos_serve_links_to_neither() -> None:
    contracts = [
        _c("pay-a", "provider", "grpc::shop.Payments/Charge", "grpc"),
        _c("pay-b", "provider", "grpc::shop.Payments/Charge", "grpc"),
        _c("checkout", "consumer", "grpc::shop.Payments/Charge", "grpc"),
    ]
    assert _match(contracts) == []


def test_a_topic_two_repos_publish_links_to_both() -> None:
    contracts = [
        _c("orders", "provider", "topic::order-placed", "topic"),
        _c("returns", "provider", "topic::order-placed", "topic"),
        _c("mailer", "consumer", "topic::order-placed", "topic"),
    ]
    assert {lk.provider_repo for lk in _match(contracts)} == {"orders", "returns"}


def test_a_prefix_stripped_route_two_repos_serve_links_to_neither() -> None:
    contracts = [
        _c("shop-a", "provider", "http::GET::/users"),
        _c("shop-b", "provider", "http::GET::/v1/users"),
        _c("web", "consumer", "http::GET::/api/v1/users"),
    ]
    assert _match(contracts) == []
    assert contracts[2].meta[AMBIGUOUS_PROVIDER] is True


def test_the_mark_is_recomputed_on_every_run() -> None:
    contracts = [
        _c("shop-a", "provider", "http::GET::/cart"),
        _c("shop-b", "provider", "http::GET::/cart"),
        _c("web", "consumer", "http::GET::/cart"),
    ]
    _match(contracts)
    del contracts[1]
    links = _match(contracts)
    assert [lk.provider_repo for lk in links] == ["shop-a"]
    assert AMBIGUOUS_PROVIDER not in contracts[1].meta


def test_a_mapped_target_serving_nothing_keeps_its_service_edge() -> None:
    contracts = [
        _c("shop-a", "provider", "http::GET::/cart"),
        _c("shop-b", "provider", "http::GET::/cart"),
        _c("cart", "provider", "http::GET::/other"),
        _c("web", "consumer", "http::GET::/cart", base_stripped=True, base_token="CART_URL"),
    ]
    annotate_consumer_targets(contracts)
    contracts[3].meta.update(target_repo="cart", target_via="compose", target_evidence="cart/compose.yml")
    state = run_matching(contracts)
    assert state.links == []
    assert AMBIGUOUS_PROVIDER not in contracts[3].meta
    assert [lk.provider_repo for lk in state.service_links] == ["cart"]
