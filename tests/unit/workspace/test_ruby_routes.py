"""Ruby HTTP providers: Rails routes.rb, Sinatra and Grape."""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.origins import ContractOrigin


def _providers(repo: Path) -> dict[str, ContractOrigin]:
    return {
        c.contract_id: c.origin
        for c in HttpExtractor().extract(repo, "api")
        if c.role == "provider"
    }


def _provider_ids(repo: Path) -> set[str]:
    return set(_providers(repo).keys())


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestRailsRoutes:
    def test_fixture_extracts_expected_contracts(self) -> None:
        fixture_repo = Path(__file__).parents[2] / "fixtures" / "workspace_http" / "rails"
        providers = _providers(fixture_repo)
        ids = set(providers.keys())

        # Root route
        assert "http::GET::/" in ids
        assert providers["http::GET::/"] == ContractOrigin.ROUTE_REGISTRATION

        # Direct routes
        assert "http::GET::/rss" in ids
        assert "http::GET::/hottest" in ids
        assert "http::GET::/page/{param}" in ids
        assert "http::GET::/active" in ids

        # Match ... via:
        assert "http::GET::/login/set_new_password" in ids
        assert "http::POST::/login/set_new_password" in ids

        # Resources with except: [:index]
        assert "http::GET::/stories" not in ids
        assert "http::POST::/stories" in ids
        assert "http::GET::/stories/{param}" in ids
        assert "http::PUT::/stories/{param}" in ids
        assert "http::DELETE::/stories/{param}" in ids
        assert "http::POST::/stories/{param}/upvote" in ids

        # Nested resources with only: [:new, :create]
        assert "http::GET::/stories/{param}/suggestions/new" in ids
        assert "http::POST::/stories/{param}/suggestions" in ids
        assert "http::GET::/stories/{param}/suggestions" not in ids

        # Member block and collection block
        assert "http::GET::/comments/{param}/reply" in ids
        assert "http::POST::/comments/{param}/delete" in ids
        assert "http::GET::/comments/search" in ids

        # Namespaced routes
        assert "http::GET::/admin/users" in ids
        assert "http::GET::/admin/users/{param}" in ids
        assert "http::GET::/admin/dashboard" in ids

        # Scoped routes
        assert "http::GET::/v1/status" in ids

        # Commented-out route yields nothing
        assert not any("commented" in cid for cid in ids)

    def test_singular_resource(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "config/routes.rb",
            """Rails.application.routes.draw do
  resource :profile, only: [:show, :update] do
    member do
      get :settings
    end
  end
end
""",
        )
        ids = _provider_ids(tmp_path)
        assert ids == {
            "http::GET::/profile",
            "http::PUT::/profile",
            "http::PATCH::/profile",
            "http::GET::/profile/settings",
        }


class TestSinatraRoutes:
    def test_basic_and_modular_sinatra(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app.rb",
            """require 'sinatra'

get '/hello' do
  'world'
end

post '/items' do
  'created'
end

# get '/commented' do
#   'nope'
# end

class AdminApp < Sinatra::Base
  delete '/users/:id' do
    'deleted'
  end
end
""",
        )
        providers = _providers(tmp_path)
        ids = set(providers.keys())
        assert ids == {
            "http::GET::/hello",
            "http::POST::/items",
            "http::DELETE::/users/{param}",
        }
        assert providers["http::GET::/hello"] == ContractOrigin.ROUTE_REGISTRATION


class TestGrapeRoutes:
    def test_grape_api_structure(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "api.rb",
            """module Shop
  class API < Grape::API
    prefix :api
    version 'v1', using: :path

    resource :orders do
      get do
        # all orders
      end

      post '/new' do
        # new order
      end

      # get '/commented' do
      # end

      route_param :id do
        get do
          # one order
        end

        delete do
          # delete order
        end
      end
    end
  end
end
""",
        )
        providers = _providers(tmp_path)
        ids = set(providers.keys())
        assert ids == {
            "http::GET::/api/v1/orders",
            "http::POST::/api/v1/orders/new",
            "http::GET::/api/v1/orders/{param}",
            "http::DELETE::/api/v1/orders/{param}",
        }
        assert providers["http::GET::/api/v1/orders"] == ContractOrigin.ROUTE_REGISTRATION
