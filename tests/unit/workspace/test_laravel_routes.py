"""Laravel route groups, resources and route-file prefixes.

The recogniser in ``ingestion.framework_routes`` serves both the graph and the
contract layer, so the paths here are what a consumer's URL must match: every
enclosing group prefix, the prefix the route file itself is served under, and
the routes a resource registration stands for.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.framework_routes import (
    laravel_route_file_prefixes,
    laravel_route_prefix,
    laravel_routes,
)
from repowise.core.workspace.extractors.http import HttpExtractor


def _routes(php: str, prefix: str = "") -> list[tuple[str, str | None, str | None]]:
    return [(r.verb, r.path, r.handler) for r in laravel_routes(php, prefix)]


def _providers(repo: Path) -> set[str]:
    return {c.contract_id for c in HttpExtractor().extract(repo, "api") if c.role == "provider"}


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestGroups:
    def test_prefix_groups_nest(self) -> None:
        php = """<?php
Route::prefix('v1')->middleware('auth')->group(function () {
    Route::get('/me', [MeController::class, 'show']);
    Route::prefix('admin')->group(function () {
        Route::delete('users/{id}', [UserController::class, 'destroy']);
    });
});
Route::get('/health', [HealthController::class, 'show']);
"""
        assert _routes(php) == [
            ("GET", "/v1/me", "MeController"),
            ("DELETE", "/v1/admin/users/{id}", "UserController"),
            ("GET", "/health", "HealthController"),
        ]

    def test_the_array_form_declares_a_prefix(self) -> None:
        php = """<?php
Route::group(['middleware' => ['web'], 'prefix' => 'radio'], static function (): void {
    Route::get('stations', [StationController::class, 'index']);
});
"""
        assert _routes(php) == [("GET", "/radio/stations", "StationController")]

    def test_a_bare_method_takes_the_controller_of_its_group(self) -> None:
        php = """<?php
Route::controller(OrderController::class)->prefix('orders')->group(function () {
    Route::get('/{id}', 'show');
    Route::post('/', 'store');
    Route::get('/export', [ExportController::class, 'csv']);
});
"""
        assert _routes(php) == [
            ("GET", "/orders/{id}", "OrderController"),
            ("POST", "/orders", "OrderController"),
            ("GET", "/orders/export", "ExportController"),
        ]

    def test_an_empty_path_is_the_group_root(self) -> None:
        php = "<?php\nRoute::prefix('users')->group(function () { Route::get('', [U::class, 'i']); });"
        assert _routes(php) == [("GET", "/users", "U")]

    def test_a_middleware_chain_can_end_in_a_verb(self) -> None:
        php = "<?php\nRoute::middleware('auth:sanctum')->prefix('v2')->get('/user', [U::class, 'me']);"
        assert _routes(php) == [("GET", "/v2/user", "U")]

    def test_a_comment_between_links_keeps_the_chain(self) -> None:
        php = """<?php
Route::prefix('api')
    ->middleware('api')
    // a flat list of routes
    ->group(static function (): void {
        Route::get('ping', static fn () => null);
    });
"""
        assert _routes(php) == [("GET", "/api/ping", None)]

    def test_an_unreadable_prefix_refuses_the_path_but_keeps_the_handler(self) -> None:
        # Serving the route at the outer prefix would be a guess; the graph
        # still wants the controller.
        php = """<?php
