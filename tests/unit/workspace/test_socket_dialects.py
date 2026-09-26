"""Socket dialects: socket.io events, websocket endpoints, and broadcast channels."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.extractors.socket import SocketExtractor
from repowise.core.workspace.matching import match_contracts


def _extract(tmp_path: Path, files: dict[str, str], alias: str = "svc") -> list[Contract]:
    root = tmp_path / alias
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return SocketExtractor().extract(root, alias)


def _ids(contracts: list[Contract]) -> set[tuple[str, str]]:
    return {(c.role, c.contract_id) for c in contracts}


_SERVER = "import { Server } from 'socket.io';\n"
_CLIENT = "import { io } from 'socket.io-client';\n"


class TestSocketIo:
    def test_server_and_client_events(self, tmp_path: Path) -> None:
        server = _SERVER + (
            "io.on('connection', (socket) => {\n"
            "  socket.on('chat message', (m) => io.emit('chat message', m));\n"
            "  socket.to(room).emit('typing', u);\n"
            "});\n"
        )
        assert _ids(_extract(tmp_path, {"server.ts": server})) == {
            ("consumer", "socket::/#chat message"),
            ("provider", "socket::/#chat message"),
            ("provider", "socket::/#typing"),
        }

    def test_lifecycle_events_are_not_contracts(self, tmp_path: Path) -> None:
        src = _CLIENT + "const socket = io();\nsocket.on('connect', f);\nsocket.on('disconnect', g);\n"
        assert _extract(tmp_path, {"c.ts": src}) == []

    def test_an_event_emitter_is_not_a_socket(self, tmp_path: Path) -> None:
        src = _SERVER + "emitter.on('ready', f);\nprocess.on('SIGTERM', g);\nbus.emit('tick');\n"
        assert _extract(tmp_path, {"s.ts": src}) == []

    def test_another_librarys_client_or_server_is_not_a_socket(self, tmp_path: Path) -> None:
        src = _SERVER + (
            "const client = mqtt.connect(url);\nclient.on('message', f);\n"
            "server.on('request', g);\n"
        )
        assert _extract(tmp_path, {"s.ts": src}) == []

    def test_a_client_bound_to_socket_io_is_a_socket(self, tmp_path: Path) -> None:
        src = _CLIENT + "const client = io('/');\nclient.on('stats', f);\n"
        assert _ids(_extract(tmp_path, {"c.ts": src})) == {("consumer", "socket::/#stats")}

    def test_a_client_url_with_a_base_names_its_namespace(self, tmp_path: Path) -> None:
        src = _CLIENT + "const socket = io(API_URL + '/chat');\nsocket.on('message', f);\n"
        assert _ids(_extract(tmp_path, {"c.ts": src})) == {("consumer", "socket::/chat#message")}

    def test_io_connect_names_its_namespace(self, tmp_path: Path) -> None:
        src = _CLIENT + "const socket = io.connect(`${base}/chat`);\nsocket.on('message', f);\n"
        assert _ids(_extract(tmp_path, {"c.ts": src})) == {("consumer", "socket::/chat#message")}

    def test_an_unreadable_server_namespace_refuses_unchained_events(self, tmp_path: Path) -> None:
        src = _SERVER + "const nsp = io.of(name);\nnsp.on('connection', (socket) => socket.emit('x'));\n"
        assert _extract(tmp_path, {"s.ts": src}) == []

    def test_nothing_without_socket_io(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, {"s.ts": "socket.on('chat', f);\n"}) == []

    def test_a_chain_of_listeners_shares_its_receiver(self, tmp_path: Path) -> None:
        src = _CLIENT + (
            "const websocket = io({ path: '/api/socket.io' });\n"
            "websocket\n  .on('connect', f)\n  .on('on_upload', g)\n  ?.on('on_delete', h);\n"
        )
        assert _ids(_extract(tmp_path, {"ws.ts": src})) == {
            ("consumer", "socket::/#on_upload"),
            ("consumer", "socket::/#on_delete"),
        }

    def test_the_client_url_names_the_namespace(self, tmp_path: Path) -> None:
        src = _CLIENT + "const s = io('https://api.example.com/admin');\ns.on('stats', f);\n"
        assert _ids(_extract(tmp_path, {"c.ts": src})) == {("consumer", "socket::/admin#stats")}

    def test_an_unreadable_client_url_is_the_default_namespace(self, tmp_path: Path) -> None:
        src = _CLIENT + "const socket = io(process.env.API_URL);\nsocket.on('stats', f);\n"
        assert _ids(_extract(tmp_path, {"c.ts": src})) == {("consumer", "socket::/#stats")}

    def test_a_server_namespace(self, tmp_path: Path) -> None:
        src = _SERVER + "const admin = io.of('/admin');\nadmin.on('connection', (socket) => socket.emit('stats', s));\n"
        assert _ids(_extract(tmp_path, {"s.ts": src})) == {("provider", "socket::/admin#stats")}

    def test_default_beside_a_named_namespace_reads_only_chained_events(self, tmp_path: Path) -> None:
        src = _SERVER + (
            "io.of('/admin').emit('stats', s);\n"
            "io.on('connection', (socket) => socket.emit('hello'));\n"
        )
        assert _ids(_extract(tmp_path, {"s.ts": src})) == {("provider", "socket::/admin#stats")}

    def test_server_to_client_link(self, tmp_path: Path) -> None:
        server = _extract(tmp_path, {"s.ts": _SERVER + "io?.emit('order.ready', o);\n"}, "api")
        client = _extract(tmp_path, {"c.ts": _CLIENT + "socket.on('order.ready', f);\n"}, "web")
        links = match_contracts(server + client)
        assert [(lk.provider_repo, lk.consumer_repo, lk.contract_id) for lk in links] == [
            ("api", "web", "socket::/#order.ready")
        ]


class TestNestGateways:
    _IMPORT = "import { WebSocketGateway, SubscribeMessage } from '@nestjs/websockets';\n"

    def test_subscribe_message_in_the_gateway_namespace(self, tmp_path: Path) -> None:
        src = self._IMPORT + (
            "@WebSocketGateway({ namespace: 'chat' })\nexport class ChatGateway {\n"
            "  @SubscribeMessage('send')\n  handle(client) { client.emit('sent', 1); }\n}\n"
        )
        assert _ids(_extract(tmp_path, {"g.ts": src})) == {
            ("consumer", "socket::/chat#send"),
            ("provider", "socket::/chat#sent"),
        }

    def test_a_gateway_path_is_an_endpoint(self, tmp_path: Path) -> None:
        src = self._IMPORT + "@WebSocketGateway({ path: '/ws' })\nexport class G {}\n"
        assert _ids(_extract(tmp_path, {"g.ts": src})) == {("provider", "socket::/ws")}


class TestPortedEndpoints:
    """The C# and FastAPI shapes, now read through the shared string resolver."""

    @pytest.mark.parametrize(
        "src",
        [
            '@router.websocket(path="/ws/feed")\nasync def f(): ...\n',
            'WS = "/ws/feed"\n@app.websocket(WS)\nasync def f(): ...\n',
        ],
    )
    def test_fastapi_keyword_and_constant(self, tmp_path: Path, src: str) -> None:
        assert _ids(_extract(tmp_path, {"ws.py": src})) == {("provider", "socket::/ws/feed")}

    @pytest.mark.parametrize(
        "src",
        [
            'await ws.ConnectAsync(new Uri(@"wss://h/hubs/game"), t);',
            'await ws.ConnectAsync(new Uri($@"{b}/hubs/game"), t);',
            'app.MapHub<GameHub>("/hubs/game", o => { o.X = 1; });',
            'new HubConnectionBuilder()\n  .WithUrl(\n    "wss://h/hubs/game",\n    opts => {})',
        ],
    )
    def test_csharp_spellings(self, tmp_path: Path, src: str) -> None:
        rows = _extract(tmp_path, {"S.cs": src})
        assert {c.contract_id for c in rows} == {"socket::/hubs/game"}

    def test_csharp_escaped_braces_are_refused(self, tmp_path: Path) -> None:
        src = 'var c = new HubConnectionBuilder().WithUrl($"{b}/hubs/{{x}}");'
        assert _extract(tmp_path, {"S.cs": src}) == []


