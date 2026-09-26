"""Unit tests for Laravel framework edges (F3)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext


def _file_info(rel: str, abs_path: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=abs_path,
        language="php",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _build_parsed(repo: Path) -> dict[str, ParsedFile]:
    parser = ASTParser()
    out: dict[str, ParsedFile] = {}
    for php in repo.rglob("*.php"):
        rel = php.resolve().relative_to(repo.resolve()).as_posix()
        fi = _file_info(rel, str(php.resolve()))
        out[rel] = parser.parse_file(fi, php.read_bytes())
    return out


def _ctx(repo: Path, parsed: dict[str, ParsedFile]) -> ResolverContext:
    path_set = set(parsed.keys())
    stem_map: dict[str, list[str]] = {}
    for p in path_set:
        stem = Path(p).stem.lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=path_set, stem_map=stem_map, graph=nx.DiGraph(), repo_path=repo
    )


def _make_composer(repo: Path, psr4: dict[str, str]) -> None:
    (repo / "composer.json").write_text(
        json.dumps({"autoload": {"psr-4": psr4}})
    )


class TestLaravelRoutes:
    def test_array_syntax_route_to_controller(self, tmp_path: Path) -> None:
        _make_composer(tmp_path, {"App\\": "src/"})
        ctrl_dir = tmp_path / "src" / "Http" / "Controllers"
        ctrl_dir.mkdir(parents=True)
        (ctrl_dir / "UsersController.php").write_text(
            "<?php\nnamespace App\\Http\\Controllers;\nclass UsersController {}\n"
        )
        routes_dir = tmp_path / "routes"
        routes_dir.mkdir()
        (routes_dir / "web.php").write_text(
            "<?php\nuse App\\Http\\Controllers\\UsersController;\n"
            "Route::get('/users', [UsersController::class, 'index']);\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["laravel"])
        assert graph.has_edge(
            "routes/web.php", "src/Http/Controllers/UsersController.php"
        )

    def test_legacy_string_route_to_controller(self, tmp_path: Path) -> None:
        _make_composer(tmp_path, {"App\\": "src/"})
        ctrl_dir = tmp_path / "src"
        ctrl_dir.mkdir(parents=True)
        (ctrl_dir / "UsersController.php").write_text(
            "<?php\nnamespace App;\nclass UsersController {}\n"
        )
        routes_dir = tmp_path / "routes"
        routes_dir.mkdir()
        (routes_dir / "api.php").write_text(
            "<?php\nRoute::get('/users', 'UsersController@index');\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["laravel"])
        assert graph.has_edge("routes/api.php", "src/UsersController.php")


class TestLaravelServiceProvider:
    def test_bind_links_provider_to_classes(self, tmp_path: Path) -> None:
        _make_composer(tmp_path, {"App\\": "src/"})
        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentInterface.php").write_text(
            "<?php\nnamespace App;\ninterface PaymentInterface {}\n"
        )
        (src / "StripePayment.php").write_text(
            "<?php\nnamespace App;\nclass StripePayment implements PaymentInterface {}\n"
        )
        (src / "AppServiceProvider.php").write_text(
            "<?php\nnamespace App;\nclass AppServiceProvider {\n"
            "  public function register() {\n"
            "    $this->app->bind(PaymentInterface::class, StripePayment::class);\n"
            "  }\n}\n"
        )
        # Need a route file to enable the slice
        (tmp_path / "routes").mkdir()
        (tmp_path / "routes" / "web.php").write_text("<?php\n")
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert graph.has_edge(
            "src/AppServiceProvider.php", "src/PaymentInterface.php"
        )
        assert graph.has_edge(
            "src/AppServiceProvider.php", "src/StripePayment.php"
        )


class TestLaravelEloquent:
    def test_has_many_links_models(self, tmp_path: Path) -> None:
        _make_composer(tmp_path, {"App\\": "src/"})
        models = tmp_path / "src" / "Models"
        models.mkdir(parents=True)
        (models / "User.php").write_text(
            "<?php\nnamespace App\\Models;\n"
            "class User {\n"
            "  public function orders() { return $this->hasMany(Order::class); }\n"
            "}\n"
        )
        (models / "Order.php").write_text(
            "<?php\nnamespace App\\Models;\nclass Order {}\n"
        )
        (tmp_path / "routes").mkdir()
        (tmp_path / "routes" / "web.php").write_text("<?php\n")
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert graph.has_edge("src/Models/User.php", "src/Models/Order.php")


class TestLaravelGate:
    def test_no_route_files_no_edges(self, tmp_path: Path) -> None:
        (tmp_path / "Plain.php").write_text("<?php\nclass Plain {}\n")
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        count = add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert count == 0


class TestLaravelConventionFiles:
    def _graph(self, repo: Path) -> nx.DiGraph:
        parsed = _build_parsed(repo)
        graph = nx.DiGraph()
        graph.add_nodes_from(parsed)
        add_framework_edges(graph, parsed, _ctx(repo, parsed), tech_stack=[])
        return graph

    def test_nested_app_found_from_its_manifest(self, tmp_path: Path) -> None:
        # No routes/ at the repo root and no stack hint: the nested manifest
        # requiring the framework is what places the app.
        app = tmp_path / "apps" / "api"
        app.mkdir(parents=True)
        (app / "composer.json").write_text(json.dumps({"require": {"laravel/framework": "^11"}}))
        for rel in (
            "routes/api.php",
            "app/Providers/AppServiceProvider.php",
            "app/Console/Commands/Prune.php",
            "database/factories/UserFactory.php",
            "app/Jobs/SendMail.php",
        ):
            (app / rel).parent.mkdir(parents=True, exist_ok=True)
            (app / rel).write_text("<?php\n")
        graph = self._graph(tmp_path)
        anchored = set(graph.successors("framework:laravel"))
        assert anchored == {
            "apps/api/routes/api.php",
            "apps/api/app/Providers/AppServiceProvider.php",
            "apps/api/app/Console/Commands/Prune.php",
            "apps/api/database/factories/UserFactory.php",
        }

    def test_route_files_alone_anchor_no_convention_files(self, tmp_path: Path) -> None:
        # A PHP app with routes/web.php but no Laravel requirement keeps its
        # route edges and gets no framework anchors for config/ or providers.
        (tmp_path / "composer.json").write_text(json.dumps({"require": {"slim/slim": "^4"}}))
        for rel in ("routes/web.php", "config/app.php", "app/Providers/Boot.php"):
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text("<?php\n")
        graph = self._graph(tmp_path)
        assert "framework:laravel" not in graph

    def test_route_files_link_controllers_under_a_nested_root(self, tmp_path: Path) -> None:
        app = tmp_path / "api"
        (app / "routes").mkdir(parents=True)
        (app / "composer.json").write_text(
            json.dumps(
                {"require": {"laravel/framework": "^11"}, "autoload": {"psr-4": {"App\\": "app/"}}}
            )
        )
        (app / "routes" / "api.php").write_text(
            "<?php\nuse App\\Http\\Controllers\\PingController;\n"
            "Route::get('/ping', [PingController::class, 'show']);\n"
        )
        ctrl = app / "app" / "Http" / "Controllers"
        ctrl.mkdir(parents=True)
        (ctrl / "PingController.php").write_text(
            "<?php\nnamespace App\\Http\\Controllers;\nclass PingController {}\n"
        )
        graph = self._graph(tmp_path)
        assert graph.has_edge("api/routes/api.php", "api/app/Http/Controllers/PingController.php")


def _laravel_app(repo: Path, files: dict[str, str]) -> nx.DiGraph:
    """A Laravel app at *repo* with *files*; the framework edges it gets."""
    (repo / "composer.json").write_text(
        json.dumps(
            {"require": {"laravel/framework": "^11"}, "autoload": {"psr-4": {"App\\": "app/"}}}
        )
    )
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    parsed = _build_parsed(repo)
    graph = nx.DiGraph()
    graph.add_nodes_from(parsed)
    add_framework_edges(graph, parsed, _ctx(repo, parsed), tech_stack=[])
    return graph


def _class(fqn: str, body: str = "") -> str:
    namespace, _, name = fqn.rpartition("\\")
    return f"<?php\nnamespace {namespace};\nclass {name} {{ {body} }}\n"


def _anchored(graph: nx.DiGraph) -> set[str]:
    return set(graph.successors("framework:laravel")) if "framework:laravel" in graph else set()


class TestLaravelRegistrations:
    def test_listen_array_links_events_and_listeners(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Events/OrderPlaced.php": _class("App\\Events\\OrderPlaced"),
                "app/Mail/SendReceipt.php": _class("App\\Mail\\SendReceipt"),
                "app/Providers/EventServiceProvider.php": (
                    "<?php\nnamespace App\\Providers;\n"
                    "use App\\Events\\OrderPlaced;\nuse App\\Mail\\SendReceipt;\n"
                    "class EventServiceProvider {\n"
                    "    protected $listen = [OrderPlaced::class => [SendReceipt::class]];\n}\n"
                ),
            },
        )
        provider = "app/Providers/EventServiceProvider.php"
        assert graph.has_edge(provider, "app/Events/OrderPlaced.php")
        assert graph.has_edge(provider, "app/Mail/SendReceipt.php")

    def test_event_listen_and_gate_policy_calls(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Events/Refunded.php": _class("App\\Events\\Refunded"),
                "app/Handlers/NotifyFinance.php": _class("App\\Handlers\\NotifyFinance"),
                "app/Models/Ticket.php": _class("App\\Models\\Ticket"),
                "app/Auth/TicketRules.php": _class("App\\Auth\\TicketRules"),
                "app/Providers/AppServiceProvider.php": (
                    "<?php\nnamespace App\\Providers;\nclass AppServiceProvider {\n"
                    "  public function boot() {\n"
                    "    Event::listen(\\App\\Events\\Refunded::class, [\\App\\Handlers\\NotifyFinance::class, 'handle']);\n"
                    "    Gate::policy(\\App\\Models\\Ticket::class, \\App\\Auth\\TicketRules::class);\n"
                    "  }\n}\n"
                ),
            },
        )
        provider = "app/Providers/AppServiceProvider.php"
        assert graph.has_edge(provider, "app/Handlers/NotifyFinance.php")
        assert graph.has_edge(provider, "app/Auth/TicketRules.php")

    def test_a_discovered_listener_is_linked_from_its_event(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Events/OrderPlaced.php": _class("App\\Events\\OrderPlaced"),
                "app/Listeners/SendReceipt.php": (
                    "<?php\nnamespace App\\Listeners;\nuse App\\Events\\OrderPlaced;\n"
                    "class SendReceipt { public function handle(OrderPlaced $event): void {} }\n"
                ),
            },
        )
        assert graph.has_edge("app/Events/OrderPlaced.php", "app/Listeners/SendReceipt.php")
        assert "app/Listeners/SendReceipt.php" not in _anchored(graph)

    def test_a_listener_for_a_framework_event_is_anchored(self, tmp_path: Path) -> None:
        # A local class that shares the vendor event's short name must not
        # stand in for it.
        graph = _laravel_app(
            tmp_path,
            {
                "app/Models/Login.php": _class("App\\Models\\Login"),
                "app/Listeners/AuditLogin.php": (
                    "<?php\nnamespace App\\Listeners;\nuse Illuminate\\Auth\\Events\\Login;\n"
                    "class AuditLogin { public function handle(Login $event): void {} }\n"
                ),
            },
        )
        assert "app/Listeners/AuditLogin.php" in _anchored(graph)
        assert not graph.has_edge("app/Models/Login.php", "app/Listeners/AuditLogin.php")

    def test_an_unregistered_listener_is_neither_linked_nor_anchored(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {"app/Listeners/Orphan.php": _class("App\\Listeners\\Orphan", "public function handle($e) {}")},
        )
        assert graph.in_degree("app/Listeners/Orphan.php") == 0

    def test_a_policy_is_found_from_its_model(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Models/Guestlist.php": _class("App\\Models\\Guestlist"),
                "app/Policies/GuestlistPolicy.php": _class("App\\Policies\\GuestlistPolicy"),
                "app/Policies/OrphanPolicy.php": _class("App\\Policies\\OrphanPolicy"),
            },
        )
        assert graph.has_edge("app/Models/Guestlist.php", "app/Policies/GuestlistPolicy.php")
        assert graph.in_degree("app/Policies/OrphanPolicy.php") == 0

    def test_bootstrap_providers_link_every_provider(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Billing/BillingServiceProvider.php": _class("App\\Billing\\BillingServiceProvider"),
                "bootstrap/providers.php": (
                    "<?php\nreturn [\n    App\\Billing\\BillingServiceProvider::class,\n];\n"
                ),
            },
        )
        assert graph.has_edge("bootstrap/providers.php", "app/Billing/BillingServiceProvider.php")

    def test_a_scheduled_command_is_linked_by_signature(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Console/Commands/PruneOrders.php": _class(
                    "App\\Console\\Commands\\PruneOrders",
                    "protected $signature = 'orders:prune {--days=30}';",
                ),
                "bootstrap/app.php": (
                    "<?php\nreturn Application::configure()->withSchedule(function ($schedule) {\n"
                    "    $schedule->command('orders:prune --days=7')->daily();\n})->create();\n"
                ),
            },
        )
        assert graph.has_edge("bootstrap/app.php", "app/Console/Commands/PruneOrders.php")

    def test_a_middleware_alias_links_the_route_file(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Http/Middleware/IdempotencyKey.php": _class("App\\Http\\Middleware\\IdempotencyKey"),
                "bootstrap/app.php": (
                    "<?php\nuse App\\Http\\Middleware\\IdempotencyKey;\n"
                    "return Application::configure()->withMiddleware(function ($middleware) {\n"
                    "    $middleware->alias(['idempotent' => IdempotencyKey::class]);\n})->create();\n"
                ),
                "routes/api.php": (
                    "<?php\nRoute::middleware(['auth', 'idempotent:strict'])->post('/pay', fn () => 1);\n"
                ),
            },
        )
        assert graph.has_edge("routes/api.php", "app/Http/Middleware/IdempotencyKey.php")

    def test_every_route_file_and_a_controller_group(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Http/Controllers/ReportController.php": _class(
                    "App\\Http\\Controllers\\ReportController"
                ),
                "routes/admin.php": (
                    "<?php\nuse App\\Http\\Controllers\\ReportController;\n"
                    "Route::controller(ReportController::class)->group(function () {\n"
                    "    Route::get('/revenue', 'revenue');\n});\n"
                ),
            },
        )
        assert graph.has_edge("routes/admin.php", "app/Http/Controllers/ReportController.php")


class TestLaravelRegistrationShapes:
    def test_subscribers_commands_and_scheduled_jobs(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Subscribers/AuditSubscriber.php": _class("App\\Subscribers\\AuditSubscriber"),
                "app/Ops/Prune.php": _class("App\\Ops\\Prune"),
                "app/Jobs/Rollup.php": _class("App\\Jobs\\Rollup"),
                "app/Providers/EventServiceProvider.php": (
                    "<?php\nnamespace App\\Providers;\nuse App\\Subscribers\\AuditSubscriber;\n"
                    "class EventServiceProvider { protected $subscribe = [AuditSubscriber::class]; }\n"
                ),
                "bootstrap/app.php": (
                    "<?php\nreturn Application::configure()\n"
                    "    ->withCommands([App\\Ops\\Prune::class])\n"
                    "    ->withSchedule(function ($schedule) {\n"
                    "        $schedule->job(new App\\Jobs\\Rollup())->hourly();\n"
                    "    })->create();\n"
                ),
            },
        )
        assert graph.has_edge(
            "app/Providers/EventServiceProvider.php", "app/Subscribers/AuditSubscriber.php"
        )
        assert graph.has_edge("bootstrap/app.php", "app/Ops/Prune.php")
        assert graph.has_edge("bootstrap/app.php", "app/Jobs/Rollup.php")

    def test_config_app_providers(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Billing/BillingServiceProvider.php": _class(
                    "App\\Billing\\BillingServiceProvider"
                ),
                "config/app.php": (
                    "<?php\nreturn ['providers' => [\n"
                    "    App\\Billing\\BillingServiceProvider::class,\n]];\n"
                ),
            },
        )
        assert graph.has_edge("config/app.php", "app/Billing/BillingServiceProvider.php")

    def test_a_vendor_class_in_a_registration_binds_nothing_local(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Models/Login.php": _class("App\\Models\\Login"),
                "app/Providers/EventServiceProvider.php": (
                    "<?php\nnamespace App\\Providers;\n"
                    "class EventServiceProvider {\n"
                    "    protected $listen = [\\Illuminate\\Auth\\Events\\Login::class => []];\n}\n"
                ),
            },
        )
        assert not graph.has_edge("app/Providers/EventServiceProvider.php", "app/Models/Login.php")

    def test_commands_called_by_signature(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Ops/Prune.php": _class(
                    "App\\Ops\\Prune", "protected $signature = 'orders:prune';"
                ),
                "app/Ops/Reindex.php": (
                    "<?php\nnamespace App\\Ops;\n#[AsCommand(name: 'search:reindex')]\nclass Reindex {}\n"
                ),
                "routes/console.php": "<?php\nSchedule::command('orders:prune')->daily();\n",
                "app/Http/Controllers/AdminController.php": (
                    "<?php\nnamespace App\\Http\\Controllers;\n"
                    "class AdminController { public function go() { Artisan::call('search:reindex'); } }\n"
                ),
            },
        )
        assert graph.has_edge("routes/console.php", "app/Ops/Prune.php")
        assert graph.has_edge("app/Http/Controllers/AdminController.php", "app/Ops/Reindex.php")

    def test_a_kernel_alias_used_by_a_controller(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Http/Middleware/Guard.php": _class("App\\Http\\Middleware\\Guard"),
                "app/Http/Kernel.php": (
                    "<?php\nnamespace App\\Http;\nuse App\\Http\\Middleware\\Guard;\n"
                    "class Kernel { protected $routeMiddleware = ['guard' => Guard::class]; }\n"
                ),
                "app/Http/Controllers/AccountController.php": (
                    "<?php\nnamespace App\\Http\\Controllers;\n"
                    "class AccountController { public function __construct() { $this->middleware('guard'); } }\n"
                ),
            },
        )
        assert graph.has_edge(
            "app/Http/Controllers/AccountController.php", "app/Http/Middleware/Guard.php"
        )

    def test_a_union_typed_listener_is_linked_from_each_event(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Events/Paid.php": _class("App\\Events\\Paid"),
                "app/Events/Refunded.php": _class("App\\Events\\Refunded"),
                "app/Listeners/Ledger.php": (
                    "<?php\nnamespace App\\Listeners;\nuse App\\Events\\Paid;\nuse App\\Events\\Refunded;\n"
                    "class Ledger { public function handle(Paid|Refunded $event): void {} }\n"
                ),
            },
        )
        assert graph.has_edge("app/Events/Paid.php", "app/Listeners/Ledger.php")
        assert graph.has_edge("app/Events/Refunded.php", "app/Listeners/Ledger.php")

    def test_a_policy_in_a_subfolder_keeps_the_model_sub_namespace(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Models/User.php": _class("App\\Models\\User"),
                "app/Models/Admin/User.php": _class("App\\Models\\Admin\\User"),
                "app/Policies/Admin/UserPolicy.php": _class("App\\Policies\\Admin\\UserPolicy"),
            },
        )
        assert graph.has_edge("app/Models/Admin/User.php", "app/Policies/Admin/UserPolicy.php")
        assert not graph.has_edge("app/Models/User.php", "app/Policies/Admin/UserPolicy.php")

    def test_a_legacy_string_handler_in_the_default_namespace(self, tmp_path: Path) -> None:
        graph = _laravel_app(
            tmp_path,
            {
                "app/Http/Controllers/Admin/DashboardController.php": _class(
                    "App\\Http\\Controllers\\Admin\\DashboardController"
                ),
                "routes/web.php": "<?php\nRoute::get('/d', 'Admin\\DashboardController@index');\n",
            },
        )
        assert graph.has_edge(
            "routes/web.php", "app/Http/Controllers/Admin/DashboardController.php"
        )