Route::prefix(config('app.admin'))->group(function () {
    Route::get('/stats', [StatsController::class, 'show']);
});
"""
        assert _routes(php) == [("GET", None, "StatsController")]

    def test_a_route_registry_call_is_not_a_route(self) -> None:
        assert _routes("<?php\n$routes = Route::getRoutes()->get('GET');") == []

    def test_match_lists_its_verbs(self) -> None:
        php = "<?php\nRoute::match(['get', 'post'], '/search', [SearchController::class, 'run']);"
        assert _routes(php) == [
            ("GET", "/search", "SearchController"),
            ("POST", "/search", "SearchController"),
        ]

    def test_the_array_form_declares_a_controller(self) -> None:
        php = (
            "<?php\nRoute::group(['controller' => C::class, 'prefix' => 'c'], function () {\n"
            "    Route::get('/x', 'show');\n});"
        )
        assert _routes(php) == [("GET", "/c/x", "C")]

    def test_an_interpolated_prefix_is_unreadable(self) -> None:
        php = "<?php\nRoute::prefix(\"v$ver\")->group(function () { Route::get('/u', [U::class, 'i']); });"
        assert _routes(php) == [("GET", None, "U")]

    def test_any_and_options_are_read_but_are_not_contracts(self, tmp_path: Path) -> None:
        php = "<?php\nRoute::any('/hook', [H::class, 'i']);\nRoute::options('/pre', [P::class, 'i']);"
        assert [v for v, _, _ in _routes(php)] == ["ANY", "OPTIONS"]
        _write(tmp_path, "routes/web.php", php)
        assert _providers(tmp_path) == set()

    def test_the_file_prefix_goes_first(self) -> None:
        php = "<?php\nRoute::prefix('orders')->group(function () { Route::get('/', [O::class, 'i']); });"
        assert _routes(php, "api") == [("GET", "/api/orders", "O")]

    def test_no_prefix_keeps_the_literal_as_written(self) -> None:
        assert _routes("<?php\nRoute::get('users', [U::class, 'i']);") == [("GET", "users", "U")]


class TestResources:
    def test_api_resource_drops_the_form_routes(self) -> None:
        verbs = [(v, p) for v, p, _ in _routes("<?php\nRoute::apiResource('tickets', T::class);")]
        assert verbs == [
            ("GET", "/tickets"),
            ("POST", "/tickets"),
            ("GET", "/tickets/{ticket}"),
            ("PUT", "/tickets/{ticket}"),
            ("PATCH", "/tickets/{ticket}"),
            ("DELETE", "/tickets/{ticket}"),
        ]

    def test_only_and_except_filter_the_actions(self) -> None:
        only = _routes("<?php\nRoute::apiResource('users', U::class)->only(['index', 'show']);")
        assert [(v, p) for v, p, _ in only] == [("GET", "/users"), ("GET", "/users/{user}")]
        except_ = _routes(
            "<?php\nRoute::resource('photos', P::class)->except(['create', 'edit', 'destroy']);"
        )
        assert [(v, p) for v, p, _ in except_] == [
            ("GET", "/photos"),
            ("POST", "/photos"),
            ("GET", "/photos/{photo}"),
            ("PUT", "/photos/{photo}"),
            ("PATCH", "/photos/{photo}"),
        ]
        single = _routes("<?php\nRoute::apiResource('venues', V::class)->only('index');")
        assert [(v, p) for v, p, _ in single] == [("GET", "/venues")]

    def test_the_options_array_filters_too(self) -> None:
        routes = _routes("<?php\nRoute::resource('tags', T::class, ['only' => ['index']]);")
        assert routes == [("GET", "/tags", "T")]

    def test_nested_and_shallow(self) -> None:
        nested = _routes("<?php\nRoute::apiResource('posts.comments', C::class)->only(['index', 'show']);")
        assert [p for _, p, _ in nested] == ["/posts/{post}/comments", "/posts/{post}/comments/{comment}"]
        shallow = _routes(
            "<?php\nRoute::apiResource('posts.comments', C::class)->only(['index', 'show'])->shallow();"
        )
        assert [p for _, p, _ in shallow] == ["/posts/{post}/comments", "/comments/{comment}"]

    def test_resources_registers_each_pair(self) -> None:
        php = "<?php\nRoute::apiResources(['albums' => AlbumController::class, 'songs' => SongController::class]);"
        handlers = {(p, h) for v, p, h in _routes(php) if v == "GET"}
        assert ("/albums", "AlbumController") in handlers
        assert ("/songs/{song}", "SongController") in handlers

    def test_a_resource_inside_a_group_carries_the_prefix(self) -> None:
        php = "<?php\nRoute::prefix('v1')->group(function () { Route::apiResource('orders', O::class)->only('index'); });"
        assert _routes(php) == [("GET", "/v1/orders", "O")]

    def test_a_computed_resource_name_still_names_its_controller(self) -> None:
        assert _routes("<?php\nRoute::resource($name, PhotoController::class);") == [
            ("RESOURCE", None, "PhotoController")
        ]


class TestRouteFilePrefixes:
    def test_with_routing_serves_api_under_its_prefix(self) -> None:
        bootstrap = """<?php
return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        web: __DIR__.'/../routes/web.php',
        api: __DIR__.'/../routes/api.php',
        apiPrefix: 'api/v2',
        health: '/up',
    )->create();
"""
        assert laravel_route_file_prefixes(bootstrap) == {"web.php": "", "api.php": "api/v2"}

    def test_with_routing_defaults_to_api(self) -> None:
        bootstrap = "<?php\nApplication::configure()->withRouting(api: __DIR__.'/../routes/api.php');"
        assert laravel_route_file_prefixes(bootstrap) == {"api.php": "api"}

    def test_an_unreadable_api_prefix_is_none(self) -> None:
        bootstrap = (
            "<?php\nApplication::configure()->withRouting("
            "api: __DIR__.'/../routes/api.php', apiPrefix: env('API_PREFIX'));"
        )
        assert laravel_route_file_prefixes(bootstrap) == {"api.php": None}

    def test_a_provider_group_names_the_file_it_loads(self) -> None:
        provider = """<?php
