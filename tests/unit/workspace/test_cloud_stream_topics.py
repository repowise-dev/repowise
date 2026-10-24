"""Spring Cloud Stream topic dialect: bindings and StreamBridge."""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.topic import TopicExtractor
from repowise.core.workspace.matching import match_contracts
from repowise.core.workspace.origins import ContractOrigin


def _extract(root: Path, rel: str, content: str, alias: str = "svc"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return TopicExtractor().extract(root, alias)


def _ids(rows, role: str) -> list[str]:
    return sorted(c.contract_id for c in rows if c.role == role)


def test_cloud_stream_yaml_in_and_out_bindings(tmp_path: Path) -> None:
    src = (
        "spring:\n"
        "  cloud:\n"
        "    stream:\n"
        "      bindings:\n"
        "        chargeOrder-in-0:\n"
        "          destination: orders\n"
        "          group: billing\n"
        "        notification-out-0:\n"
        "          destination: notifications\n"
    )
    rows = _extract(tmp_path, "application.yml", src)
    assert _ids(rows, "consumer") == ["topic::orders"]
    assert _ids(rows, "provider") == ["topic::notifications"]
    [in_c] = [c for c in rows if c.role == "consumer"]
    assert in_c.origin == ContractOrigin.ROUTE_REGISTRATION
    assert in_c.meta == {"topic": "orders", "broker": "cloud_stream", "kind": "topic"}


def test_cloud_stream_flat_properties_bindings(tmp_path: Path) -> None:
    src = (
        "spring.cloud.stream.bindings.chargeOrder-in-0.destination=orders\n"
        "spring.cloud.stream.bindings.notification-out-0.destination=notifications\n"
    )
    rows = _extract(tmp_path, "application.properties", src)
    assert _ids(rows, "consumer") == ["topic::orders"]
    assert _ids(rows, "provider") == ["topic::notifications"]


def test_cloud_stream_stream_bridge_send(tmp_path: Path) -> None:
    yml = (
        "spring:\n"
        "  cloud:\n"
        "    stream:\n"
        "      bindings:\n"
        "        orderEvents-out-0:\n"
        "          destination: orders\n"
    )
    java = (
        "import org.springframework.cloud.stream.function.StreamBridge;\n"
        "class Sender {\n"
        "  void send(StreamBridge streamBridge, Order order) {\n"
        '    streamBridge.send("orderEvents-out-0", order);\n'
        '    streamBridge.send("directTopic", order);\n'
        "  }\n"
        "}\n"
    )
    _extract(tmp_path, "src/main/resources/application.yml", yml)
    rows = _extract(tmp_path, "src/main/java/Sender.java", java)
    assert _ids(rows, "provider") == ["topic::directtopic", "topic::orders"]
    [c] = [r for r in rows if r.contract_id == "topic::orders"]
    assert c.origin == ContractOrigin.ROUTE_REGISTRATION


def test_cloud_stream_binding_without_destination_ignored(tmp_path: Path) -> None:
    src = (
        "spring:\n"
        "  cloud:\n"
        "    stream:\n"
        "      bindings:\n"
        "        noDest-in-0:\n"
        "          group: mygroup\n"
    )
    assert _extract(tmp_path, "application.yml", src) == []


def test_cloud_stream_links_across_repos(tmp_path: Path) -> None:
    producer = _extract(
        tmp_path / "orders",
        "application.yml",
        "spring.cloud.stream.bindings.orders-out-0.destination=orders\n",
        "orders",
    )
    consumer = _extract(
        tmp_path / "billing",
        "application.yml",
        "spring.cloud.stream.bindings.orders-in-0.destination=orders\n",
        "billing",
    )
    links = [lk for lk in match_contracts(producer + consumer) if lk.contract_type == "topic"]
    assert [(lk.provider_repo, lk.consumer_repo, lk.contract_id) for lk in links] == [
        ("orders", "billing", "topic::orders")
    ]


def test_cloud_stream_isolated_from_kafka_topic(tmp_path: Path) -> None:
    cs = _extract(
        tmp_path / "cs",
        "application.yml",
        "spring.cloud.stream.bindings.orders-in-0.destination=orders\n",
        "cs",
    )
    k = _extract(
        tmp_path / "k",
        "P.java",
        'kafkaTemplate.send("orders", payload);\n',
        "k",
    )
    assert [lk for lk in match_contracts(cs + k) if lk.contract_type == "topic"] == []
