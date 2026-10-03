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


# amqplib's `publish` and `consume` are read only in a file importing it.
_AMQP = "import amqp from 'amqplib';\n"


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

    def test_kafka_listener_singular_topic_key(self, tmp_path: Path) -> None:
        c = _one(_extract(tmp_path, "C.java", '@KafkaListener(topic = "orders")'), "consumer")
        assert c.contract_id == "topic::orders"

    def test_kafkajs_subscribe_singular_topic(self, tmp_path: Path) -> None:
        src = "await consumer.subscribe({ topic: 'orders' });"
        c = _one(_extract(tmp_path, "c.ts", src), "consumer")
        assert c.contract_id == "topic::orders"

    def test_confluent_produce_topic_keyword(self, tmp_path: Path) -> None:
        src = "producer.produce(topic='orders', value=b'x')\n"
        c = _one(_extract(tmp_path, "p.py", src), "provider")
        assert c.contract_id == "topic::orders"

    def test_an_array_keeps_the_elements_it_can_read(self, tmp_path: Path) -> None:
        rows = _extract(tmp_path, "C.java", '@KafkaListener(topics = {"orders", dynamicTopic})')
        assert [c.contract_id for c in rows] == ["topic::orders"]

    def test_a_spring_property_placeholder_is_refused(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, "C.java", '@KafkaListener(topics = "${orders.topic}")') == []

    @pytest.mark.parametrize(
        ("rel", "src"),
        [
            ("pub.go", 'const Orders = "orders"\nc.ConsumePartition(Orders, 0, 0)\n'),
            ("Pub.java", 'static final String ORDERS = "orders";\nkafkaTemplate.send(ORDERS, p);\n'),
        ],
    )
    def test_constants_fold_in_go_and_java(self, tmp_path: Path, rel: str, src: str) -> None:
        assert [c.contract_id for c in _extract(tmp_path, rel, src)] == ["topic::orders"]

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
        src = _AMQP + "channel.publish('orders', 'order.created', body);"
        c = _one(_extract(tmp_path, "pub.ts", src), "provider")
        assert c.contract_id == "topic::orders"
        assert c.meta["kind"] == "exchange"
        assert c.meta["routing_key"] == "order.created"

    def test_publish_to_the_default_exchange_names_the_queue(self, tmp_path: Path) -> None:
        src = _AMQP + "channel.publish('', 'jobs', body);"
        c = _one(_extract(tmp_path, "pub.ts", src), "provider")
        assert c.contract_id == "topic::jobs"
        assert c.meta["kind"] == "queue"
        assert "routing_key" not in c.meta

    def test_two_routing_keys_on_one_exchange_are_two_rows(self, tmp_path: Path) -> None:
        src = _AMQP + "channel.publish('orders', 'a', b);\nchannel.publish('orders', 'b', b);\n"
        keys = sorted(c.meta["routing_key"] for c in _extract(tmp_path, "pub.ts", src))
        assert keys == ["a", "b"]

    def test_repeated_identical_call_is_one_row(self, tmp_path: Path) -> None:
        src = _AMQP + "channel.consume('jobs', a);\nchannel.consume('jobs', b);\n"
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

    def test_bind_queue_with_an_unreadable_queue_binds_nothing(self, tmp_path: Path) -> None:
        src = "channel.bindQueue(q.queue, 'orders', 'order.*');"
        assert _extract(tmp_path, "w.js", src) == []

    def test_bind_queue_with_an_unreadable_pattern_is_marked(self, tmp_path: Path) -> None:
        src = "channel.bindQueue('audit', 'orders', pattern);"
        c = _one(_extract(tmp_path, "w.js", src), "consumer")
        assert c.meta["routing_key_unresolved"] is True
        assert "routing_key" not in c.meta

    def test_spring_convert_and_send_reads_the_routing_key(self, tmp_path: Path) -> None:
        src = 'rabbitTemplate.convertAndSend("orders", "order.created", event);'
        c = _one(_extract(tmp_path, "P.java", src), "provider")
        assert (c.contract_id, c.meta["kind"], c.meta["routing_key"]) == (
            "topic::orders",
            "exchange",
            "order.created",
        )

    def test_spring_two_argument_send_routes_nowhere(self, tmp_path: Path) -> None:
        src = 'rabbitTemplate.convertAndSend("jobs", event);'
        c = _one(_extract(tmp_path, "P.java", src), "provider")
        assert c.meta["routing_key_unresolved"] is True

    @pytest.mark.parametrize(
        "call",
        [
            "channel.basic_consume(queue='jobs', on_message_callback=cb)",
            "channel.basic_consume('jobs', cb)",
        ],
    )
    def test_pika_basic_consume(self, tmp_path: Path, call: str) -> None:
        c = _one(_extract(tmp_path, "w.py", call + "\n"), "consumer")
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
        assert _extract(tmp_path, "w.js", _AMQP + call) == []

    def test_publish_and_consume_need_the_amqplib_import(self, tmp_path: Path) -> None:
        src = "channel.publish('orders', 'k', b);\nchannel.consume('jobs', h);\n"
        assert _extract(tmp_path, "w.ts", src) == []

    def test_any_channel_name_is_read_in_an_amqplib_file(self, tmp_path: Path) -> None:
        src = _AMQP + "await this.ch.consume('jobs', h);\n"
        assert _one(_extract(tmp_path, "w.ts", src), "consumer").contract_id == "topic::jobs"

    def test_send_to_queue_needs_no_import(self, tmp_path: Path) -> None:
        src = "currentChannel().sendToQueue('jobs', body);\n"
        assert _one(_extract(tmp_path, "w.ts", src), "provider").contract_id == "topic::jobs"

    def test_an_object_member_constant_folds(self, tmp_path: Path) -> None:
        src = (
            _AMQP
            + "export const QUEUES = { ticketSold: 'ticket.sold' } as const;\n"
            + "channel.consume(QUEUES.ticketSold, h);\n"
        )
        assert _one(_extract(tmp_path, "w.ts", src), "consumer").contract_id == "topic::ticket.sold"


