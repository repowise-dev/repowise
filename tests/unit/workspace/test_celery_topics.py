"""Celery task topic dialect: task definitions, send_task, .delay() and .apply_async()."""

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


def test_celery_task_explicit_name(tmp_path: Path) -> None:
    src = (
        "from celery import shared_task\n\n"
        '@shared_task(name="orders.create_order")\n'
        "def create_order(order_id):\n"
        "    return order_id\n"
    )
    [c] = _extract(tmp_path, "tasks.py", src)
    assert (c.contract_id, c.role) == ("topic::orders.create_order", "consumer")
    assert c.meta == {"topic": "orders.create_order", "broker": "celery", "kind": "queue"}
    assert c.origin == ContractOrigin.ROUTE_REGISTRATION


def test_celery_task_convention_name(tmp_path: Path) -> None:
    src = (
        "from celery import Celery\n\n"
        "app = Celery('proj')\n\n"
        "@app.task\n"
        "def process_payment(amount):\n"
        "    pass\n"
    )
    [c] = _extract(tmp_path, "payments.py", src)
    assert (c.contract_id, c.role) == ("topic::payments.process_payment", "consumer")
    assert c.origin == ContractOrigin.NAME_CONVENTION


def test_celery_send_task_provider(tmp_path: Path) -> None:
    src = (
        "from celery import current_app\n\n"
        "def trigger():\n"
        '    current_app.send_task("orders.create_order", args=[123])\n'
    )
    [c] = _extract(tmp_path, "views.py", src)
    assert (c.contract_id, c.role) == ("topic::orders.create_order", "provider")
    assert c.origin == ContractOrigin.CLIENT_CALL


def test_celery_delay_and_apply_async_providers(tmp_path: Path) -> None:
    src = (
        "from myapp.tasks import create_order, process_payment\n\n"
        "def run():\n"
        "    create_order.delay(42)\n"
        "    process_payment.apply_async(args=[100], countdown=5)\n"
    )
    rows = _extract(tmp_path, "client.py", src)
    assert _ids(rows, "provider") == ["topic::create_order", "topic::process_payment"]


def test_celery_links_delay_call_to_task_across_repos(tmp_path: Path) -> None:
    worker = _extract(
        tmp_path / "worker",
        "tasks.py",
        "from celery import shared_task\n\n"
        '@shared_task(name="orders.create_order")\n'
        "def create_order(x):\n"
        "    pass\n",
        "worker",
    )
    web = _extract(
        tmp_path / "web",
        "views.py",
        "from tasks import create_order\n\n"
        "def submit():\n"
        "    create_order.delay(10)\n",
        "web",
    )
    links = [lk for lk in match_contracts(worker + web) if lk.contract_type == "topic"]
    assert [(lk.provider_repo, lk.consumer_repo, lk.contract_id) for lk in links] == [
        ("web", "worker", "topic::create_order")
    ]


def test_celery_does_not_meet_kafka_or_bullmq_topic(tmp_path: Path) -> None:
    celery = _extract(
        tmp_path / "c",
        "tasks.py",
        "from celery import shared_task\n\n"
        '@shared_task(name="orders")\n'
        "def f(): pass\n",
        "c",
    )
    bull = _extract(
        tmp_path / "b",
        "producer.ts",
        "import { Queue } from 'bullmq';\nconst q = new Queue('orders');\n",
        "b",
    )
    assert [lk for lk in match_contracts(celery + bull) if lk.contract_type == "topic"] == []


def test_commented_out_celery_task_is_not_read(tmp_path: Path) -> None:
    src = (
        "from celery import shared_task\n\n"
        '# @shared_task(name="commented.task")\n'
        "# def commented(): pass\n"
    )
    assert _extract(tmp_path, "t.py", src) == []
