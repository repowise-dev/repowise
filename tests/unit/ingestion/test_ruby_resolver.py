"""Unit tests for the Ruby Rails / Zeitwerk-aware import resolver."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.ruby import resolve_ruby_import
from repowise.core.ingestion.resolvers.ruby_rails import (
    build_rails_index,
    camel_to_snake,
)


def _ctx(repo: Path, paths: list[str]) -> ResolverContext:
    path_set = set(paths)
    stem_map: dict[str, list[str]] = {}
    for p in paths:
        stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=path_set,
        stem_map=stem_map,
        graph=nx.DiGraph(),
        repo_path=repo,
    )


def _make_rails_repo(repo: Path) -> None:
    (repo / "config").mkdir()
    (repo / "config" / "application.rb").write_text("module App; class Application; end; end\n")


class TestRailsDetection:
    def test_returns_none_when_not_rails(self, tmp_path: Path) -> None:
        assert build_rails_index(tmp_path) is None

    def test_detects_via_application_rb(self, tmp_path: Path) -> None:
        _make_rails_repo(tmp_path)
        index = build_rails_index(tmp_path)
        assert index is not None


class TestRailsConstantLookup:
    def test_simple_constant(self, tmp_path: Path) -> None:
        _make_rails_repo(tmp_path)
        ctrl = tmp_path / "app" / "controllers"
        ctrl.mkdir(parents=True)
        (ctrl / "users_controller.rb").write_text("class UsersController; end\n")
        index = build_rails_index(tmp_path)
        assert index is not None
        assert index.lookup("UsersController") == "app/controllers/users_controller.rb"

    def test_namespaced_constant(self, tmp_path: Path) -> None:
        _make_rails_repo(tmp_path)
        admin = tmp_path / "app" / "controllers" / "admin"
        admin.mkdir(parents=True)
        (admin / "reports_controller.rb").write_text(
            "module Admin; class ReportsController; end; end\n"
        )
        index = build_rails_index(tmp_path)
        assert index is not None
        result = index.lookup("Admin::ReportsController")
        assert result == "app/controllers/admin/reports_controller.rb"

    def test_camel_to_snake(self) -> None:
        assert camel_to_snake("UserController") == "user_controller"
        assert camel_to_snake("Foo") == "foo"


class TestRubyResolverIntegration:
    def test_require_relative_unaffected(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["lib/foo.rb"])
        result = resolve_ruby_import("./foo", "lib/main.rb", ctx)
        assert result == "lib/foo.rb"

    def test_rails_path_style_require(self, tmp_path: Path) -> None:
        _make_rails_repo(tmp_path)
        svc = tmp_path / "app" / "services"
        svc.mkdir(parents=True)
        (svc / "user_service.rb").write_text("class UserService; end\n")
        ctx = _ctx(tmp_path, ["app/services/user_service.rb", "config/application.rb"])
        result = resolve_ruby_import("app/services/user_service", "main.rb", ctx)
        assert result == "app/services/user_service.rb"

    def test_non_rails_unaffected(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["lib/foo.rb"])
        result = resolve_ruby_import("foo", "main.rb", ctx)
        assert result == "lib/foo.rb"


class TestLoadPathConvention:
    def test_lib_probe_resolves_nested_require(self, tmp_path: Path) -> None:
        # Ruby's $LOAD_PATH convention: require 'sinatra/base' from
        # anywhere in the repo means lib/sinatra/base.rb.
        paths = ["lib/sinatra/base.rb", "lib/sinatra.rb", "test/helper.rb"]
        ctx = _ctx(tmp_path, paths)
        assert resolve_ruby_import("sinatra/base", "test/helper.rb", ctx) == "lib/sinatra/base.rb"

    def test_lib_probe_resolves_root_require(self, tmp_path: Path) -> None:
        paths = ["lib/sinatra.rb", "examples/app.rb"]
        ctx = _ctx(tmp_path, paths)
        assert resolve_ruby_import("sinatra", "examples/app.rb", ctx) == "lib/sinatra.rb"

    def test_repo_root_join_still_works(self, tmp_path: Path) -> None:
        paths = ["helpers/util.rb", "main.rb"]
        ctx = _ctx(tmp_path, paths)
        assert resolve_ruby_import("helpers/util", "main.rb", ctx) == "helpers/util.rb"

    def test_lib_probe_beats_basename_fuzzy_match(self, tmp_path: Path) -> None:
        # base.rb exists in two places; the $LOAD_PATH join is exact and
        # must win over any same-basename fuzzy hit.
        paths = ["lib/sinatra/base.rb", "spec/fixtures/base.rb", "app.rb"]
        ctx = _ctx(tmp_path, paths)
        assert resolve_ruby_import("sinatra/base", "app.rb", ctx) == "lib/sinatra/base.rb"


class TestGemDistinction:
    def test_gemfile_gem_require_labelled_as_gem(self, tmp_path: Path) -> None:
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\ngem 'rack'\n")
        ctx = _ctx(tmp_path, ["app.rb"])
        got = resolve_ruby_import("rack", "app.rb", ctx)
        assert got == "external:gem:rack"

    def test_gem_subpath_require_labelled_as_gem(self, tmp_path: Path) -> None:
        (tmp_path / "Gemfile").write_text("gem 'rack'\n")
        ctx = _ctx(tmp_path, ["app.rb"])
        got = resolve_ruby_import("rack/protection", "app.rb", ctx)
        assert got == "external:gem:rack/protection"

    def test_dashed_gem_name_matches_slash_require(self, tmp_path: Path) -> None:
        (tmp_path / "Gemfile").write_text("gem 'rack-protection'\n")
        ctx = _ctx(tmp_path, ["app.rb"])
        got = resolve_ruby_import("rack/protection", "app.rb", ctx)
        assert got == "external:gem:rack/protection"

    def test_gemspec_dependency_recognised(self, tmp_path: Path) -> None:
        (tmp_path / "mylib.gemspec").write_text(
            'Gem::Specification.new do |s|\n  s.add_dependency "mustermann"\n'
            '  s.add_development_dependency "rack-test"\nend\n'
        )
        ctx = _ctx(tmp_path, ["app.rb"])
        assert resolve_ruby_import("mustermann", "app.rb", ctx) == "external:gem:mustermann"
        # rake itself ships with the interpreter (stdlib drop); a dashed
        # dev dependency keeps the gem label.
        assert resolve_ruby_import("rack/test", "app.rb", ctx) == "external:gem:rack/test"

    def test_unknown_require_stays_plain_external(self, tmp_path: Path) -> None:
        (tmp_path / "Gemfile").write_text("gem 'rack'\n")
        ctx = _ctx(tmp_path, ["app.rb"])
        got = resolve_ruby_import("definitely_not_a_thing", "app.rb", ctx)
        assert got == "external:definitely_not_a_thing"

    def test_local_file_beats_gem_label(self, tmp_path: Path) -> None:
        # The repo IS the gem: require 'sinatra/base' must resolve locally
        # even though sinatra.gemspec names dependencies.
        (tmp_path / "Gemfile").write_text("gem 'sinatra'\n")
        ctx = _ctx(tmp_path, ["lib/sinatra/base.rb", "app.rb"])
        assert resolve_ruby_import("sinatra/base", "app.rb", ctx) == "lib/sinatra/base.rb"


class TestRubyStdlib:
    def test_stdlib_requires_dropped(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["app.rb"])
        assert resolve_ruby_import("stringio", "app.rb", ctx) is None
        assert resolve_ruby_import("net/http", "app.rb", ctx) is None
        assert resolve_ruby_import("open-uri", "app.rb", ctx) is None

    def test_local_file_beats_stdlib_name(self, tmp_path: Path) -> None:
        # The repo may BE the json gem — exact lib/ probe wins.
        ctx = _ctx(tmp_path, ["lib/json.rb", "app.rb"])
        assert resolve_ruby_import("json", "app.rb", ctx) == "lib/json.rb"

    def test_stdlib_never_fuzzy_matches(self, tmp_path: Path) -> None:
        # require 'time' must not stem-match a repo file named time.rb in
        # some unrelated subdirectory.
        ctx = _ctx(tmp_path, ["app/models/time.rb", "app.rb"])
        assert resolve_ruby_import("time", "app.rb", ctx) is None


class TestSpecMirrorEdges:
    def test_spec_mirror_links_subject(self, tmp_path: Path) -> None:
        from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

        (tmp_path / "rack-protection/lib/rack/protection").mkdir(parents=True)
        (tmp_path / "rack-protection/spec/lib/rack/protection").mkdir(parents=True)
        (tmp_path / "rack-protection/lib/rack/protection/base.rb").write_text(
            "module Rack\n  module Protection\n    class Base\n    end\n  end\nend\n"
        )
        (tmp_path / "rack-protection/spec/lib/rack/protection/base_spec.rb").write_text(
            "RSpec.describe Rack::Protection::Base do\nend\n"
        )
        traverser = FileTraverser(tmp_path)
        parser = ASTParser()
        builder = GraphBuilder(repo_path=tmp_path)
        for fi in traverser.traverse():
            builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
        graph = builder.build()
        edge = graph.get_edge_data(
            "rack-protection/spec/lib/rack/protection/base_spec.rb",
            "rack-protection/lib/rack/protection/base.rb",
        )
        assert edge is not None
        assert edge.get("hint_source") == "spec_mirror"

    def test_spec_without_mirror_stays_unlinked(self, tmp_path: Path) -> None:
        from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

        (tmp_path / "spec").mkdir()
        (tmp_path / "lib").mkdir()
        (tmp_path / "spec/integration_spec.rb").write_text("RSpec.describe 'x' do\nend\n")
        (tmp_path / "lib/other.rb").write_text("class Other\nend\n")
        traverser = FileTraverser(tmp_path)
        parser = ASTParser()
        builder = GraphBuilder(repo_path=tmp_path)
        for fi in traverser.traverse():
            builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
        graph = builder.build()
        assert not graph.has_edge("spec/integration_spec.rb", "lib/other.rb")