class TestPhpAmqplib:
    _USE = "<?php\nuse PhpAmqpLib\\Message\\AMQPMessage;\n"

    def test_publish_to_an_exchange(self, tmp_path: Path) -> None:
        src = self._USE + "$channel->basic_publish($msg, 'orders', 'order.created');\n"
        c = _one(_extract(tmp_path, "Pub.php", src), "provider")
        assert (c.contract_id, c.meta["kind"], c.meta["routing_key"]) == (
            "topic::orders",
            "exchange",
            "order.created",
        )

    def test_publish_to_the_default_exchange_names_the_queue(self, tmp_path: Path) -> None:
        src = self._USE + "$channel->basic_publish($msg, '', 'jobs');\n"
        c = _one(_extract(tmp_path, "Pub.php", src), "provider")
        assert (c.contract_id, c.meta["kind"]) == ("topic::jobs", "queue")

    def test_consume_and_bind(self, tmp_path: Path) -> None:
        src = (
            self._USE
            + "$channel->queue_bind('audit', 'orders', 'order.*');\n"
            + "$channel->basic_consume('audit', '', false, true, false, false, $cb);\n"
        )
        rows = _extract(tmp_path, "Work.php", src)
        assert {(c.contract_id, c.meta["kind"]) for c in rows} == {
            ("topic::orders", "binding"),
            ("topic::audit", "queue"),
        }

    def test_nothing_without_the_library(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, "X.php", "<?php\n$x->basic_publish($m, 'orders');\n") == []


class TestNats:
    def test_subscribe_and_publish(self, tmp_path: Path) -> None:
        src = 'nc.Subscribe("events.created", h)\nnc.Publish("events.deleted", d)\n'
        rows = _extract(tmp_path, "main.go", src)
        assert {(c.role, c.contract_id) for c in rows} == {
            ("consumer", "topic::events.created"),
            ("provider", "topic::events.deleted"),
        }
        assert {c.meta["kind"] for c in rows} == {"subject"}

    @pytest.mark.parametrize(
        ("rel", "src", "role"),
        [
            ("sub.py", 'await nc.subscribe("events.created", cb=h)\n', "consumer"),
            ("Pub.java", 'nc.publish("events.created", data);', "provider"),
            ("pub.ts", "nc.publish('events.created', sc.encode(x));", "provider"),
        ],
    )
    def test_nats_in_other_languages(self, tmp_path: Path, rel: str, src: str, role: str) -> None:
        c = _one(_extract(tmp_path, rel, src), role)
        assert c.contract_id == "topic::events.created"

    def test_an_unrelated_receiver_is_not_nats(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, "x.ts", "store.subscribe('state', h);") == []

    def test_bare_sub_is_not_a_nats_receiver(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, "x.ts", "sub.subscribe('events.created', h);") == []

    @pytest.mark.parametrize("subject", ["events.*", "events.>"])
    def test_a_wildcard_subscription_is_a_pattern(self, tmp_path: Path, subject: str) -> None:
        c = _one(_extract(tmp_path, "sub.go", f'nc.Subscribe("{subject}", h)\n'), "consumer")
        assert c.meta["pattern"] == "nats"

    def test_a_plain_subject_is_not_a_pattern(self, tmp_path: Path) -> None:
        c = _one(_extract(tmp_path, "sub.go", 'nc.Subscribe("events.created", h)\n'), "consumer")
        assert "pattern" not in c.meta