class TestWebsocketEndpoints:
    def test_ws_server_and_browser_client(self, tmp_path: Path) -> None:
        server = _extract(
            tmp_path,
            {"s.ts": "import { WebSocketServer } from 'ws';\nnew WebSocketServer({ server, path: '/live' });\n"},
            "api",
        )
        client = _extract(
            tmp_path, {"c.ts": "const ws = new WebSocket(`wss://${host}/live`);\n"}, "web"
        )
        assert _ids(server) == {("provider", "socket::/live")}
        assert _ids(client) == {("consumer", "socket::/live")}
        assert len(match_contracts(server + client)) == 1

    def test_a_url_constant_folds(self, tmp_path: Path) -> None:
        src = "const URL = 'wss://example.com/feed';\nconst ws = new WebSocket(URL);\n"
        assert _ids(_extract(tmp_path, {"c.ts": src})) == {("consumer", "socket::/feed")}


_EVENT_USES = "use Illuminate\\Broadcasting\\PrivateChannel;\nuse Illuminate\\Contracts\\Broadcasting\\ShouldBroadcast;\n"


def _event(body: str, name: str = "OrderShipped") -> str:
    return (
        f"<?php\n\nnamespace App\\Events;\n\n{_EVENT_USES}\n"
        f"class {name} implements ShouldBroadcast\n{{\n{body}\n}}\n"
    )


_ON_ORDER = (
    "    public function broadcastOn(): array\n"
    "    {\n        return [new PrivateChannel('orders.'.$this->order->id)];\n    }\n"
)


