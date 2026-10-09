"""Topic matching: routing keys and queues reached through an exchange binding."""

from __future__ import annotations

import pytest

from repowise.core.workspace.contracts import Contract, ContractLink
from repowise.core.workspace.diagnostics import build_diagnostics
from repowise.core.workspace.matching import match_contracts
from repowise.core.workspace.matching.topic import pattern_matches, routing_matches


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


class TestPatternMatches:
    @pytest.mark.parametrize(
        ("syntax", "pattern", "name", "expected"),
        [
            ("nats", "orders.*", "orders.created", True),
            ("nats", "orders.*", "orders.created.v2", False),
            ("nats", "orders.>", "orders.created.v2", True),
            ("nats", "orders.>", "orders", False),
            ("nats", "*.created", "orders.created", True),
            ("glob", "orders.*", "orders.created.v2", True),
            ("glob", "orders.?", "orders.a", True),
            ("glob", "Orders.*", "orders.a", True),
            ("regex", "orders\\..*", "orders.created", True),
            ("regex", "orders\\..*", "ordersXcreated", False),
            ("regex", "[unclosed", "orders", False),
            ("unknown", "*", "orders", False),
        ],
    )
    def test_semantics(self, syntax: str, pattern: str, name: str, expected: bool) -> None:
        assert pattern_matches(syntax, pattern, name) is expected


class TestPatternSubscriptions:
    def _subscriber(self, pattern: str, syntax: str = "nats") -> Contract:
        return _topic("audit", "consumer", pattern, "sub.go", kind="subject", pattern=syntax)

    def test_a_pattern_links_every_publisher_it_matches(self) -> None:
        contracts = [
            _topic("orders", "provider", "orders.created", "a.go", kind="subject"),
            _topic("orders", "provider", "orders.deleted", "b.go", kind="subject"),
            _topic("billing", "provider", "invoices.created", "c.go", kind="subject"),
            self._subscriber("orders.*"),
        ]
        links = match_contracts(contracts)
        # Each link names the topic it carries; the subscription is the consumer's id.
        assert sorted((lk.contract_id, lk.provider_file) for lk in links) == [
            ("topic::orders.created", "a.go"),
            ("topic::orders.deleted", "b.go"),
        ]
        assert {lk.consumer_contract_id for lk in links} == {"topic::orders.*"}

    def test_two_topics_from_one_file_are_two_links(self) -> None:
        contracts = [
            _topic("orders", "provider", "orders.created", "pub.go", kind="subject"),
            _topic("orders", "provider", "orders.deleted", "pub.go", kind="subject"),
            self._subscriber("orders.*"),
        ]
        assert len(match_contracts(contracts)) == 2

    @pytest.mark.parametrize(("pattern", "syntax"), [(">", "nats"), ("*", "glob"), (".*", "regex")])
    def test_a_pattern_of_wildcards_alone_links_nothing(self, pattern: str, syntax: str) -> None:
        contracts = [
            _topic("orders", "provider", "orders.created", "a.go", kind="subject"),
            self._subscriber(pattern, syntax),
        ]
        assert match_contracts(contracts) == []

    def test_a_pattern_reaches_only_its_own_broker(self) -> None:
        kafka = _topic("orders", "provider", "orders.created", "a.ts", kind="topic")
        kafka.meta["broker"] = "kafka"
        nats = self._subscriber("orders.*")
        nats.meta["broker"] = "nats"
        assert match_contracts([kafka, nats]) == []


    def test_a_pattern_in_its_own_service_is_not_linked(self) -> None:
        contracts = [
            _topic("audit", "provider", "orders.created", "a.go", kind="subject"),
            self._subscriber("orders.*"),
        ]
        assert match_contracts(contracts) == []


def _on(broker: str, role: str, repo: str, name: str = "emails", **meta) -> Contract:
    c = _topic(repo, role, name, f"{repo}.src", kind="queue", **meta)
    c.meta["broker"] = broker
    return c


class TestTransports:
    @pytest.mark.parametrize(
        ("provider", "consumer", "linked"),
        [
            ("rabbitmq", "rabbitmq", True),
            ("kafka", "rabbitmq", False),
            ("sns", "sqs", False),
            ("laravel", "rabbitmq", True),
            ("rabbitmq", "laravel", True),
            ("nestjs", "kafka", True),
            ("laravel", "bullmq", False),
            ("redis", "bullmq", False),
            ("bullmq", "bullmq", True),
        ],
    )
    def test_which_brokers_meet(self, provider: str, consumer: str, linked: bool) -> None:
        links = match_contracts([_on(provider, "provider", "a"), _on(consumer, "consumer", "b")])
        assert bool(links) is linked

    def test_two_laravel_apps_meet_only_on_one_job_class(self) -> None:
        same = [
            _on("laravel", "provider", "web", job="App\\Jobs\\Send"),
            _on("laravel", "consumer", "worker", job="App\\Jobs\\Send"),
        ]
        other = [
            _on("laravel", "provider", "web", job="App\\Jobs\\Send"),
            _on("laravel", "consumer", "worker", job="App\\Jobs\\Other"),
        ]
        assert len(match_contracts(same)) == 1
        assert match_contracts(other) == []

    def test_a_name_that_only_looks_like_a_pattern_is_exact(self) -> None:
        contracts = [
            _topic("orders", "provider", "orders.created", "a.go", kind="subject"),
            _topic("audit", "consumer", "orders.*", "sub.go", kind="subject"),
        ]
        assert match_contracts(contracts) == []
