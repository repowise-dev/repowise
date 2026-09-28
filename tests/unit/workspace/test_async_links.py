"""Async contracts end to end: extracted in one repo, linked to another by the matcher."""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.extractors import SocketExtractor, TopicExtractor
from repowise.core.workspace.matching import match_contracts


def _repo(tmp_path: Path, alias: str, files: dict[str, str]) -> list[Contract]:
    root = tmp_path / alias
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return TopicExtractor().extract(root, alias) + SocketExtractor().extract(root, alias)


def _links(*repos: list[Contract]) -> set[tuple[str, str, str]]:
    return {
        (link.provider_repo, link.consumer_repo, link.contract_id)
        for link in match_contracts([c for repo in repos for c in repo])
    }


_JOB = (
    "<?php\nnamespace App\\Jobs;\nuse Illuminate\\Contracts\\Queue\\ShouldQueue;\n"
    "class NotifyTicketSold implements ShouldQueue\n{\n    public function handle() {}\n}\n"
)
_CONTROLLER = (
    "<?php\nnamespace App\\Http\\Controllers;\nuse App\\Jobs\\NotifyTicketSold;\n"
    "class TicketController\n{\n    public function store($t)\n    {\n"
    "        NotifyTicketSold::dispatch($t)->onQueue('ticket.sold');\n    }\n}\n"
)
_AMQP = "import amqp from 'amqplib';\n"


def test_a_laravel_dispatch_reaches_a_node_consumer(tmp_path: Path) -> None:
    api = _repo(tmp_path, "api", {"app/Jobs/NotifyTicketSold.php": _JOB, "app/Http/C.php": _CONTROLLER})
    worker = _repo(tmp_path, "worker", {"w.ts": _AMQP + "channel.consume('ticket.sold', h);\n"})
    assert _links(api, worker) == {("api", "worker", "topic::ticket.sold")}


def test_sqs_links_across_languages(tmp_path: Path) -> None:
    api = _repo(
        tmp_path,
        "api",
        {"q.ts": "new SendMessageCommand({ QueueUrl: 'https://sqs.r.amazonaws.com/1/orders' });\n"},
    )
    worker = _repo(
        tmp_path, "worker", {"w.py": "sqs.receive_message(QueueUrl=f'{BASE}/orders')\n"}
    )
    assert _links(api, worker) == {("api", "worker", "topic::orders")}


def test_bullmq_queue_to_worker(tmp_path: Path) -> None:
    api = _repo(tmp_path, "api", {"q.ts": "import { Queue } from 'bullmq';\nnew Queue('emails');\n"})
    worker = _repo(
        tmp_path, "worker", {"w.ts": "import { Worker } from 'bullmq';\nnew Worker('emails', f);\n"}
    )
    assert _links(api, worker) == {("api", "worker", "topic::emails")}


def test_a_bullmq_queue_is_not_a_laravel_queue_of_the_same_name(tmp_path: Path) -> None:
    api = _repo(
        tmp_path,
        "api",
        {"C.php": "<?php\nuse App\\Jobs\\Mail;\nMail::dispatch()->onQueue('emails');\n"},
    )
    worker = _repo(
        tmp_path, "worker", {"w.ts": "import { Worker } from 'bullmq';\nnew Worker('emails', f);\n"}
    )
    assert _links(api, worker) == set()


def test_a_nats_wildcard_subscription(tmp_path: Path) -> None:
    api = _repo(tmp_path, "api", {"p.go": 'nc.Publish("orders.created", d)\n'})
    audit = _repo(tmp_path, "audit", {"s.ts": "nc.subscribe('orders.>');\n"})
    assert _links(api, audit) == {("api", "audit", "topic::orders.created")}


def test_a_redis_pattern_subscription(tmp_path: Path) -> None:
    api = _repo(tmp_path, "api", {"p.py": "import redis\nr.publish('scores.eu', x)\n"})
    board = _repo(tmp_path, "board", {"s.ts": "import Redis from 'ioredis';\nsub.psubscribe('scores.*');\n"})
    assert _links(api, board) == {("api", "board", "topic::scores.eu")}


def test_php_amqplib_publish_reaches_an_amqplib_queue_through_a_binding(tmp_path: Path) -> None:
    api = _repo(
        tmp_path,
        "api",
        {"P.php": "<?php\nuse PhpAmqpLib\\Message\\AMQPMessage;\n$ch->basic_publish($m, 'orders', 'order.created');\n"},
    )
    audit = _repo(
        tmp_path,
        "audit",
        {"w.ts": _AMQP + "ch.bindQueue('audit', 'orders', 'order.*');\nch.consume('audit', h);\n"},
    )
    assert _links(api, audit) == {("api", "audit", "topic::orders")}


def test_a_nestjs_object_pattern_across_repos(tmp_path: Path) -> None:
    imp = "import { ClientProxy, MessagePattern } from '@nestjs/microservices';\n"
    api = _repo(
        tmp_path,
        "api",
        {"c.ts": imp + "client: ClientProxy;\nthis.client.send<number>({ role: 'math', cmd: 'sum' }, [1]);\n"},
    )
    math = _repo(tmp_path, "math", {"m.ts": imp + "@MessagePattern({ cmd: 'sum', role: 'math' })\nf() {}\n"})
    assert _links(api, math) == {("api", "math", 'topic::{"cmd":"sum","role":"math"}')}


def test_fastapi_websocket_to_browser(tmp_path: Path) -> None:
    api = _repo(tmp_path, "api", {"ws.py": '@app.websocket("/ws/chat/{room_id}")\nasync def chat(): ...\n'})
    web = _repo(tmp_path, "web", {"c.ts": "new WebSocket(`wss://${host}/ws/chat/${room}`);\n"})
    assert _links(api, web) == {("api", "web", "socket::/ws/chat/{param}")}


def test_a_socket_io_namespace_across_repos(tmp_path: Path) -> None:
    api = _repo(
        tmp_path, "api", {"s.ts": "import { Server } from 'socket.io';\nio.of('/admin').emit('stats', s);\n"}
    )
    web = _repo(
        tmp_path,
        "web",
        {"c.ts": "import { io } from 'socket.io-client';\nconst socket = io('https://h/admin');\nsocket.on('stats', f);\n"},
    )
    root = _repo(
        tmp_path,
        "other",
        {"c.ts": "import { io } from 'socket.io-client';\nconst socket = io();\nsocket.on('stats', f);\n"},
    )
    assert _links(api, web, root) == {("api", "web", "socket::/admin#stats")}


def test_an_anonymous_broadcast_reaches_echo(tmp_path: Path) -> None:
    api = _repo(tmp_path, "api", {"S.php": "<?php\nBroadcast::on('news')->send();\n"})
    web = _repo(tmp_path, "web", {"a.js": "Echo.channel('news').listen('.AnonymousEvent', f);\n"})
    assert _links(api, web) == {("api", "web", "socket::news#AnonymousEvent")}