class TestLaravelBroadcasting:
    def test_an_event_class_is_broadcast_under_its_class_name(self, tmp_path: Path) -> None:
        rows = _extract(tmp_path, {"app/Events/OrderShipped.php": _event(_ON_ORDER)})
        assert _ids(rows) == {
            ("provider", "socket::private-orders.{param}#App\\Events\\OrderShipped")
        }
        assert rows[0].meta == {
            "scope": "private-orders.{param}",
            "event": "App\\Events\\OrderShipped",
            "transport": "pusher",
        }

    def test_broadcast_as_renames_the_event(self, tmp_path: Path) -> None:
        body = _ON_ORDER + "    public function broadcastAs(): string { return 'order.shipped'; }\n"
        rows = _extract(tmp_path, {"app/Events/OrderShipped.php": _event(body)})
        assert _ids(rows) == {("provider", "socket::private-orders.{param}#order.shipped")}

    @pytest.mark.parametrize(
        ("listen", "event"),
        [
            ("Echo.private(`orders.${id}`).listen('OrderShipped', f)", "App\\Events\\OrderShipped"),
            ("window.Echo.private('orders.' + id).listen('.order.shipped', f)", "order.shipped"),
            ("useEcho(`orders.${id}`, 'OrderShipped', f)", "App\\Events\\OrderShipped"),
            ("useEcho(`orders.${id}`, ['.order.shipped', 'Other'], f)", "order.shipped"),
        ],
    )
    def test_echo_listeners(self, tmp_path: Path, listen: str, event: str) -> None:
        rows = _extract(tmp_path, {"app.ts": f"import {{ useEcho }} from '@laravel/echo-react';\n{listen};\n"})
        assert ("consumer", f"socket::private-orders.{{param}}#{event}") in _ids(rows)

    def test_presence_and_public_channels(self, tmp_path: Path) -> None:
        src = "Echo.join('room.1').listen('.moved', f);\nEcho.channel('news').listen('.posted', g);\n"
        assert _ids(_extract(tmp_path, {"a.js": src})) == {
            ("consumer", "socket::presence-room.1#moved"),
            ("consumer", "socket::news#posted"),
        }

    def test_the_event_links_to_its_listener(self, tmp_path: Path) -> None:
        server = _extract(tmp_path, {"app/Events/OrderShipped.php": _event(_ON_ORDER)}, "api")
        client = _extract(
            tmp_path, {"a.ts": "Echo.private(`orders.${order.id}`).listen('OrderShipped', f);\n"}, "web"
        )
        links = match_contracts(server + client)
        assert [(lk.provider_repo, lk.consumer_repo) for lk in links] == [("api", "web")]

    def test_an_escaped_fully_qualified_echo_name(self, tmp_path: Path) -> None:
        src = "Echo.private(`orders.${id}`).listen('.App\\\\Events\\\\OrderShipped', f);\n"
        assert _ids(_extract(tmp_path, {"a.ts": src})) == {
            ("consumer", "socket::private-orders.{param}#App\\Events\\OrderShipped")
        }

    def test_a_notification_is_not_an_event(self, tmp_path: Path) -> None:
        src = _event(_ON_ORDER, "OrderShippedNotice").replace(
            "implements ShouldBroadcast", "extends Notification"
        )
        assert _extract(tmp_path, {"app/Notifications/N.php": src}) == []

    def test_anonymous_broadcast(self, tmp_path: Path) -> None:
        src = "<?php\nBroadcast::private('orders.'.$id)->as('OrderPlaced')->with($o)->send();\n"
        assert _ids(_extract(tmp_path, {"S.php": src})) == {
            ("provider", "socket::private-orders.{param}#OrderPlaced")
        }


class TestPusher:
    def test_trigger_and_subscribe(self, tmp_path: Path) -> None:
        php = "<?php\nuse Pusher\\Pusher;\n$pusher->trigger('private-feed', 'item.added', $data);\n"
        js = (
            "import Pusher from 'pusher-js';\n"
            "const channel = pusher.subscribe('private-feed');\n"
            "channel.bind('item.added', f);\nmychannel.bind('other', g);\n"
        )
        provider = _extract(tmp_path, {"P.php": php}, "api")
        consumer = _extract(tmp_path, {"c.ts": js}, "web")
        assert _ids(provider) == {("provider", "socket::private-feed#item.added")}
        assert _ids(consumer) == {("consumer", "socket::private-feed#item.added")}

    def test_a_reused_variable_keeps_each_subscriptions_events(self, tmp_path: Path) -> None:
        js = (
            "import Pusher from 'pusher-js';\n"
            "function a() { const channel = pusher.subscribe('orders'); channel.bind('Shipped', f); }\n"
            "function b() { const channel = pusher.subscribe('users'); channel.bind('Joined', g); }\n"
        )
        assert _ids(_extract(tmp_path, {"c.ts": js})) == {
            ("consumer", "socket::orders#Shipped"),
            ("consumer", "socket::users#Joined"),
        }

    def test_a_chained_bind(self, tmp_path: Path) -> None:
        js = "import Pusher from 'pusher-js';\npusher.subscribe('feed').bind('added', f);\n"
        assert _ids(_extract(tmp_path, {"c.ts": js})) == {("consumer", "socket::feed#added")}