class RouteServiceProvider {
    public function boot() {
        Route::prefix('api')->middleware('api')->group(base_path('routes/api.php'));
        Route::middleware('web')->group(base_path('routes/web.php'));
        Route::group(['prefix' => 'admin'], function ($router) {
            require base_path('routes/admin.php');
        });
    }
}
"""
        assert laravel_route_file_prefixes(provider) == {
            "api.php": "api",
            "web.php": "",
            "admin.php": "admin",
        }

    def test_a_route_file_loaded_from_with_routing_then(self) -> None:
        bootstrap = """<?php
Application::configure()->withRouting(
    web: __DIR__.'/../routes/web.php',
    then: function () {
        Route::middleware('api')->prefix('webhooks')->group(base_path('routes/webhooks.php'));
    },
);
"""
        assert laravel_route_file_prefixes(bootstrap) == {"web.php": "", "webhooks.php": "webhooks"}

    def test_a_top_level_require_names_nothing(self) -> None:
        # Outside a group the loaded file inherits the loader's own prefix,
        # which this file cannot know.
        assert laravel_route_file_prefixes("<?php\nRoute::get('/', [H::class, 'i']);\nrequire __DIR__.'/auth.php';") == {}

    def test_route_prefix_defaults(self) -> None:
        assert laravel_route_prefix("routes/api.php", {}) == "api"
        assert laravel_route_prefix("routes/web.php", {}) == ""
        assert laravel_route_prefix("apps/shop/routes/api.php", {}) == "api"
        assert laravel_route_prefix("routes/api.php", {"api.php": "v1"}) == "v1"
        assert laravel_route_prefix("routes/api.php", {"api.php": None}) is None
        # Only files in a routes directory are route files, at any depth and
        # in either case; the default applies to `routes/api.php` alone.
        assert laravel_route_prefix("app/Http/api.php", {"api.php": "v1"}) == ""
        assert laravel_route_prefix("routes/api/v1.php", {"v1.php": "api/v1"}) == "api/v1"
        assert laravel_route_prefix("routes/api/v1.php", {}) == ""
        assert laravel_route_prefix("Modules/Blog/Routes/api.php", {}) == "api"


class TestContracts:
    def test_api_routes_default_to_the_api_prefix(self, tmp_path: Path) -> None:
        _write(tmp_path, "routes/api.php", "<?php\nRoute::get('/orders', [O::class, 'i']);")
        _write(tmp_path, "routes/web.php", "<?php\nRoute::get('/home', [H::class, 'i']);")
        assert _providers(tmp_path) == {"http::GET::/api/orders", "http::GET::/home"}

    def test_bootstrap_declares_the_api_prefix(self, tmp_path: Path) -> None:
        _write(tmp_path, "routes/api.php", "<?php\nRoute::get('/orders', [O::class, 'i']);")
        _write(
            tmp_path,
            "bootstrap/app.php",
            "<?php\nApplication::configure()->withRouting(api: __DIR__.'/../routes/api.php', apiPrefix: 'v1');",
        )
        assert _providers(tmp_path) == {"http::GET::/v1/orders"}

    def test_an_unreadable_file_prefix_refuses_the_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "routes/api.php", "<?php\nRoute::get('/orders', [O::class, 'i']);")
        _write(
            tmp_path,
            "bootstrap/app.php",
            "<?php\nApplication::configure()->withRouting(api: __DIR__.'/../routes/api.php', apiPrefix: env('P'));",
        )
        assert _providers(tmp_path) == set()

    def test_disagreeing_declarations_refuse_the_file(self, tmp_path: Path) -> None:
        # Two declarations of one file name the merge cannot settle: serving
        # the file at the default would be a guess.
        _write(tmp_path, "routes/api.php", "<?php\nRoute::get('/orders', [O::class, 'i']);")
        _write(
            tmp_path,
            "bootstrap/app.php",
            "<?php\nApplication::configure()"
            "->withRouting(api: __DIR__.'/../routes/api.php', apiPrefix: 'v1');",
        )
        _write(
            tmp_path,
            "app/Providers/RouteServiceProvider.php",
            "<?php\nRoute::prefix('api')->group(base_path('routes/api.php'));",
        )
        assert _providers(tmp_path) == set()

    def test_a_nested_route_file_takes_its_declared_prefix(self, tmp_path: Path) -> None:
        _write(tmp_path, "routes/api/v1.php", "<?php\nRoute::get('/users', [U::class, 'i']);")
        _write(
            tmp_path,
            "app/Providers/RouteServiceProvider.php",
            "<?php\nRoute::prefix('api/v1')->group(base_path('routes/api/v1.php'));",
        )
        assert _providers(tmp_path) == {"http::GET::/api/v1/users"}

    def test_a_group_and_resource_reach_the_contract(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "routes/api.php",
            "<?php\nRoute::prefix('events/{event}')->group(function () {\n"
            "    Route::apiResource('tickets', TicketController::class)->only(['index', 'store']);\n"
            "});\n",
        )
        assert _providers(tmp_path) == {
            "http::GET::/api/events/{param}/tickets",
            "http::POST::/api/events/{param}/tickets",
        }
