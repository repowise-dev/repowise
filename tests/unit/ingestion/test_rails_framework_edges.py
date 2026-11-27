"""Unit tests for Rails framework edges (F2)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext


def _file_info(rel: str, abs_path: str, language: str = "ruby") -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=abs_path,
        language=language,
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=rel.startswith("config/"),
        is_api_contract=False,
        is_entry_point=False,
    )


def _build_parsed(repo: Path) -> dict[str, ParsedFile]:
    parser = ASTParser()
    out: dict[str, ParsedFile] = {}
    for rb in repo.rglob("*.rb"):
        rel = rb.resolve().relative_to(repo.resolve()).as_posix()
        fi = _file_info(rel, str(rb.resolve()))
        out[rel] = parser.parse_file(fi, rb.read_bytes())
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


def _make_rails_app(repo: Path) -> None:
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "application.rb").write_text("module App; class Application; end; end\n")


class TestRailsRoutes:
    def test_resources_routes_to_controller(self, tmp_path: Path) -> None:
        _make_rails_app(tmp_path)
        (tmp_path / "config" / "routes.rb").write_text(
            "Rails.application.routes.draw do\n  resources :users\nend\n"
        )
        ctrl_dir = tmp_path / "app" / "controllers"
        ctrl_dir.mkdir(parents=True)
        (ctrl_dir / "users_controller.rb").write_text("class UsersController; end\n")

        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["rails"])
        assert graph.has_edge("config/routes.rb", "app/controllers/users_controller.rb")

    def test_get_to_routes_to_controller(self, tmp_path: Path) -> None:
        _make_rails_app(tmp_path)
        (tmp_path / "config" / "routes.rb").write_text(
            'Rails.application.routes.draw do\n  get "/foo", to: "users#index"\nend\n'
        )
        ctrl_dir = tmp_path / "app" / "controllers"
        ctrl_dir.mkdir(parents=True)
        (ctrl_dir / "users_controller.rb").write_text("class UsersController; end\n")

        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["rails"])
        assert graph.has_edge("config/routes.rb", "app/controllers/users_controller.rb")

    def test_namespace_block_resolves_nested_controller(self, tmp_path: Path) -> None:
        _make_rails_app(tmp_path)
        (tmp_path / "config" / "routes.rb").write_text(
            "Rails.application.routes.draw do\n"
            "  namespace :admin do\n"
            "    resources :reports\n"
            "  end\n"
            "end\n"
        )
        admin_dir = tmp_path / "app" / "controllers" / "admin"
        admin_dir.mkdir(parents=True)
        (admin_dir / "reports_controller.rb").write_text(
            "module Admin; class ReportsController; end; end\n"
        )

        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["rails"])
        assert graph.has_edge(
            "config/routes.rb", "app/controllers/admin/reports_controller.rb"
        )


class TestRailsActiveRecord:
    def test_belongs_to_links_models(self, tmp_path: Path) -> None:
        _make_rails_app(tmp_path)
        models_dir = tmp_path / "app" / "models"
        models_dir.mkdir(parents=True)
        (models_dir / "order.rb").write_text(
            "class Order < ApplicationRecord\n  belongs_to :user\nend\n"
        )
        (models_dir / "user.rb").write_text("class User < ApplicationRecord; end\n")

        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["rails"])
        assert graph.has_edge("app/models/order.rb", "app/models/user.rb")

    def test_has_many_singularizes(self, tmp_path: Path) -> None:
        _make_rails_app(tmp_path)
        models_dir = tmp_path / "app" / "models"
        models_dir.mkdir(parents=True)
        (models_dir / "user.rb").write_text(
            "class User < ApplicationRecord\n  has_many :orders\nend\n"
        )
        (models_dir / "order.rb").write_text("class Order < ApplicationRecord; end\n")

        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["rails"])
        assert graph.has_edge("app/models/user.rb", "app/models/order.rb")


class TestRailsGate:
    def test_non_rails_repo_unaffected(self, tmp_path: Path) -> None:
        # Plain Ruby with similar-looking code but no config/application.rb
        (tmp_path / "models.rb").write_text(
            "class Order; belongs_to :user; end\nclass User; end\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        count = add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert count == 0


def _edges_for(tmp_path: Path, files: dict[str, str]) -> nx.DiGraph:
    _make_rails_app(tmp_path)
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    parsed = _build_parsed(tmp_path)
    graph = nx.DiGraph()
    for p in parsed:
        graph.add_node(p)
    add_framework_edges(graph, parsed, _ctx(tmp_path, parsed), tech_stack=["rails"])
    return graph


class TestRailsRouteScopes:
    def test_scope_module_and_nested_resources(self, tmp_path: Path) -> None:
        graph = _edges_for(
            tmp_path,
            {
                "config/routes.rb": (
                    "Rails.application.routes.draw do\n"
                    "  namespace :api do\n"
                    "    resources :accounts, only: [:show] do\n"
                    "      member do\n"
                    "        post :touch\n"
                    "      end\n"
                    "      scope module: :accounts do\n"
                    "        if Feature.enabled?\n"
                    "          resources :monitors\n"
                    "        end\n"
                    "        resources :conversations do\n"
                    "          resources :messages, only: [:index]\n"
                    "        end\n"
                    "        resources :inboxes, only: [:index], module: :policies\n"
                    "        get 'stats', to: 'reports#index'\n"
                    "      end\n"
                    "    end\n"
                    "  end\n"
                    "end\n"
                ),
                "app/controllers/api/accounts_controller.rb": "class Api::AccountsController; end\n",
                "app/controllers/api/accounts/monitors_controller.rb": "class X; end\n",
                "app/controllers/api/accounts/conversations_controller.rb": "class X; end\n",
                "app/controllers/api/accounts/messages_controller.rb": "class X; end\n",
                "app/controllers/api/accounts/policies/inboxes_controller.rb": "class X; end\n",
                "app/controllers/api/accounts/reports_controller.rb": "class X; end\n",
                # Same file names one namespace off: a stack that lost a frame
                # would bind the routes above to these.
                "app/controllers/admin/messages_controller.rb": "class X; end\n",
                "app/controllers/admin/inboxes_controller.rb": "class X; end\n",
            },
        )
        routes = "config/routes.rb"
        for controller in (
            "api/accounts_controller",
            "api/accounts/monitors_controller",
            "api/accounts/conversations_controller",
            "api/accounts/messages_controller",
            "api/accounts/policies/inboxes_controller",
            "api/accounts/reports_controller",
        ):
            assert graph.has_edge(routes, f"app/controllers/{controller}.rb"), controller
        assert not graph.has_edge(routes, "app/controllers/admin/messages_controller.rb")
        assert not graph.has_edge(routes, "app/controllers/admin/inboxes_controller.rb")

    def test_drawn_routes_file_keeps_the_drawers_scope(self, tmp_path: Path) -> None:
        graph = _edges_for(
            tmp_path,
            {
                "config/routes.rb": (
                    "Rails.application.routes.draw do\n"
                    "  namespace :api do\n    draw(:v1)\n  end\nend\n"
                ),
                "config/routes/v1.rb": "resources :widgets\n",
                "app/controllers/api/widgets_controller.rb": "class X; end\n",
            },
        )
        assert graph.has_edge("config/routes.rb", "config/routes/v1.rb")
        assert graph.has_edge("config/routes/v1.rb", "app/controllers/api/widgets_controller.rb")


class TestRailsConventionEntries:
    def test_framework_loaded_files_are_anchored(self, tmp_path: Path) -> None:
        graph = _edges_for(
            tmp_path,
            {
                "config/initializers/sidekiq.rb": "Sidekiq.configure_server { }\n",
                "config/environments/production.rb": "Rails.application.configure { }\n",
                "enterprise/config/initializers/billing.rb": "x = 1\n",
                "app/channels/application_cable/connection.rb": "module ApplicationCable; end\n",
                "app/channels/room_channel.rb": "class RoomChannel; end\n",
                "app/helpers/application_helper.rb": "module ApplicationHelper; end\n",
                "app/services/plain_service.rb": "class PlainService; end\n",
            },
        )
        anchored = set(graph.successors("framework:rails"))
        assert {
            "config/application.rb",
            "config/initializers/sidekiq.rb",
            "config/environments/production.rb",
            "enterprise/config/initializers/billing.rb",
            "app/channels/application_cable/connection.rb",
            "app/channels/room_channel.rb",
            "app/helpers/application_helper.rb",
        } <= anchored
        assert "app/services/plain_service.rb" not in anchored

    def test_overlay_policy_mailbox_and_schedule(self, tmp_path: Path) -> None:
        graph = _edges_for(
            tmp_path,
            {
                "app/models/account.rb": (
                    "class Account < ApplicationRecord\nend\n\nAccount.prepend_mod_with('Account')\n"
                ),
                "app/finders/user_finder.rb": "class UserFinder\n  prepend_mod\nend\n",
                "enterprise/app/models/enterprise/account.rb": "module Enterprise::Account; end\n",
                "enterprise/app/finders/enterprise/user_finder.rb": "module Enterprise::UserFinder; end\n",
                "app/policies/account_policy.rb": "class AccountPolicy; end\n",
                "app/mailboxes/application_mailbox.rb": (
                    "class ApplicationMailbox < ActionMailbox::Base\n"
                    "  routing(/^reply/i => :reply)\n  routing(all: :default)\nend\n"
                ),
                "app/mailboxes/reply_mailbox.rb": "class ReplyMailbox; end\n",
                "app/mailboxes/default_mailbox.rb": "class DefaultMailbox; end\n",
                "config/schedule.yml": "nightly:\n  cron: '0 0 * * *'\n  class: 'Internal::NightlyJob'\n",
                "app/jobs/internal/nightly_job.rb": "class Internal::NightlyJob; end\n",
            },
        )
        assert graph.has_edge(
            "app/models/account.rb", "enterprise/app/models/enterprise/account.rb"
        )
        assert graph.has_edge(
            "app/finders/user_finder.rb", "enterprise/app/finders/enterprise/user_finder.rb"
        )
        assert graph.has_edge("app/models/account.rb", "app/policies/account_policy.rb")
        mailbox = "app/mailboxes/application_mailbox.rb"
        assert graph.has_edge(mailbox, "app/mailboxes/reply_mailbox.rb")
        assert graph.has_edge(mailbox, "app/mailboxes/default_mailbox.rb")
        assert graph.has_edge("framework:rails", "app/jobs/internal/nightly_job.rb")

    def test_singular_resource_and_controllers_hash(self, tmp_path: Path) -> None:
        graph = _edges_for(
            tmp_path,
            {
                "config/routes.rb": (
                    "Rails.application.routes.draw do\n"
                    "  devise_for :users, controllers: {\n"
                    "    sessions: 'devise_overrides/sessions'\n"
                    "  }\n"
                    "  resource :profile, only: [:show]\n"
                    "  resource :stripe, controller: 'stripe'\n"
                    "  resource :notification_settings\n"
                    "  resource :instance_status\n"
                    "end\n"
                ),
                "app/controllers/devise_overrides/sessions_controller.rb": "class X; end\n",
                "app/controllers/profiles_controller.rb": "class X; end\n",
                "app/controllers/stripe_controller.rb": "class X; end\n",
                "app/controllers/notification_settings_controller.rb": "class X; end\n",
                "app/controllers/instance_statuses_controller.rb": "class X; end\n",
            },
        )
        for controller in (
            "devise_overrides/sessions",
            "profiles",
            "stripe",
            "notification_settings",
            "instance_statuses",
        ):
            assert graph.has_edge("config/routes.rb", f"app/controllers/{controller}_controller.rb")
