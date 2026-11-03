"""PHP HTTP providers: Symfony (attributes and docblocks) and Slim."""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.origins import ContractOrigin


def _providers(repo: Path) -> dict[str, ContractOrigin]:
    return {
        c.contract_id: c.origin
        for c in HttpExtractor().extract(repo, "ratings")
        if c.role == "provider"
    }


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestSymfonyRoutes:
    def test_robotshop_ratings_controllers(self) -> None:
        fixture_repo = Path(__file__).parents[2] / "fixtures" / "workspace_http" / "symfony"
        providers = _providers(fixture_repo)

        assert "http::GET::/_health" in providers
        assert providers["http::GET::/_health"] == ContractOrigin.ROUTE_REGISTRATION

        assert "http::PUT::/api/rate/{param}/{param}" in providers
        assert "http::GET::/api/fetch/{param}" in providers

    def test_php8_attributes(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/Controller/ProductController.php",
            """<?php
namespace App\\Controller;

use Symfony\\Component\\Routing\\Annotation\\Route;

#[Route('/catalog')]
class ProductController
{
    #[Route('/items', methods: ['GET', 'HEAD'])]
    public function index() {}

    #[Route('/items', methods: 'POST')]
    public function create() {}

    #[Route('/items/{id}', methods: ['DELETE'])]
    public function remove(int $id) {}
}
""",
        )
        providers = _providers(tmp_path)
        assert set(providers.keys()) == {
            "http::GET::/catalog/items",
            "http::POST::/catalog/items",
            "http::DELETE::/catalog/items/{param}",
        }


class TestSlimRoutes:
    def test_slim_routes_and_groups(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "public/index.php",
            """<?php
use Slim\\Factory\\AppFactory;

$app = AppFactory::create();

$app->get('/health', function ($request, $response) {
    return $response;
});

$app->group('/api/v1', function ($group) {
    $group->get('/orders', function ($request, $response) {});
    $group->post('/orders', function ($request, $response) {});

    $group->group('/users', function ($nested) {
        $nested->get('/{id}', function ($request, $response) {});
        $nested->delete('/{id}', function ($request, $response) {});
    });
});
""",
        )
        providers = _providers(tmp_path)
        assert set(providers.keys()) == {
            "http::GET::/health",
            "http::GET::/api/v1/orders",
            "http::POST::/api/v1/orders",
            "http::GET::/api/v1/users/{param}",
            "http::DELETE::/api/v1/users/{param}",
        }
        assert providers["http::GET::/health"] == ContractOrigin.ROUTE_REGISTRATION
