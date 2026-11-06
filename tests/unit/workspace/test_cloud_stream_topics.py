"""Spring Cloud Stream topic dialect: bindings and StreamBridge."""

from __future__ import annotations

from pathlib import Path

import pytest

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


@pytest.mark.parametrize("gap", ["\n", "\n\n", "   \n", "# comment\n", "  # comment\n"])
def test_blank_or_comment_lines_do_not_close_a_nested_binding(tmp_path, gap):
    source = (
        "spring:\n  cloud:\n    stream:\n      bindings:\n        orders-out-0:\n"
        + gap
        + "          destination: orders\n"
    )
    rows = _extract(tmp_path, "application.yml", source)
    assert _ids(rows, "provider") == ["topic::orders"]


@pytest.mark.parametrize("suffix", [".java", ".kt"])
@pytest.mark.parametrize(
    "comment",
    ['// streamBridge.send("orders", payload);', '/* streamBridge.send("orders", payload); */'],
)
def test_commented_send_cannot_claim_the_live_send_identity(tmp_path, suffix, comment):
    field = (
        "lateinit var streamBridge: StreamBridge"
        if suffix == ".kt"
        else "StreamBridge streamBridge;"
    )
    method = "fun send(payload: Any)" if suffix == ".kt" else "void send(Object payload)"
    source = (
        f"class Sender {{\n  {field}\n  {method} {{\n    "
        + comment
        + '\n    streamBridge.send("orders", payload);\n  }\n}\n'
    )
    rows = _extract(tmp_path, "Sender" + suffix, source)
    assert _ids(rows, "provider") == ["topic::orders"]
    assert rows[0].line == 5


@pytest.mark.parametrize(
    ("suffix", "comment"), [(".yml", "#"), (".properties", "#"), (".properties", "!")]
)
def test_commented_property_does_not_hide_or_create_a_provider(tmp_path, suffix, comment):
    source = (
        f"{comment} spring.cloud.stream.bindings.old-out-0.destination=orders\n"
        "spring.cloud.stream.bindings.live-out-0.destination=orders\n"
    )
    rows = _extract(tmp_path, "application" + suffix, source)
    assert _ids(rows, "provider") == ["topic::orders"]
    assert rows[0].line == 2
    assert _extract(tmp_path, "application" + suffix, source.splitlines(keepends=True)[0]) == []
