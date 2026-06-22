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
    annotate_consumer_targets,
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

    def test_fastapi_router_prefix_infile(self, tmp_path: Path) -> None:
        # APIRouter(prefix=...) must be stitched onto each decorator path.
        self._write_file(tmp_path, "routers/snapshots.py", """
            router = APIRouter(prefix="/snapshots", tags=["snap"])

            @router.get("/{snapshot_id}/symbols")
            async def symbols(snapshot_id: str): ...

            @router.post("/{snapshot_id}/chat")
            async def chat(snapshot_id: str): ...
        """)
        contracts = HttpExtractor().extract(tmp_path, "backend")
        ids = {c.contract_id for c in contracts if c.role == "provider"}
        assert "http::GET::/snapshots/{param}/symbols" in ids
        assert "http::POST::/snapshots/{param}/chat" in ids

    def test_fastapi_multiple_routers_distinct_prefixes(self, tmp_path: Path) -> None:
        # Each route uses its own router's prefix, not the first one in the file.
        self._write_file(tmp_path, "routers/multi.py", """
            repos_router = APIRouter(prefix="/repos")
            users_router = APIRouter(prefix="/users")

            @repos_router.get("/{repo_id}")
            async def repo(repo_id: str): ...

            @users_router.get("/me")
            async def me(): ...
        """)
        ids = {c.contract_id for c in HttpExtractor().extract(tmp_path, "backend") if c.role == "provider"}
        assert "http::GET::/repos/{param}" in ids
        assert "http::GET::/users/me" in ids

    def test_fastapi_dependency_paren_in_args(self, tmp_path: Path) -> None:
        # A nested call in the constructor args must not truncate the prefix scan.
        self._write_file(tmp_path, "routers/secure.py", """
            router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

            @router.get("/stats")
            async def stats(): ...
        """)
        ids = {c.contract_id for c in HttpExtractor().extract(tmp_path, "backend") if c.role == "provider"}
        assert "http::GET::/admin/stats" in ids

    def test_fastapi_unknown_decorator_not_a_route(self, tmp_path: Path) -> None:
        # @cache.get(...) on a non-router object must not become a contract.
        self._write_file(tmp_path, "service.py", """
            @cache.get("/some-key")
            def cached(): ...
        """)
        providers = [c for c in HttpExtractor().extract(tmp_path, "api") if c.role == "provider"]
        assert providers == []

    def test_fastapi_cross_file_include_router_prefix(self, tmp_path: Path) -> None:
        # include_router(items_router, prefix="/api") in main.py mounts a router
        # defined in another file under that prefix.
        self._write_file(tmp_path, "app/main.py", """
            from .items import items_router
            app = FastAPI()
            app.include_router(items_router, prefix="/api")
        """)
        self._write_file(tmp_path, "app/items.py", """
            items_router = APIRouter(prefix="/items")

            @items_router.get("/{item_id}")
            async def get_item(item_id: int): ...
        """)
        ids = {c.contract_id for c in HttpExtractor().extract(tmp_path, "backend") if c.role == "provider"}
        assert "http::GET::/api/items/{param}" in ids

    def test_express_cross_file_app_use_prefix(self, tmp_path: Path) -> None:
        # app.use('/api/users', userRouter) mounts a uniquely-named router.
        self._write_file(tmp_path, "src/app.js", """
            const userRouter = require('./users');
            app.use('/api/users', userRouter);
        """)
        self._write_file(tmp_path, "src/users.js", """
            const userRouter = express.Router();
            userRouter.get('/:id', handler);
        """)
        ids = {c.contract_id for c in HttpExtractor().extract(tmp_path, "backend") if c.role == "provider"}
        assert "http::GET::/api/users/{param}" in ids

    def test_go_route_group_nested(self, tmp_path: Path) -> None:
        # Nested groups compose: v1 := api.Group("/v1") under api := r.Group("/api").
        self._write_file(tmp_path, "main.go", """
            r := gin.Default()
            api := r.Group("/api")
            v1 := api.Group("/v1")
            v1.GET("/users", listUsers)
        """)
        ids = {c.contract_id for c in HttpExtractor().extract(tmp_path, "svc") if c.role == "provider"}
        assert "http::GET::/api/v1/users" in ids

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

    def test_fetch_consumer_strips_leading_base_expr(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/api.ts", """
            const users = await fetch(`${API_BASE}/api/users`);
        """)
        contracts = HttpExtractor().extract(tmp_path, "frontend")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        # The unresolved base placeholder is stripped, not turned into {param}.
        assert consumers[0].contract_id == "http::GET::/api/users"
        assert consumers[0].meta.get("base_stripped") is True

    def test_fetch_consumer_keeps_interior_param(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/api.ts", """
            const u = await fetch(`/users/${id}`);
        """)
        contracts = HttpExtractor().extract(tmp_path, "frontend")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        # Interior expressions are genuine path params and must remain.
        assert consumers[0].contract_id == "http::GET::/users/{param}"
        assert "base_stripped" not in consumers[0].meta

    def test_rust_axum_provider(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/main.rs", """
            let app = Router::new()
                .route("/path", post(find_path))
                .route("/systems/{id}", get(get_system))
                .route("/health", get(health));
        """)
        contracts = HttpExtractor().extract(tmp_path, "rust-svc")
        providers = [c for c in contracts if c.role == "provider"]
        ids = {c.contract_id for c in providers}
        assert "http::POST::/path" in ids
        assert "http::GET::/systems/{param}" in ids
        assert "http::GET::/health" in ids

    def test_rust_axum_chained_methods(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/routes.rs", """
            .route("/users", get(list_users).post(create_user))
        """)
        contracts = HttpExtractor().extract(tmp_path, "rust-svc")
        ids = {c.contract_id for c in contracts if c.role == "provider"}
        assert "http::GET::/users" in ids
        assert "http::POST::/users" in ids

    def test_rust_attribute_macro_provider(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/handlers.rs", """
            #[get("/systems/nearby")]
            async fn get_nearby_systems() -> impl Responder { ... }
        """)
        contracts = HttpExtractor().extract(tmp_path, "rust-svc")
        providers = [c for c in contracts if c.role == "provider"]
        assert len(providers) == 1
        assert providers[0].contract_id == "http::GET::/systems/nearby"

    def test_rust_reqwest_literal_consumer(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/client.rs", """
            let resp = client.post("http://backend:8000/api/data").send().await?;
            let r = reqwest::get("http://svc/health").await?;
        """)
        contracts = HttpExtractor().extract(tmp_path, "rust-client")
        ids = {c.contract_id for c in contracts if c.role == "consumer"}
        assert "http::POST::/api/data" in ids
        assert "http::GET::/health" in ids

    def test_rust_reqwest_format_consumer(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/client.rs", """
            let r = client.get(format!("{}/systems/{}", base, id)).send().await?;
        """)
        contracts = HttpExtractor().extract(tmp_path, "rust-client")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        assert consumers[0].contract_id == "http::GET::/systems/{param}"
        assert consumers[0].meta.get("base_stripped") is True

    def test_rust_reqwest_ignores_map_get(self, tmp_path: Path) -> None:
        # A HashMap-style .get("key") has no slash and must not look like a route.
        self._write_file(tmp_path, "src/lookup.rs", """
            let v = config.get("database_url");
        """)
        contracts = HttpExtractor().extract(tmp_path, "rust-client")
        assert [c for c in contracts if c.role == "consumer"] == []

    def test_csharp_wrapper_interpolated_consumer(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "ApiClient.cs", """
            public class ApiClient {
                public async Task Bids() {
                    return await GetRequest<Response>($"{_baseUrl}/vix/my/bids");
                }
            }
        """)
        contracts = HttpExtractor().extract(tmp_path, "unity-game")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        assert consumers[0].contract_id == "http::GET::/vix/my/bids"
        assert consumers[0].meta.get("base_stripped") is True

    def test_csharp_wrapper_interior_param(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "ApiClient.cs", """
            await PostRequest<Response>($"{_baseUrl}/vix/listings/{id}/bid", payload);
            GetAsync<Response>($"{_config.ApiBaseUrl}/fleet/summary/{playerId}", token);
        """)
        contracts = HttpExtractor().extract(tmp_path, "unity-game")
        ids = {c.contract_id for c in contracts if c.role == "consumer"}
        assert "http::POST::/vix/listings/{param}/bid" in ids
        assert "http::GET::/fleet/summary/{param}" in ids

    def test_csharp_unitywebrequest_consumer(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "Net.cs", """
            UnityWebRequest.Post($"{_baseUrl}/vix/my/bids", form);
            var req = UnityWebRequest.Get(url);
        """)
        contracts = HttpExtractor().extract(tmp_path, "unity-game")
        consumers = [c for c in contracts if c.role == "consumer"]
        # The literal Post call is captured; Get(url) with a variable URL is not.
        assert len(consumers) == 1
        assert consumers[0].contract_id == "http::POST::/vix/my/bids"
        assert consumers[0].meta["client"] == "unitywebrequest"

    def test_js_wrapper_consumer_with_method(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/api.ts", """
            fetchJSON(`${BASE}/path`, { method: "POST" });
        """)
        contracts = HttpExtractor().extract(tmp_path, "client")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        assert consumers[0].contract_id == "http::POST::/path"
        assert consumers[0].meta["client"] == "wrapper"

    def test_js_wrapper_consumer_default_get_and_interior_param(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/api.ts", """
            fetchJSON(`${BASE}/health`);
            fetchJSON(`${BASE}/systems/${Number(id)}`);
        """)
        contracts = HttpExtractor().extract(tmp_path, "client")
        ids = {c.contract_id for c in contracts if c.role == "consumer"}
        assert "http::GET::/health" in ids
        assert "http::GET::/systems/{param}" in ids

    def test_js_wrapper_method_signal_without_http_name(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/api.ts", """
            send("/orders", { method: "PUT" });
        """)
        contracts = HttpExtractor().extract(tmp_path, "client")
        ids = {c.contract_id for c in contracts if c.role == "consumer"}
        assert ids == {"http::PUT::/orders"}

    def test_js_wrapper_ignores_navigation_and_keys(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "src/app.tsx", """
            navigate("/dashboard");
            router.push("/home");
            t("/some/i18n/key");
        """)
        contracts = HttpExtractor().extract(tmp_path, "client")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert consumers == []

    def test_empty_file(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "empty.py", "")
        contracts = HttpExtractor().extract(tmp_path, "svc")
        assert contracts == []

    def test_no_patterns(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "utils.py", "def add(a, b): return a + b")
        contracts = HttpExtractor().extract(tmp_path, "svc")
        assert contracts == []

    def test_truncated_template_literal_dropped(self, tmp_path: Path) -> None:
        # A nested quote inside the template expression truncates the capture,
        # leaving an unbalanced ``${`` — this is noise and must not be emitted.
        self._write_file(tmp_path, "src/api.ts", """
            fetchJSON(`/repos/explore${qs ? '?' + qs : ''}`);
        """)
        consumers = [
            c for c in HttpExtractor().extract(tmp_path, "frontend") if c.role == "consumer"
        ]
        assert consumers == []

    def test_bare_param_path_dropped(self, tmp_path: Path) -> None:
        # fetch(`${x}`) collapses to a single {param} segment — no concrete path
        # to match on, so it is dropped rather than linked indiscriminately.
        self._write_file(tmp_path, "src/api.ts", """
            await fetch(`${endpoint}`);
        """)
        consumers = [
            c for c in HttpExtractor().extract(tmp_path, "frontend") if c.role == "consumer"
        ]
        assert consumers == []

    def test_absolute_host_recorded_in_meta(self, tmp_path: Path) -> None:
        # An absolute third-party URL keeps its host so the matcher can exclude it.
        self._write_file(tmp_path, "src/api.ts", """
            fetch('https://formspree.io/f/mkopzvak', { method: 'POST' });
        """)
        consumers = [
            c for c in HttpExtractor().extract(tmp_path, "frontend") if c.role == "consumer"
        ]
        assert len(consumers) == 1
        assert consumers[0].meta.get("host") == "formspree.io"

    def test_test_directory_files_excluded(self, tmp_path: Path) -> None:
        # A route defined only under tests/ is a fixture, not a service contract.
        self._write_file(tmp_path, "tests/test_routes.py", """
            @app.get("/fixture-only")
            def fixture(): ...
        """)
        self._write_file(tmp_path, "app/routes.py", """
            @app.get("/real")
            def real(): ...
        """)
        from repowise.core.workspace.extractors.base import make_exclude_predicate

        ids = {
            c.contract_id
            for c in HttpExtractor().extract(tmp_path, "svc", make_exclude_predicate())
            if c.role == "provider"
        }
        assert "http::GET::/real" in ids
        assert "http::GET::/fixture-only" not in ids


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

    def test_csharp_consumer_requires_grpc_context(self, tmp_path: Path) -> None:
        self._write_file(tmp_path, "Caller.cs", """
            using Grpc.Net.Client;
            public class Caller {
                public void Go() {
                    var channel = GrpcChannel.ForAddress("https://api");
                    var client = new GreeterClient(channel);
                }
            }
        """)
        contracts = GrpcExtractor().extract(tmp_path, "web")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 1
        assert consumers[0].contract_id == "grpc::Greeter/*"

    def test_csharp_consumer_skips_non_grpc_clients(self, tmp_path: Path) -> None:
        # TLS/crypto client classes with no gRPC context must not be emitted as
        # gRPC consumers (the false positives reported in #474).
        self._write_file(tmp_path, "Tls.cs", """
            public class Security {
                public void Setup() {
                    var tls = new TlsClient(config);
                    var sodium = new SodiumClient();
                }
            }
        """)
        contracts = GrpcExtractor().extract(tmp_path, "game")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert consumers == []


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
# Candidate matching (mount / version / base-prefix tolerant)
# ---------------------------------------------------------------------------


class TestCandidateMatching:
    def _c(self, **kwargs) -> Contract:
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

    def test_mount_prefix_creates_candidate_link(self) -> None:
        # Consumer hits /api/resource/search; provider mounts /resource/search.
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::GET::/resource/search"),
            self._c(repo="frontend", role="consumer", contract_id="http::GET::/api/resource/search",
                    file_path="c.ts", confidence=0.75),
        ]
        links = match_contracts(contracts)
        assert len(links) == 1
        assert links[0].match_type == "candidate"
        # Candidate confidence is strictly lower than an exact min() would give.
        assert links[0].confidence < 0.75

    def test_version_prefix_with_base_param(self) -> None:
        # Provider /v1/resource; consumer base resolved to a leading {param}.
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::POST::/v1/resource"),
            self._c(repo="frontend", role="consumer", contract_id="http::POST::/{param}/resource",
                    file_path="c.ts"),
        ]
        links = match_contracts(contracts)
        assert len(links) == 1
        assert links[0].match_type == "candidate"

    def test_base_stripped_consumer_unique_service_is_exact(self) -> None:
        # A stripped base whose full path matches exactly one provider service is
        # unambiguous: the base can only be that service, so the link is exact.
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::GET::/resource"),
            self._c(repo="frontend", role="consumer", contract_id="http::GET::/resource",
                    file_path="c.ts", meta={"base_stripped": True}),
        ]
        links = match_contracts(contracts)
        assert len(links) == 1
        assert links[0].match_type == "exact"

    def test_base_stripped_consumer_ambiguous_is_candidate(self) -> None:
        # The same stripped path provided by two distinct services is ambiguous —
        # we can't tell which one the base points at, so both links are candidate.
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::GET::/resource"),
            self._c(repo="payments", role="provider", contract_id="http::GET::/resource"),
            self._c(repo="frontend", role="consumer", contract_id="http::GET::/resource",
                    file_path="c.ts", meta={"base_stripped": True}),
        ]
        links = match_contracts(contracts)
        assert len(links) == 2
        assert all(lk.match_type == "candidate" for lk in links)

    def test_base_stripped_consumer_resolved_by_service_bases(self) -> None:
        # An ambiguous stripped path is promoted to exact (and narrowed to the
        # configured repo) once a base token resolves the target service.
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::GET::/resource"),
            self._c(repo="payments", role="provider", contract_id="http::GET::/resource"),
            self._c(repo="frontend", role="consumer", contract_id="http::GET::/resource",
                    file_path="c.ts", meta={"base_stripped": True, "base_token": "API_BASE"}),
        ]
        annotate_consumer_targets(contracts, {"API_BASE": "backend"})
        links = match_contracts(contracts)
        assert len(links) == 1
        assert links[0].match_type == "exact"
        assert links[0].provider_repo == "backend"

    def test_exact_match_takes_precedence_over_candidate(self) -> None:
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::GET::/api/users"),
            self._c(repo="frontend", role="consumer", contract_id="http::GET::/api/users", file_path="c.ts"),
        ]
        links = match_contracts(contracts)
        assert len(links) == 1
        assert links[0].match_type == "exact"

    def test_static_asset_excluded_from_candidate(self) -> None:
        # /api/app.js reduces to the same core as /app.js, but static assets
        # must never produce contract links.
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::GET::/app.js"),
            self._c(repo="frontend", role="consumer", contract_id="http::GET::/api/app.js", file_path="c.ts"),
        ]
        links = match_contracts(contracts)
        assert links == []

    def test_external_host_consumer_excluded(self) -> None:
        # A consumer pointing at a literal third-party host (not a workspace
        # repo) is excluded from matching even when a path-equal provider exists.
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::POST::/f/mkopzvak"),
            self._c(repo="frontend", role="consumer", contract_id="http::POST::/f/mkopzvak",
                    file_path="c.ts", meta={"host": "formspree.io"}),
        ]
        annotate_consumer_targets(contracts)
        assert any(c.meta.get("external") for c in contracts if c.role == "consumer")
        assert match_contracts(contracts) == []

    def test_absolute_host_matching_repo_alias_links(self) -> None:
        # http://backend:8000/... resolves to the workspace 'backend' repo and
        # links normally (target host is a service, not a third party).
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::GET::/api/data"),
            self._c(repo="worker", role="consumer", contract_id="http::GET::/api/data",
                    file_path="c.py", meta={"host": "backend"}),
        ]
        annotate_consumer_targets(contracts)
        links = match_contracts(contracts)
        assert len(links) == 1
        assert links[0].provider_repo == "backend"

    def test_public_subdomain_not_mistaken_for_repo_alias(self) -> None:
        # backend.stripe.com must be external even though 'backend' is a repo —
        # only the full host or an internal-DNS leading label maps to an alias.
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::GET::/v1/charges"),
            self._c(repo="frontend", role="consumer", contract_id="http::GET::/v1/charges",
                    file_path="c.ts", meta={"host": "backend.stripe.com"}),
        ]
        annotate_consumer_targets(contracts)
        assert any(c.meta.get("external") for c in contracts if c.role == "consumer")
        assert match_contracts(contracts) == []

    def test_internal_dns_host_resolves_to_alias(self) -> None:
        # k8s service DNS backend.default.svc.cluster.local -> the 'backend' repo.
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::GET::/api/data"),
            self._c(repo="worker", role="consumer", contract_id="http::GET::/api/data",
                    file_path="c.py", meta={"host": "backend.default.svc.cluster.local"}),
        ]
        annotate_consumer_targets(contracts)
        links = match_contracts(contracts)
        assert len(links) == 1
        assert links[0].provider_repo == "backend"

    def test_base_stripped_stale_service_base_stays_candidate(self) -> None:
        # A service_bases target that matches no provider must fall back to the
        # ambiguity rule, not emit exact links to every service.
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::GET::/resource"),
            self._c(repo="payments", role="provider", contract_id="http::GET::/resource"),
            self._c(repo="frontend", role="consumer", contract_id="http::GET::/resource",
                    file_path="c.ts", meta={"base_stripped": True, "base_token": "API_BASE"}),
        ]
        annotate_consumer_targets(contracts, {"API_BASE": "typo_repo"})
        links = match_contracts(contracts)
        assert len(links) == 2
        assert all(lk.match_type == "candidate" for lk in links)

    def test_method_mismatch_no_candidate(self) -> None:
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::POST::/v1/resource"),
            self._c(repo="frontend", role="consumer", contract_id="http::GET::/resource",
                    file_path="c.ts", meta={"base_stripped": True}),
        ]
        links = match_contracts(contracts)
        assert links == []

    def test_wildcard_provider_method_candidate(self) -> None:
        # Go HandleFunc providers carry method "*" — compatible with any method.
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::*::/v1/orders"),
            self._c(repo="frontend", role="consumer", contract_id="http::GET::/api/orders", file_path="c.ts"),
        ]
        links = match_contracts(contracts)
        assert len(links) == 1
        assert links[0].match_type == "candidate"

    def test_does_not_overcollapse_to_root(self) -> None:
        # Both sides reduce to "/" once prefixes are stripped — too generic to
        # link safely.
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::GET::/api/v1"),
            self._c(repo="frontend", role="consumer", contract_id="http::GET::/api", file_path="c.ts"),
        ]
        links = match_contracts(contracts)
        assert links == []

    def test_non_mount_segment_not_stripped(self) -> None:
        # `internal` is a legitimate resource name, not a mount prefix — it must
        # not be stripped, so /internal/users and /api/users stay distinct.
        contracts = [
            self._c(repo="backend", role="provider", contract_id="http::GET::/internal/users"),
            self._c(repo="frontend", role="consumer", contract_id="http::GET::/api/users", file_path="c.ts"),
        ]
        links = match_contracts(contracts)
        assert links == []

    def test_same_repo_same_service_filtered_in_candidate(self) -> None:
        contracts = [
            self._c(repo="mono", role="provider", contract_id="http::GET::/v1/resource", service="svc-a"),
            self._c(repo="mono", role="consumer", contract_id="http::GET::/api/resource",
                    file_path="c.py", service="svc-a"),
        ]
        links = match_contracts(contracts)
        assert links == []


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
