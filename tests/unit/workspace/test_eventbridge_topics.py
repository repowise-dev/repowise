"""AWS EventBridge topic dialect: PutEvents producers and rule pattern consumers."""

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


def test_eventbridge_put_events_command_ts(tmp_path: Path) -> None:
    src = (
        'import { EventBridgeClient, PutEventsCommand } from "@aws-sdk/client-eventbridge";\n'
        "const client = new EventBridgeClient({});\n"
        "await client.send(new PutEventsCommand({\n"
        "  Entries: [\n"
        "    {\n"
        '      Source: "com.shop.orders",\n'
        '      DetailType: "OrderCreated",\n'
        '      Detail: JSON.stringify({ id: "1" }),\n'
        "    }\n"
        "  ]\n"
        "}));\n"
    )
    [c] = _extract(tmp_path, "publish.ts", src)
    assert (c.contract_id, c.role) == ("topic::com.shop.orders/ordercreated", "provider")
    assert c.meta == {"topic": "com.shop.orders/OrderCreated", "broker": "eventbridge", "kind": "topic"}
    assert c.origin == ContractOrigin.CLIENT_CALL


def test_eventbridge_put_events_python(tmp_path: Path) -> None:
    src = (
        "import boto3\n"
        "client = boto3.client('events')\n"
        "client.put_events(\n"
        "    Entries=[\n"
        "        {\n"
        "            'Source': 'com.shop.orders',\n"
        "            'DetailType': 'OrderCreated',\n"
        "            'Detail': '{}',\n"
        "        }\n"
        "    ]\n"
        ")\n"
    )
    [c] = _extract(tmp_path, "publish.py", src)
    assert (c.contract_id, c.role) == ("topic::com.shop.orders/ordercreated", "provider")
    assert c.origin == ContractOrigin.CLIENT_CALL


def test_eventbridge_rule_cdk(tmp_path: Path) -> None:
    src = (
        "import { Rule } from 'aws-cdk-lib/aws-events';\n"
        "new Rule(this, 'Rule', {\n"
        "  eventPattern: {\n"
        "    source: ['com.shop.orders'],\n"
        "    detailType: ['OrderCreated'],\n"
        "  },\n"
        "});\n"
    )
    [c] = _extract(tmp_path, "rule.ts", src)
    assert (c.contract_id, c.role) == ("topic::com.shop.orders/ordercreated", "consumer")
    assert c.origin == ContractOrigin.ROUTE_REGISTRATION


def test_eventbridge_sam_yaml(tmp_path: Path) -> None:
    src = (
        "OrderCreatedRule:\n"
        "  Type: EventBridgeRule\n"
        "  Properties:\n"
        "    Pattern:\n"
        "      source:\n"
        "        - com.shop.orders\n"
        "      detail-type:\n"
        "        - OrderCreated\n"
    )
    [c] = _extract(tmp_path, "template.yaml", src)
    assert (c.contract_id, c.role) == ("topic::com.shop.orders/ordercreated", "consumer")
    assert c.origin == ContractOrigin.ROUTE_REGISTRATION


def test_eventbridge_links_across_repos(tmp_path: Path) -> None:
    pub = _extract(
        tmp_path / "pub",
        "send.py",
        "import boto3\n"
        "boto3.client('events').put_events(Entries=[{'Source': 'com.shop.orders', 'DetailType': 'OrderCreated'}])\n",
        "pub",
    )
    sub = _extract(
        tmp_path / "sub",
        "rule.ts",
        "new Rule(this, 'R', { eventPattern: { source: ['com.shop.orders'], detailType: ['OrderCreated'] } });\n",
        "sub",
    )
    links = [lk for lk in match_contracts(pub + sub) if lk.contract_type == "topic"]
    assert [(lk.provider_repo, lk.consumer_repo, lk.contract_id) for lk in links] == [
        ("pub", "sub", "topic::com.shop.orders/ordercreated")
    ]


def test_eventbridge_isolated_from_sqs_and_sns(tmp_path: Path) -> None:
    eb = _extract(
        tmp_path / "eb",
        "send.py",
        "boto3.client('events').put_events(Entries=[{'Source': 'orders', 'DetailType': 'created'}])\n",
        "eb",
    )
    sns = _extract(
        tmp_path / "sns",
        "pub.py",
        "boto3.client('sns').publish(TopicArn='arn:aws:sns:eu-west-1:123:orders/created')\n",
        "sns",
    )
    assert [lk for lk in match_contracts(eb + sns) if lk.contract_type == "topic"] == []


def test_commented_out_eventbridge_call_is_skipped(tmp_path: Path) -> None:
    src = (
        "// client.putEvents({ Entries: [{ Source: 'com.shop.orders', DetailType: 'OrderCreated' }] });\n"
    )
    assert _extract(tmp_path, "c.ts", src) == []
