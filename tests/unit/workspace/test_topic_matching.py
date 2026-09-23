"""Topic matching: routing keys and queues reached through an exchange binding."""

from __future__ import annotations

import pytest

from repowise.core.workspace.contracts import Contract, ContractLink
from repowise.core.workspace.diagnostics import build_diagnostics
from repowise.core.workspace.matching import match_contracts
from repowise.core.workspace.matching.topic import routing_matches


def _topic(repo: str, role: str, name: str, file: str, **meta) -> Contract:
    return Contract(
        repo=repo,
        contract_id=f"topic::{name}",
        contract_type="topic",
        role=role,
        file_path=file,
        symbol_name=f"call('{name}')",
        confidence=0.8,
        meta={"topic": name, "broker": "rabbitmq", **meta},
    )


def _publisher(key: str = "order.created") -> Contract:
    return _topic("orders", "provider", "orders", "pub.ts", kind="exchange", routing_key=key)


def _binding(repo: str, queue: str, pattern: str, file: str = "setup.ts") -> Contract:
    return _topic(repo, "consumer", "orders", file, kind="binding", queue=queue, routing_key=pattern)


def _queue_consumer(repo: str, queue: str, file: str = "worker.ts") -> Contract:
    return _topic(repo, "consumer", queue, file, kind="queue")


class TestRoutingMatches:
    @pytest.mark.parametrize(
        ("pattern", "key", "expected"),
        [
            ("order.created", "order.created", True),
            ("order.created", "order.deleted", False),
            ("order.*", "order.created", True),
            ("order.*", "order.created.v2", False),
            ("order.#", "order", True),
            ("order.#", "order.created.v2", True),
            ("#.v2", "order.created.v2", True),
            ("*.created", "order.created", True),
            ("#", "anything.at.all", True),
            ("", "order.created", True),
            ("order.*", "", True),
        ],
    )
    def test_amqp_topic_semantics(self, pattern: str, key: str, expected: bool) -> None:
        assert routing_matches(pattern, key) is expected


class TestBindingPass:
    def test_a_queue_bound_in_the_producer_links_its_consumer(self) -> None:
        # The producer declares the binding; the consumer only names its queue.
        contracts = [
            _publisher(),
            _binding("orders", "audit", "order.*"),
            _queue_consumer("audit-worker", "audit"),
        ]
        links = match_contracts(contracts)
        assert len(links) == 1
        link = links[0]
        assert (link.provider_repo, link.consumer_repo) == ("orders", "audit-worker")
        assert link.contract_id == "topic::orders"
        assert link.consumer_contract_id == "topic::audit"
        assert link.match_type == "exact"

    def test_a_binding_beside_its_consumer_is_wiring_not_a_second_link(self) -> None:
        contracts = [
            _publisher(),
            _binding("audit-worker", "audit", "order.*", file="worker.ts"),
            _queue_consumer("audit-worker", "audit", file="worker.ts"),
        ]
        links = match_contracts(contracts)
        assert [(lk.contract_id, lk.consumer_contract_id) for lk in links] == [
            ("topic::orders", "topic::audit")
        ]

    def test_a_binding_with_no_consumer_found_links_itself(self) -> None:
        links = match_contracts([_publisher(), _binding("audit-worker", "audit", "order.*")])
        assert len(links) == 1
        assert (links[0].consumer_repo, links[0].consumer_contract_id) == ("audit-worker", None)

    def test_an_unresolved_routing_key_routes_nowhere(self) -> None:
        publisher = _publisher()
        publisher.meta["routing_key_unresolved"] = True
        del publisher.meta["routing_key"]
        contracts = [publisher, _binding("orders", "audit", "#"), _queue_consumer("w", "audit")]
        assert match_contracts(contracts) == []

    def test_an_unresolved_binding_pattern_routes_nowhere(self) -> None:
        binding = _binding("orders", "audit", "")
        binding.meta["routing_key_unresolved"] = True
        assert match_contracts([_publisher(), binding, _queue_consumer("w", "audit")]) == []

    def test_a_queue_fed_directly_is_still_bridged_to_its_exchange(self) -> None:
        contracts = [
            _topic("jobs-api", "provider", "audit", "direct.ts", kind="queue"),
            _publisher(),
            _binding("orders", "audit", "order.*"),
            _queue_consumer("audit-worker", "audit"),
        ]
        links = match_contracts(contracts)
        assert sorted((lk.provider_repo, lk.contract_id) for lk in links) == [
            ("jobs-api", "topic::audit"),
            ("orders", "topic::orders"),
        ]

    def test_one_queue_bound_to_two_exchanges_published_from_one_file(self) -> None:
        refunds = _topic("orders", "provider", "refunds", "pub.ts", kind="exchange", routing_key="r")
        contracts = [
            _publisher(),
            refunds,
            _binding("orders", "audit", "order.*"),
            _topic("orders", "consumer", "refunds", "setup.ts", kind="binding", queue="audit", routing_key="#"),
            _queue_consumer("audit-worker", "audit"),
        ]
        assert sorted(lk.contract_id for lk in match_contracts(contracts)) == [
            "topic::orders",
            "topic::refunds",
        ]

    def test_a_routing_key_the_binding_never_receives_does_not_link(self) -> None:
        contracts = [
            _publisher(key="payment.captured"),
            _binding("orders", "audit", "order.*"),
            _queue_consumer("audit-worker", "audit"),
        ]
        assert match_contracts(contracts) == []

    def test_a_binding_alone_honours_the_routing_key_too(self) -> None:
        contracts = [_publisher(key="payment.captured"), _binding("audit-worker", "audit", "order.*")]
        assert match_contracts(contracts) == []

    def test_a_queue_named_directly_still_links_exactly(self) -> None:
        contracts = [
            _topic("pub", "provider", "jobs", "p.ts", kind="queue"),
            _queue_consumer("worker", "jobs"),
        ]
        links = match_contracts(contracts)
        assert len(links) == 1
        assert links[0].consumer_contract_id is None

    def test_same_service_is_not_bridged(self) -> None:
        contracts = [_publisher(), _binding("orders", "audit", "order.*"), _queue_consumer("orders", "audit")]
        assert match_contracts(contracts) == []


class TestDiagnosticsReadTheConsumerId:
    def test_a_bridged_consumer_is_not_reported_unmatched(self) -> None:
        contracts = [
            _publisher(),
            _binding("orders", "audit", "order.*"),
            _queue_consumer("audit-worker", "audit"),
        ]
        links = match_contracts(contracts)
        diag = build_diagnostics(contracts, links)
        unmatched = {(u.repo, u.contract_id) for u in diag.unmatched_consumers}
        assert ("audit-worker", "topic::audit") not in unmatched

    def test_a_binding_row_is_never_reported_unmatched(self) -> None:
        # The binding sits in the producer's own service, so nothing links it.
        contracts = [_publisher(), _binding("orders", "audit", "order.*")]
        diag = build_diagnostics(contracts, match_contracts(contracts))
        assert diag.unmatched_consumers == []

    def test_the_link_round_trips_its_consumer_id(self) -> None:
        link = match_contracts(
            [_publisher(), _binding("orders", "audit", "#"), _queue_consumer("w", "audit")]
        )[0]
        assert ContractLink.from_dict(link.to_dict()).consumer_contract_id == "topic::audit"
        plain = ContractLink.from_dict({**link.to_dict(), "consumer_contract_id": None})
        assert "consumer_contract_id" not in plain.to_dict()
