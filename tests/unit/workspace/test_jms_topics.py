"""JMS topic dialect: Spring JmsTemplate sends and receives, @JmsListener, listener containers."""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.topic import TopicExtractor
from repowise.core.workspace.matching import match_contracts
from repowise.core.workspace.origins import ContractOrigin

_IMPORT = "import org.springframework.jms.core.JmsTemplate;\n"


def _extract(root: Path, rel: str, content: str, alias: str = "svc"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return TopicExtractor().extract(root, alias)


def _ids(rows, role: str) -> list[str]:
    return sorted(c.contract_id for c in rows if c.role == role)


def test_jms_listener_reads_destination_not_container_factory(tmp_path: Path) -> None:
    src = '@JmsListener(destination = "order", containerFactory = "jsaFactory")\nvoid f() {}'
    [c] = _extract(tmp_path, "OrderReceiver.java", src)
    assert (c.contract_id, c.role) == ("topic::order", "consumer")
    assert c.meta == {"topic": "order", "broker": "jms", "kind": "queue"}
    assert c.origin == ContractOrigin.CLIENT_CALL


def test_jms_listener_without_destination_and_placeholder_are_refused(tmp_path: Path) -> None:
    src = (
        '@JmsListener(containerFactory = "jsaFactory", subscription = "sub")\nvoid a() {}\n'
        '@JmsListener(destination = "${app.queue}")\nvoid b() {}\n'
    )
    assert _extract(tmp_path, "L.java", src) == []


def test_template_sends_and_receives_under_jms_receiver_names(tmp_path: Path) -> None:
    src = _IMPORT + (
        'static final String Q = "orders";\n'
        "void f() {\n"
        "  jmsTemplate.convertAndSend(Q, order);\n"
        '  this.topicJmsTemplate.send("audit", s -> s.createTextMessage(x));\n'
        '  getJmsTemplate().convertAndSend("billing", b);\n'
        '  jmsMessagingTemplate.convertAndSend("ledger", l);\n'
        '  Object r = jmsTemplate.receiveAndConvert("replies");\n'
        "}\n"
    )
    rows = _extract(tmp_path, "Sender.java", src)
    assert _ids(rows, "provider") == [
        "topic::audit",
        "topic::billing",
        "topic::ledger",
        "topic::orders",
    ]
    assert _ids(rows, "consumer") == ["topic::replies"]


def test_one_argument_send_is_the_default_destination(tmp_path: Path) -> None:
    src = _IMPORT + (
        'jmsTemplate.convertAndSend("hello, world");\n'
        "jmsTemplate.send(s -> s.createTextMessage(x));\n"
        "jmsTemplate.receive();\n"
        'jmsTemplate.receiveSelected("type = \'a\'");\n'
        'jmsTemplate.convertAndSend("hello", m -> { m.setJMSType("t"); return m; });\n'
        'jmsTemplate.convertAndSend("hello", (m) -> m);\n'
    )
    assert _extract(tmp_path, "Sender.java", src) == []


def test_request_reply_verbs_and_kotlin_templates(tmp_path: Path) -> None:
    src = _IMPORT + (
        'jmsTemplate.sendAndReceive("quotes", s -> s.createTextMessage(x));\n'
        'jmsMessagingTemplate.convertSendAndReceive("prices", req, Price.class);\n'
    )
    assert _ids(_extract(tmp_path, "Q.java", src), "provider") == [
        "topic::prices",
        "topic::quotes",
    ]
    kt = _IMPORT + 'jmsTemplate.convertAndSend("orders", order)\n'
    assert _ids(_extract(tmp_path / "kt", "S.kt", kt), "provider") == ["topic::orders"]


def test_lookalike_templates_are_not_jms(tmp_path: Path) -> None:
    # A STOMP messaging template and a template under a name that does not say JMS.
    src = _IMPORT + (
        'messagingTemplate.convertAndSend("/topic/greetings", g);\n'
        'template.convertAndSend("orders", o);\n'
        '// jmsTemplate.convertAndSend("commented", o);\n'
    )
    assert _extract(tmp_path, "Sender.java", src) == []


def test_without_the_type_in_the_file_nothing_is_read(tmp_path: Path) -> None:
    assert _extract(tmp_path, "S.java", 'jmsTemplate.convertAndSend("orders", o);') == []


def test_listener_container_destination_name(tmp_path: Path) -> None:
    src = (
        "DefaultMessageListenerContainer c = new DefaultMessageListenerContainer();\n"
        'c.setDestinationName("personneQueue");\n'
        'template.setDefaultDestinationName("other");\n'
    )
    [c] = _extract(tmp_path, "JMSConfiguration.java", src)
    assert (c.contract_id, c.role) == ("topic::personnequeue", "consumer")


def test_uri_style_queue_names_drop_the_prefix(tmp_path: Path) -> None:
    src = _IMPORT + (
        'jmsTemplate.convertAndSend("queue://orders", o);\n'
        'jmsTemplate.convertAndSend("queue:///DEV.QUEUE.1?targetClient=1", o);\n'
        'jmsTemplate.convertAndSend("topic://prices", p);\n'
    )
    assert _ids(_extract(tmp_path, "S.java", src), "provider") == [
        "topic::dev.queue.1",
        "topic::orders",
        "topic::topic://prices",
    ]


def test_kotlin_listener(tmp_path: Path) -> None:
    [c] = _extract(tmp_path, "L.kt", '@JmsListener(destination = "order")\nfun f(o: Order) {}')
    assert c.contract_id == "topic::order"


def test_sender_links_to_listener_in_another_repo(tmp_path: Path) -> None:
    cart = _extract(
        tmp_path / "cart",
        "OrderSender.java",
        _IMPORT + 'jmsTemplate.convertAndSend("order", order);',
        "cart",
    )
    order = _extract(
        tmp_path / "order",
        "OrderReceiver.java",
        '@JmsListener(destination = "queue://order")\nvoid receive(OrderDto o) {}',
        "order",
    )
    links = [lk for lk in match_contracts(cart + order) if lk.contract_type == "topic"]
    assert [(lk.provider_repo, lk.consumer_repo, lk.contract_id) for lk in links] == [
        ("cart", "order", "topic::order")
    ]


def test_a_jms_queue_does_not_meet_a_kafka_topic_of_the_same_name(tmp_path: Path) -> None:
    jms = _extract(
        tmp_path / "a", "S.java", _IMPORT + 'jmsTemplate.convertAndSend("orders", o);', "a"
    )
    kafka = _extract(tmp_path / "b", "C.java", '@KafkaListener(topics = "orders")\nvoid f() {}', "b")
    assert [lk for lk in match_contracts(jms + kafka) if lk.contract_type == "topic"] == []