class TestKafkaPattern:
    def test_topic_pattern_is_a_regex_subscription(self, tmp_path: Path) -> None:
        src = '@KafkaListener(topicPattern = "orders\\\\..*")\nvoid f() {}'
        c = _one(_extract(tmp_path, "C.java", src), "consumer")
        assert c.meta["pattern"] == "regex"
        assert c.meta["topic"] == "orders\\..*"  # the Java escape read as the regex it spells

    def test_a_topics_listener_is_not_a_pattern(self, tmp_path: Path) -> None:
        c = _one(_extract(tmp_path, "C.java", '@KafkaListener(topics = "a.*")'), "consumer")
        assert "pattern" not in c.meta


class TestBullMq:
    _IMPORT = "import { Queue, Worker } from 'bullmq';\n"

    def test_queue_and_worker(self, tmp_path: Path) -> None:
        src = self._IMPORT + "const q = new Queue('emails');\nnew Worker<Job>('emails', process);\n"
        rows = _extract(tmp_path, "q.ts", src)
        assert {(c.role, c.contract_id, c.meta["kind"]) for c in rows} == {
            ("provider", "topic::emails", "queue"),
            ("consumer", "topic::emails", "queue"),
        }

    def test_a_queue_class_of_another_library_is_not_bullmq(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, "q.ts", "const q = new Queue('emails');\n") == []

    def test_nestjs_processor_and_inject_queue(self, tmp_path: Path) -> None:
        src = (
            "import { Processor, InjectQueue } from '@nestjs/bullmq';\n"
            "@Processor('audio')\nclass AudioProcessor {}\n"
            "constructor(@InjectQueue('audio') private q: Queue) {}\n"
        )
        rows = _extract(tmp_path, "audio.ts", src)
        assert {(c.role, c.contract_id) for c in rows} == {
            ("consumer", "topic::audio"),
            ("provider", "topic::audio"),
        }

    def test_a_flow_names_its_queue(self, tmp_path: Path) -> None:
        src = (
            self._IMPORT
            + "const flow = new FlowProducer();\n"
            + "flow.add({ name: 'j', queueName: 'renders' });\n"
        )
        assert _one(_extract(tmp_path, "f.ts", src), "provider").contract_id == "topic::renders"


class TestAws:
    def test_sqs_queue_url_names_its_last_segment(self, tmp_path: Path) -> None:
        src = (
            "const URL = 'https://sqs.eu-west-1.amazonaws.com/123456789012/orders';\n"
            "await sqs.send(new SendMessageCommand({ QueueUrl: URL, MessageBody: b }));\n"
            "await sqs.send(new ReceiveMessageCommand({ QueueUrl: `${base}/orders` }));\n"
        )
        rows = _extract(tmp_path, "q.ts", src)
        assert {(c.role, c.contract_id, c.meta["broker"]) for c in rows} == {
            ("provider", "topic::orders", "sqs"),
            ("consumer", "topic::orders", "sqs"),
        }

    def test_a_queue_url_from_the_environment_is_refused(self, tmp_path: Path) -> None:
        src = "new SendMessageCommand({ QueueUrl: process.env.QUEUE_URL, MessageBody: b });\n"
        assert _extract(tmp_path, "q.ts", src) == []

    def test_sns_topic_arn_names_its_last_field(self, tmp_path: Path) -> None:
        src = "new PublishCommand({ TopicArn: 'arn:aws:sns:eu-west-1:123:orders', Message: m });\n"
        c = _one(_extract(tmp_path, "p.ts", src), "provider")
        assert (c.contract_id, c.meta["broker"], c.meta["kind"]) == ("topic::orders", "sns", "topic")

    def test_boto3_keywords(self, tmp_path: Path) -> None:
        src = "sqs.send_message(QueueUrl='https://sqs.x/1/jobs', MessageBody='x')\n"
        assert _one(_extract(tmp_path, "p.py", src), "provider").contract_id == "topic::jobs"

    def test_send_message_without_a_queue_url_is_not_sqs(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, "bot.ts", "bot.sendMessage(chatId, 'hello');\n") == []


