"""Tests for contract extraction — HTTP, gRPC, topic extractors, matching, persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.workspace.contracts import (
    CONTRACTS_FILENAME,
    Contract,
    ContractLink,
    ContractStore,
    load_contract_store,
    match_contracts,
    normalize_contract_id,
    save_contract_store,
)
from repowise.core.workspace.extractors.http_extractor import (
    HttpExtractor,
    normalize_http_path,
)
from repowise.core.workspace.extractors.grpc_extractor import (
    GrpcExtractor,
    _extract_service_blocks,
)
from repowise.core.workspace.extractors.topic_extractor import TopicExtractor
from repowise.core.workspace.extractors.service_boundary import (
    ServiceBoundary,
    assign_service,
    detect_service_boundaries,
)


# ---------------------------------------------------------------------------
# normalize_http_path
# ---------------------------------------------------------------------------


class TestNormalizeHttpPath:
    def test_express_param(self) -> None:
        assert normalize_http_path("/api/users/:id") == "/api/users/{param}"

    def test_spring_param(self) -> None:
        assert normalize_http_path("/api/users/{userId}") == "/api/users/{param}"

    def test_nextjs_param(self) -> None:
        assert normalize_http_path("/api/users/[id]") == "/api/users/{param}"

    def test_query_string_stripped(self) -> None:
        assert normalize_http_path("/api/users?page=1") == "/api/users"

    def test_trailing_slash_stripped(self) -> None:
        assert normalize_http_path("/api/users/") == "/api/users"

    def test_case_lowered(self) -> None:
        assert normalize_http_path("/Api/Users") == "/api/users"

    def test_multiple_params(self) -> None:
        assert normalize_http_path("/api/:org/repos/:id") == "/api/{param}/repos/{param}"

    def test_root_path(self) -> None:
        assert normalize_http_path("/") == "/"


# ---------------------------------------------------------------------------
# HttpExtractor
# ---------------------------------------------------------------------------


class TestHttpExtractor:
    def _write_file(self, repo: Path, rel: str, content: str) -> None:
        fpath = repo / rel
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")

    def test_express_provider(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/routes.js", """
            const router = require('express').Router();
            router.get('/api/users', handler);
            router.post('/api/users', createHandler);
        """)
        contracts = HttpExtractor().extract(tmp_path, "backend")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 2
        ids = {c.contract_id for c in providers}
        assert "http::GET::/api/users" in ids
        assert "http::POST::/api/users" in ids

    def test_fastapi_provider(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "main.py", """
            @app.get("/api/items/{item_id}")
            async def get_item(item_id: int): ...
        """)
        contracts = HttpExtractor().extract(tmp_path, "api")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1
        assert providers[0].contract_id == "http::GET::/api/items/{param}"

    def test_spring_with_class_prefix(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "UserController.java", """
            @RequestMapping("/api/v1")
            @RestController
            public class UserController {
                @GetMapping("/users")
                public List<User> list() { ... }
            }
        """)
        contracts = HttpExtractor().extract(tmp_path, "backend")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1
        assert providers[0].contract_id == "http::GET::/api/v1/users"

    def test_spring_multiple_classes_correct_prefix(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "MultiController.java", """
            @RequestMapping("/api/v1")
            @RestController
            public class UserController {
                @GetMapping("/users")
                public List<User> list() { ... }
            }

            @RequestMapping("/internal")
            @RestController
            public class AdminController {
                @PostMapping("/admin")
                public void doAdmin() { ... }
            }
        """)
        contracts = HttpExtractor().extract(tmp_path, "backend")
        providers = [c for c in contracts if c.role == "provider"]
        ids = {c.contract_id for c in providers}
        assert "http::GET::/api/v1/users" in ids
        assert "http::POST::/internal/admin" in ids
        # The inner class prefix must NOT be applied to the first class's methods
        assert "http::GET::/internal/users" not in ids

    def test_laravel_provider(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "routes/web.php", """
            Route::delete('/posts/{id}', [PostController::class, 'destroy']);
        """)
        contracts = HttpExtractor().extract(tmp_path, "app")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1
        assert providers[0].contract_id == "http::DELETE::/posts/{param}"

    def test_go_provider(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "main.go", """
            r.GET("/health", healthHandler)
        """)
        contracts = HttpExtractor().extract(tmp_path, "svc")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1
        assert providers[0].contract_id == "http::GET::/health"

    def test_fetch_consumer(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/api.ts", """
            const users = await fetch('/api/users');
        """)
        contracts = HttpExtractor().extract(tmp_path, "frontend")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        assert consumers[0].contract_id == "http::GET::/api/users"

    def test_fetch_with_method_consumer(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/api.ts", """
            await fetch('/api/users', { method: 'POST', body: data });
        """)
        contracts = HttpExtractor().extract(tmp_path, "frontend")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        assert consumers[0].contract_id == "http::POST::/api/users"

    def test_axios_consumer(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/client.js", """
            const items = await axios.get('/api/items');
        """)
        contracts = HttpExtractor().extract(tmp_path, "frontend")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        assert consumers[0].contract_id == "http::GET::/api/items"

    def test_requests_consumer(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "client.py", """
            resp = requests.post('http://backend:8000/api/data')
        """)
        contracts = HttpExtractor().extract(tmp_path, "worker")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        assert consumers[0].contract_id == "http::POST::/api/data"

    def test_empty_file(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "empty.py", "")
        contracts = HttpExtractor().extract(tmp_path, "svc")
        assert contracts == []

    def test_no_patterns(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "utils.py", "def add(a, b): return a + b")
        contracts = HttpExtractor().extract(tmp_path, "svc")
        assert contracts == []


# ---------------------------------------------------------------------------
# GrpcExtractor
# ---------------------------------------------------------------------------


class TestGrpcExtractor:
    def _write_file(self, repo: Path, rel: str, content: str) -> None:
        fpath = repo / rel
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")

    def test_proto_parsing(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "auth.proto", """
            syntax = "proto3";
            package auth;

            service AuthService {
                rpc Login(LoginRequest) returns (LoginResponse);
                rpc Logout(LogoutRequest) returns (Empty);
            }
        """)
        contracts = GrpcExtractor().extract(tmp_path, "auth")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 2
        ids = {c.contract_id for c in providers}
        assert "grpc::auth.AuthService/Login" in ids
        assert "grpc::auth.AuthService/Logout" in ids

    def test_proto_multiple_services(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "api.proto", """
            syntax = "proto3";
            package api;

            service UserService {
                rpc GetUser(GetUserReq) returns (User);
            }

            service OrderService {
                rpc CreateOrder(CreateOrderReq) returns (Order);
            }
        """)
        contracts = GrpcExtractor().extract(tmp_path, "api")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 2

    def test_proto_nested_braces(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "svc.proto", """
            syntax = "proto3";
            package svc;

            service MyService {
                rpc DoThing(Req) returns (Resp) {
                    option (google.api.http) = {
                        post: "/api/thing"
                        body: "*"
                    };
                }
            }
        """)
        contracts = GrpcExtractor().extract(tmp_path, "svc")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1
        assert providers[0].contract_id == "grpc::svc.MyService/DoThing"

    def test_go_provider(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "server.go", """
            pb.RegisterPaymentServer(grpcServer, &impl{})
        """)
        contracts = GrpcExtractor().extract(tmp_path, "payment")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1
        assert providers[0].contract_id == "grpc::Payment/*"

    def test_go_consumer(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "client.go", """
            client := pb.NewPaymentClient(conn)
        """)
        contracts = GrpcExtractor().extract(tmp_path, "gateway")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        assert consumers[0].contract_id == "grpc::Payment/*"

    def test_java_provider(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "AuthImpl.java", """
            @GrpcService
            public class AuthImpl extends AuthGrpc.AuthImplBase {
                @Override
                public void login(LoginRequest req, StreamObserver<LoginReply> resp) {}
            }
        """)
        contracts = GrpcExtractor().extract(tmp_path, "auth")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1
        assert providers[0].contract_id == "grpc::Auth/*"

    def test_java_consumer(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "Client.java", """
            var stub = AuthGrpc.newBlockingStub(channel);
        """)
        contracts = GrpcExtractor().extract(tmp_path, "gateway")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        assert consumers[0].contract_id == "grpc::Auth/*"

    def test_python_provider(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "server.py", """
            add_GreeterServicer_to_server(GreeterServicer(), server)
        """)
        contracts = GrpcExtractor().extract(tmp_path, "greeter")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1
        assert providers[0].contract_id == "grpc::Greeter/*"

    def test_python_consumer_filters_mock(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "test_client.py", """
            stub = MockGreeterStub(channel)
            real = GreeterStub(channel)
        """)
        contracts = GrpcExtractor().extract(tmp_path, "test")
        consumers = [c for c in contracts if c.role == "consumer"]
        # MockGreeterStub should be filtered, only GreeterStub remains
        assert len(consumers) == 1
        assert "Greeter" in consumers[0].contract_id

    def test_ts_provider(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "auth.controller.ts", """
            @GrpcMethod('AuthService', 'Login')
            login(data: LoginRequest): LoginResponse { ... }
        """)
        contracts = GrpcExtractor().extract(tmp_path, "auth")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1
        assert providers[0].contract_id == "grpc::AuthService/Login"


# ---------------------------------------------------------------------------
# TopicExtractor
# ---------------------------------------------------------------------------


class TestTopicExtractor:
    def _write_file(self, repo: Path, rel: str, content: str) -> None:
        fpath = repo / rel
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")

    def test_kafka_listener_consumer(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "Consumer.java", """
            @KafkaListener(topics = "orders")
            public void listen(String message) {}
        """)
        contracts = TopicExtractor().extract(tmp_path, "worker")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        assert consumers[0].contract_id == "topic::orders"
        assert consumers[0].meta["broker"] == "kafka"

    def test_kafka_template_provider(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "Publisher.java", """
            kafkaTemplate.send("orders", payload);
        """)
        contracts = TopicExtractor().extract(tmp_path, "api")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1
        assert providers[0].contract_id == "topic::orders"

    def test_kafka_node_provider(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "producer.js", """
            await producer.send({ topic: 'payments', messages });
        """)
        contracts = TopicExtractor().extract(tmp_path, "svc")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1
        assert providers[0].contract_id == "topic::payments"

    def test_rabbitmq_listener_consumer(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "Worker.java", """
            @RabbitListener(queues = "jobs")
            public void process(Message msg) {}
        """)
        contracts = TopicExtractor().extract(tmp_path, "worker")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        assert consumers[0].contract_id == "topic::jobs"
        assert consumers[0].meta["broker"] == "rabbitmq"

    def test_nats_subscribe_consumer(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "listener.go", """
            nc.Subscribe("events.created", handler)
        """)
        contracts = TopicExtractor().extract(tmp_path, "svc")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        assert consumers[0].contract_id == "topic::events.created"
        assert consumers[0].meta["broker"] == "nats"

    def test_nats_publish_provider(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "notifier.go", """
            nc.Publish("events.created", data)
        """)
        contracts = TopicExtractor().extract(tmp_path, "notifier")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1
        assert providers[0].contract_id == "topic::events.created"

    def test_dedup_same_pattern_in_file(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "multi.java", """
            kafkaTemplate.send("orders", payload1);
            kafkaTemplate.send("orders", payload2);
        """)
        contracts = TopicExtractor().extract(tmp_path, "svc")
        # Same topic, same role, same file → deduplicated
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1


# ---------------------------------------------------------------------------
# ServiceBoundary
# ---------------------------------------------------------------------------


class TestServiceBoundary:
    def test_detects_boundary(self, tmp_path: Path) -> None:
        svc = tmp_path / "services" / "auth"
        svc.mkdir(parents=True)
        (svc / "package.json").write_text("{}")
        (svc / "handler.ts").write_text("export function login() {}")

        boundaries = detect_service_boundaries(tmp_path)
        assert len(boundaries) == 1
        assert boundaries[0].service_path == "services/auth"
        assert boundaries[0].confidence == 0.75

    def test_root_excluded(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "app.ts").write_text("console.log('hi')")

        boundaries = detect_service_boundaries(tmp_path)
        assert len(boundaries) == 0

    def test_confidence_scoring(self, tmp_path: Path) -> None:
        svc = tmp_path / "svc"
        svc.mkdir()
        (svc / "package.json").write_text("{}")
        (svc / "Dockerfile").write_text("FROM node")
        (svc / "go.mod").write_text("module svc")
        (svc / "main.go").write_text("package main")

        boundaries = detect_service_boundaries(tmp_path)
        assert len(boundaries) == 1
        assert boundaries[0].confidence == 1.0

    def test_two_marker_confidence(self, tmp_path: Path) -> None:
        svc = tmp_path / "svc"
        svc.mkdir()
        (svc / "package.json").write_text("{}")
        (svc / "Dockerfile").write_text("FROM node")
        (svc / "index.ts").write_text("export default {}")

        boundaries = detect_service_boundaries(tmp_path)
        assert len(boundaries) == 1
        assert boundaries[0].confidence == 0.9

    def test_nested_services(self, tmp_path: Path) -> None:
        for name in ("auth", "billing"):
            svc = tmp_path / "services" / name
            svc.mkdir(parents=True)
            (svc / "package.json").write_text("{}")
            (svc / "index.ts").write_text("export {}")

        boundaries = detect_service_boundaries(tmp_path)
        assert len(boundaries) == 2
        paths = {b.service_path for b in boundaries}
        assert "services/auth" in paths
        assert "services/billing" in paths

    def test_assign_service_longest_prefix(self) -> None:
        boundaries = [
            ServiceBoundary(service_path="services", service_name="services"),
            ServiceBoundary(service_path="services/auth", service_name="auth"),
        ]
        assert assign_service("services/auth/handler.ts", boundaries) == "services/auth"

    def test_assign_service_root_file(self) -> None:
        boundaries = [
            ServiceBoundary(service_path="services/auth", service_name="auth"),
        ]
        assert assign_service("README.md", boundaries) is None


# ---------------------------------------------------------------------------
# normalize_contract_id
# ---------------------------------------------------------------------------


class TestNormalizeContractId:
    def test_http_normalizes(self) -> None:
        assert normalize_contract_id("http::get::/Api/Users") == "http::GET::/api/users"

    def test_http_strips_trailing_slash(self) -> None:
        assert normalize_contract_id("http::GET::/api/") == "http::GET::/api"

    def test_grpc_preserves_method_case(self) -> None:
        result = normalize_contract_id("grpc::PKG.Service/GetUser")
        assert result == "grpc::pkg.service/GetUser"

    def test_topic_lowercases(self) -> None:
        assert normalize_contract_id("topic::Orders") == "topic::orders"


# ---------------------------------------------------------------------------
# match_contracts
# ---------------------------------------------------------------------------


class TestMatchContracts:
    def _contract(self, **kwargs) -> Contract:
        defaults = {
            "repo": "a",
            "contract_id": "http::GET::/api",
            "contract_type": "http",
            "role": "provider",
            "file_path": "f.py",
            "symbol_name": "handler",
            "confidence": 0.85,
            "service": None,
            "meta": {},
        }
        defaults.update(kwargs)
        return Contract(**defaults)

    def test_exact_match_creates_link(self) -> None:
        contracts = [
            self._contract(repo="backend", role="provider", contract_id="http::GET::/api/users"),
            self._contract(repo="frontend", role="consumer", contract_id="http::GET::/api/users", file_path="client.ts"),
        ]
        links = match_contracts(contracts)
        assert len(links) == 1
        assert links[0].provider_repo == "backend"
        assert links[0].consumer_repo == "frontend"

    def test_same_repo_same_service_filtered(self) -> None:
        contracts = [
            self._contract(repo="mono", role="provider", service="svc-a"),
            self._contract(repo="mono", role="consumer", service="svc-a", file_path="c.py"),
        ]
        links = match_contracts(contracts)
        assert len(links) == 0

    def test_same_repo_different_service_allowed(self) -> None:
        contracts = [
            self._contract(repo="mono", role="provider", service="svc-a"),
            self._contract(repo="mono", role="consumer", service="svc-b", file_path="c.py"),
        ]
        links = match_contracts(contracts)
        assert len(links) == 1

    def test_http_wildcard_method(self) -> None:
        contracts = [
            self._contract(repo="backend", role="provider", contract_id="http::GET::/api/orders"),
            self._contract(repo="frontend", role="consumer", contract_id="http::*::/api/orders", file_path="c.ts"),
        ]
        links = match_contracts(contracts)
        assert len(links) == 1

    def test_grpc_wildcard_service(self) -> None:
        contracts = [
            self._contract(repo="auth", role="provider", contract_id="grpc::auth.AuthService/Login", contract_type="grpc"),
            self._contract(repo="gateway", role="consumer", contract_id="grpc::auth.AuthService/*", contract_type="grpc", file_path="gw.go"),
        ]
        links = match_contracts(contracts)
        assert len(links) == 1

    def test_no_match(self) -> None:
        contracts = [
            self._contract(repo="backend", role="provider", contract_id="http::GET::/api/users"),
            self._contract(repo="frontend", role="consumer", contract_id="http::GET::/api/orders", file_path="c.ts"),
        ]
        links = match_contracts(contracts)
        assert len(links) == 0

    def test_multiple_consumers_for_one_provider(self) -> None:
        contracts = [
            self._contract(repo="backend", role="provider", contract_id="http::GET::/api/users"),
            self._contract(repo="frontend", role="consumer", contract_id="http::GET::/api/users", file_path="c1.ts"),
            self._contract(repo="mobile", role="consumer", contract_id="http::GET::/api/users", file_path="c2.ts"),
        ]
        links = match_contracts(contracts)
        assert len(links) == 2


# ---------------------------------------------------------------------------
# ContractStore persistence
# ---------------------------------------------------------------------------


class TestContractStorePersistence:
    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        store = ContractStore(
            version=1,
            generated_at="2026-04-12T12:00:00Z",
            contracts=[
                Contract(
                    repo="backend",
                    contract_id="http::GET::/api/users",
                    contract_type="http",
                    role="provider",
                    file_path="routes.py",
                    symbol_name="get_users",
                    confidence=0.85,
                    service=None,
                    meta={"method": "GET"},
                ),
            ],
            contract_links=[
                ContractLink(
                    contract_id="http::GET::/api/users",
                    contract_type="http",
                    match_type="exact",
                    confidence=0.75,
                    provider_repo="backend",
                    provider_file="routes.py",
                    provider_symbol="get_users",
                    provider_service=None,
                    consumer_repo="frontend",
                    consumer_file="client.ts",
                    consumer_symbol="fetchUsers",
                    consumer_service=None,
                ),
            ],
        )
        save_contract_store(store, tmp_path)
        loaded = load_contract_store(tmp_path)
        assert loaded is not None
        assert loaded.version == 1
        assert len(loaded.contracts) == 1
        assert loaded.contracts[0].contract_id == "http::GET::/api/users"
        assert len(loaded.contract_links) == 1
        assert loaded.contract_links[0].provider_repo == "backend"

    def test_load_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_contract_store(tmp_path) is None

    def test_load_corrupt_json_returns_none(self, tmp_path: Path) -> None:
        data_dir = tmp_path / ".repowise-workspace"
        data_dir.mkdir()
        (data_dir / CONTRACTS_FILENAME).write_text("not json!!!")
        assert load_contract_store(tmp_path) is None

    def test_empty_store_round_trip(self, tmp_path: Path) -> None:
        store = ContractStore()
        save_contract_store(store, tmp_path)
        loaded = load_contract_store(tmp_path)
        assert loaded is not None
        assert loaded.contracts == []
        assert loaded.contract_links == []
