"""Topic dialects: what each broker call reads, and what it refuses."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.extractors.topic import TopicExtractor


def _extract(tmp_path: Path, rel: str, content: str, alias: str = "svc"):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return TopicExtractor().extract(tmp_path, alias)


def _one(contracts, role: str):
    rows = [c for c in contracts if c.role == role]
    assert len(rows) == 1, rows
    return rows[0]


class TestKafka:
    def test_kafka_listener_consumer(self, tmp_path: Path) -> None:
        c = _one(
            _extract(tmp_path, "Consumer.java", '@KafkaListener(topics = "orders")\nvoid f() {}'),
            "consumer",
        )
        assert c.contract_id == "topic::orders"
        assert c.meta == {"topic": "orders", "broker": "kafka", "kind": "topic"}
        assert c.symbol_name == "@KafkaListener('orders')"

    def test_kafka_listener_reads_every_topic_of_an_array(self, tmp_path: Path) -> None:
        rows = _extract(
            tmp_path, "Consumer.java", '@KafkaListener(id = "g", topics = {"orders", "refunds"})'
        )
        assert sorted(c.contract_id for c in rows) == ["topic::orders", "topic::refunds"]

    def test_kafka_template_provider(self, tmp_path: Path) -> None:
        c = _one(_extract(tmp_path, "Publisher.java", 'kafkaTemplate.send("orders", p);'), "provider")
        assert c.contract_id == "topic::orders"

    def test_kafkajs_producer_reads_the_topic_key(self, tmp_path: Path) -> None:
        c = _one(
            _extract(tmp_path, "producer.js", "await producer.send({ topic: 'payments', messages });"),
            "provider",
        )
        assert c.contract_id == "topic::payments"

    def test_kafkajs_subscribe_reads_a_topics_array(self, tmp_path: Path) -> None:
        rows = _extract(
            tmp_path, "consumer.ts", "await consumer.subscribe({ topics: ['a.created', 'a.deleted'] });"
        )
        assert sorted(c.contract_id for c in rows) == ["topic::a.created", "topic::a.deleted"]

    def test_python_constant_folds(self, tmp_path: Path) -> None:
        src = 'ORDERS_TOPIC = "orders"\n\nproducer.produce(ORDERS_TOPIC, value=b"x")\n'
        c = _one(_extract(tmp_path, "pub.py", src), "provider")
        assert c.contract_id == "topic::orders"

    def test_a_name_assigned_twice_is_refused(self, tmp_path: Path) -> None:
        src = 'T = "a"\nT = "b"\nproducer.produce(T)\n'
        assert _extract(tmp_path, "pub.py", src) == []

    def test_java_listener_shape_is_not_read_in_python(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, "x.py", 'kafkaTemplate.send("orders", p)') == []


class TestRabbitMq:
    def test_send_to_queue_is_a_queue(self, tmp_path: Path) -> None:
        c = _one(
            _extract(tmp_path, "pub.ts", "channel.sendToQueue('ticket.sold', Buffer.from(x));"),
            "provider",
        )
        assert c.contract_id == "topic::ticket.sold"
        assert c.meta["kind"] == "queue"

    def test_publish_carries_exchange_and_routing_key(self, tmp_path: Path) -> None:
        c = _one(
            _extract(tmp_path, "pub.ts", "channel.publish('orders', 'order.created', body);"),
            "provider",
        )
        assert c.contract_id == "topic::orders"
        assert c.meta["kind"] == "exchange"
        assert c.meta["routing_key"] == "order.created"

    def test_publish_to_the_default_exchange_names_the_queue(self, tmp_path: Path) -> None:
        c = _one(_extract(tmp_path, "pub.ts", "channel.publish('', 'jobs', body);"), "provider")
        assert c.contract_id == "topic::jobs"
        assert c.meta["kind"] == "queue"
        assert "routing_key" not in c.meta

    def test_two_routing_keys_on_one_exchange_are_two_rows(self, tmp_path: Path) -> None:
        src = "channel.publish('orders', 'a', b);\nchannel.publish('orders', 'b', b);\n"
        keys = sorted(c.meta["routing_key"] for c in _extract(tmp_path, "pub.ts", src))
        assert keys == ["a", "b"]

    def test_repeated_identical_call_is_one_row(self, tmp_path: Path) -> None:
        src = "channel.consume('jobs', a);\nchannel.consume('jobs', b);\n"
        assert len(_extract(tmp_path, "w.js", src)) == 1

    def test_bind_queue_is_a_binding_consumer_of_the_exchange(self, tmp_path: Path) -> None:
        c = _one(
            _extract(tmp_path, "w.js", "await channel.bindQueue('audit', 'orders', 'order.*');"),
            "consumer",
        )
        assert c.contract_id == "topic::orders"
        assert c.meta == {
            "topic": "orders",
            "broker": "rabbitmq",
            "kind": "binding",
            "routing_key": "order.*",
            "queue": "audit",
        }

    def test_pika_queue_bind_reads_keywords(self, tmp_path: Path) -> None:
        src = "channel.queue_bind(exchange='orders', queue='audit', routing_key='order.#')\n"
        c = _one(_extract(tmp_path, "w.py", src), "consumer")
        assert (c.contract_id, c.meta["queue"], c.meta["routing_key"]) == (
            "topic::orders",
            "audit",
            "order.#",
        )

    def test_pika_basic_publish_default_exchange(self, tmp_path: Path) -> None:
        src = "channel.basic_publish(exchange='', routing_key='jobs', body=b'x')\n"
        c = _one(_extract(tmp_path, "p.py", src), "provider")
        assert (c.contract_id, c.meta["kind"]) == ("topic::jobs", "queue")

    def test_rabbit_listener_consumer(self, tmp_path: Path) -> None:
        c = _one(_extract(tmp_path, "W.java", '@RabbitListener(queues = "jobs")'), "consumer")
        assert (c.contract_id, c.meta["kind"]) == ("topic::jobs", "queue")

    @pytest.mark.parametrize(
        "call",
        [
            "channel.consume(`jobs.${env}`, h);",
            "channel.consume(queueName, h);",
            "channel.consume('jobs' + suffix, h);",
        ],
    )
    def test_a_name_not_settled_in_the_file_is_refused(self, tmp_path: Path, call: str) -> None:
        assert _extract(tmp_path, "w.js", call) == []


class TestNats:
    def test_subscribe_and_publish(self, tmp_path: Path) -> None:
        src = 'nc.Subscribe("events.created", h)\nnc.Publish("events.deleted", d)\n'
        rows = _extract(tmp_path, "main.go", src)
        assert {(c.role, c.contract_id) for c in rows} == {
            ("consumer", "topic::events.created"),
            ("provider", "topic::events.deleted"),
        }
        assert {c.meta["kind"] for c in rows} == {"subject"}

    def test_an_unrelated_receiver_is_not_nats(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, "x.ts", "store.subscribe('state', h);") == []