class TestRedis:
    _IMPORT = "import Redis from 'ioredis';\n"

    def test_publish_subscribe(self, tmp_path: Path) -> None:
        src = self._IMPORT + "pub.publish('scores', x);\nsub.subscribe('scores');\n"
        rows = _extract(tmp_path, "r.ts", src)
        assert {(c.role, c.contract_id, c.meta["broker"], c.meta["kind"]) for c in rows} == {
            ("provider", "topic::scores", "redis", "channel"),
            ("consumer", "topic::scores", "redis", "channel"),
        }

    def test_psubscribe_is_a_glob_pattern(self, tmp_path: Path) -> None:
        src = self._IMPORT + "sub.psubscribe('scores.*');\n"
        assert _one(_extract(tmp_path, "r.ts", src), "consumer").meta["pattern"] == "glob"

    def test_an_rxjs_subscribe_is_not_a_channel(self, tmp_path: Path) -> None:
        src = self._IMPORT + "obs$.subscribe((v) => log(v));\nstore.subscribe(handler);\n"
        assert _extract(tmp_path, "r.ts", src) == []

    def test_nothing_without_a_redis_client(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, "r.ts", "emitter.publish('scores', x);\n") == []

    def test_a_redis_client_named_client_is_redis_not_nats(self, tmp_path: Path) -> None:
        src = "import { createClient } from 'redis';\nclient.publish('scores', x);\n"
        assert _one(_extract(tmp_path, "r.ts", src), "provider").meta["broker"] == "redis"

    def test_laravel_facade(self, tmp_path: Path) -> None:
        src = "<?php\nRedis::publish('scores', $x);\nRedis::subscribe(['scores', 'ranks'], $cb);\n"
        rows = _extract(tmp_path, "R.php", src)
        assert sorted((c.role, c.contract_id) for c in rows) == [
            ("consumer", "topic::ranks"),
            ("consumer", "topic::scores"),
            ("provider", "topic::scores"),
        ]


class TestNestMicroservices:
    _IMPORT = "import { EventPattern, MessagePattern, ClientProxy } from '@nestjs/microservices';\n"

    def test_handlers(self, tmp_path: Path) -> None:
        src = self._IMPORT + "@EventPattern('ticket.sold')\nf() {}\n@MessagePattern('sum')\ng() {}\n"
        rows = _extract(tmp_path, "c.ts", src)
        assert {(c.role, c.contract_id, c.meta["broker"]) for c in rows} == {
            ("consumer", "topic::ticket.sold", "nestjs"),
            ("consumer", "topic::sum", "nestjs"),
        }

    def test_a_client_proxy_emits_under_any_name(self, tmp_path: Path) -> None:
        src = (
            self._IMPORT
            + "constructor(@Inject('BILLING') private readonly billing: ClientProxy) {}\n"
            + "this.billing.emit('ticket.sold', t);\n"
            + "res.send('ok');\n"
        )
        c = _one(_extract(tmp_path, "s.ts", src), "provider")
        assert (c.contract_id, c.symbol_name) == (
            "topic::ticket.sold",
            "ClientProxy.emit('ticket.sold')",
        )

    def test_an_object_pattern_is_named_as_nest_serializes_it(self, tmp_path: Path) -> None:
        src = (
            self._IMPORT
            + "@MessagePattern({ role: 'math', cmd: 'sum' })\nf() {}\n"
            + "client: ClientProxy;\nthis.client.send({ cmd: 'sum', role: 'math' }, [1, 2]);\n"
        )
        rows = _extract(tmp_path, "m.ts", src)
        assert {c.contract_id for c in rows} == {'topic::{"cmd":"sum","role":"math"}'}
        assert {c.role for c in rows} == {"consumer", "provider"}

    def test_handlers_need_the_microservices_import(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, "c.ts", "@EventPattern('x')\nf() {}\n") == []
