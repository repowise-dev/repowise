"""Advisory tiers over synthetic records: roots, reach, and the fold."""

from __future__ import annotations

from dataclasses import dataclass

from repowise.core.analysis.execution_graph import ExecutionGraphIndex
from repowise.core.inventory.vuln_reach import (
    MAX_SITES,
    TIERS,
    AffectedImport,
    ImportSite,
    production_roots,
    tier_advisory,
)


@dataclass(frozen=True)
class Call:
    """The call-site record's fields the fold reads."""

    caller_symbol_id: str
    module: str
    symbol_path: str
    line: int

    @property
    def file_path(self) -> str:
        return self.caller_symbol_id.partition("::")[0]


YAML_LOAD = (AffectedImport("yaml", ("load",)),)
PARSE_IMPORT = [ImportSite("app/parse.py", "yaml", 1)]
LOAD_CALL = Call("app/parse.py::read_config", "yaml", "load", 12)


def _reach(calls: dict[str, list[str]], **roots) -> frozenset[str]:
    index = ExecutionGraphIndex(calls=calls)
    return frozenset(index.forward_reachable(production_roots(**roots)))


def test_tiers_strongest_first():
    assert TIERS == (
        "reachable",
        "package_called",
        "imported",
        "indirect_unknown",
        "test_or_dev_only",
        "not_imported",
    )


# -- roots ---------------------------------------------------------------


def test_roots_are_entry_modules_route_handlers_and_cli_commands():
    roots = production_roots(
        entry_files=["app/main.py"],
        route_handlers=["app/api.py::list_users", None],
        cli_commands=["app/cli.py::serve"],
    )
    assert roots == {"app/main.py::__module__", "app/api.py::list_users", "app/cli.py::serve"}


def test_roots_outside_production_code_are_dropped():
    roots = production_roots(
        entry_files=["tests/test_main.py", "docs_src/tutorial/main.py", "scripts/release.py"],
        route_handlers=["tests/conftest.py::fake_route"],
        cli_commands=["examples/demo.py::main"],
    )
    assert roots == frozenset()


def test_stored_origin_wins_over_the_path_rule():
    roots = production_roots(
        entry_files=["app/main.py", "tools/gen.py"],
        origins={"app/main.py": "generated", "tools/gen.py": "production"},
    )
    assert roots == {"tools/gen.py::__module__"}


def test_reach_walks_from_roots_over_call_edges():
    reached = _reach(
        {
            "app/main.py::__module__": ["app/svc.py::run"],
            "app/svc.py::run": ["app/parse.py::read_config"],
            "app/unused.py::old": ["app/parse.py::legacy"],
        },
        entry_files=["app/main.py"],
    )
    assert "app/parse.py::read_config" in reached
    assert "app/parse.py::legacy" not in reached


# -- reachable and package_called ----------------------------------------


def test_reachable_when_a_reached_function_calls_a_named_symbol():
    reached = _reach(
        {"app/api.py::upload": ["app/parse.py::read_config"]},
        route_handlers=["app/api.py::upload"],
    )
    result = tier_advisory(
        ecosystem="pypi",
        affected=YAML_LOAD,
        import_sites=PARSE_IMPORT,
        call_sites=[LOAD_CALL],
        reachable=reached,
    )
    assert (result.tier, result.basis) == ("reachable", "call")
    assert [(s.path, s.line, s.symbol) for s in result.sites] == [("app/parse.py", 12, "yaml.load")]


def test_a_reached_call_to_another_symbol_is_package_called():
    result = tier_advisory(
        ecosystem="pypi",
        affected=YAML_LOAD,
        import_sites=PARSE_IMPORT,
        call_sites=[Call("app/parse.py::read_config", "yaml", "safe_load", 12)],
        reachable={"app/parse.py::read_config"},
    )
    assert (result.tier, result.basis) == ("package_called", "call")
    assert [s.symbol for s in result.sites] == ["yaml.safe_load"]


def test_an_advisory_naming_no_symbols_reaches_package_called_at_most():
    # OSV names no affected functions for PyPI and npm advisories.
    result = tier_advisory(
        ecosystem="npm",
        affected=(AffectedImport("express"),),
        import_sites=[ImportSite("src/server.js", "express")],
        call_sites=[Call("src/server.js::__module__", "express", "", 3)],
        reachable={"src/server.js::__module__"},
    )
    assert (result.tier, result.basis) == ("package_called", "call")
    assert [(s.path, s.line, s.symbol) for s in result.sites] == [("src/server.js", 3, "express")]


def test_a_call_no_root_reaches_stays_imported():
    result = tier_advisory(
        ecosystem="pypi",
        affected=YAML_LOAD,
        import_sites=PARSE_IMPORT,
        call_sites=[LOAD_CALL],
        reachable=frozenset(),
    )
    assert (result.tier, result.basis) == ("imported", "import")
    assert [s.path for s in result.sites] == ["app/parse.py"]


def test_a_reached_call_from_test_code_proves_nothing():
    result = tier_advisory(
        ecosystem="pypi",
        affected=YAML_LOAD,
        import_sites=PARSE_IMPORT,
        call_sites=[Call("tests/helpers.py::load_fixture", "yaml", "load", 4)],
        reachable={"tests/helpers.py::load_fixture"},
    )
    assert result.tier == "imported"


def test_qualified_affected_symbols_match_exactly():
    base = dict(
        ecosystem="pypi",
        affected=(AffectedImport("requests", ("Session.get",)),),
        import_sites=[ImportSite("app/http.py", "requests")],
        reachable={"app/http.py::fetch"},
    )
    exact = Call("app/http.py::fetch", "requests", "Session.get", 5)
    bare = Call("app/http.py::fetch", "requests", "get", 6)
    assert tier_advisory(**base, call_sites=[exact]).tier == "reachable"
    assert tier_advisory(**base, call_sites=[bare]).tier == "package_called"


def test_default_binding_and_submodule_calls_match_named_symbols():
    base = dict(
        ecosystem="npm",
        affected=(AffectedImport("lodash", ("merge",)),),
        import_sites=[ImportSite("src/a.ts", "lodash")],
        reachable={"src/a.ts::f"},
    )
    default = Call("src/a.ts::f", "lodash", "default.merge", 3)
    sub = Call("src/a.ts::f", "lodash/fp", "merge", 4)
    assert tier_advisory(**base, call_sites=[default]).tier == "reachable"
    assert tier_advisory(**base, call_sites=[sub]).tier == "reachable"


def test_missing_call_records_stay_imported():
    result = tier_advisory(
        ecosystem="pypi",
        affected=YAML_LOAD,
        import_sites=PARSE_IMPORT,
        call_sites=[LOAD_CALL],
        reachable={"app/parse.py::read_config"},
        call_sites_known=False,
    )
    assert (result.tier, result.basis) == ("imported", "no_call_records")


# -- below imported: only with proof -------------------------------------


TEST_ONLY_IMPORTS = [
    ImportSite("tests/test_parse.py", "yaml", 2),
    ImportSite("scripts/gen_fixtures.py", "yaml", 1),
    ImportSite("docs_src/tutorial/load.py", "yaml", 1),
]


def test_direct_dependency_imported_only_by_tests_and_tooling():
    result = tier_advisory(
        ecosystem="pypi",
        affected=YAML_LOAD,
        import_sites=TEST_ONLY_IMPORTS,
        dependency="direct",
    )
    assert (result.tier, result.basis) == ("test_or_dev_only", "non_shipping_imports_only")
    assert len(result.sites) == 3


def test_direct_dependency_no_file_imports_is_not_imported():
    result = tier_advisory(
        ecosystem="pypi", affected=YAML_LOAD, import_sites=[], dependency="direct"
    )
    assert (result.tier, result.basis, result.sites) == ("not_imported", "no_import_site", ())


def test_transitive_dependency_is_indirect_unknown_never_buried():
    # repo -> dep A -> vulnerable dep B: B's callers live in A, out of sight.
    for imports in ([], TEST_ONLY_IMPORTS):
        result = tier_advisory(
            ecosystem="go",
            affected=(AffectedImport("golang.org/x/text/language", ("Parse",)),),
            import_sites=imports,
            dependency="indirect",
        )
        assert (result.tier, result.basis) == ("indirect_unknown", "indirect_dependency")


def test_unknown_dependency_graph_takes_the_safer_label():
    for imports in ([], TEST_ONLY_IMPORTS):
        result = tier_advisory(ecosystem="pypi", affected=YAML_LOAD, import_sites=imports)
        assert (result.tier, result.basis) == ("indirect_unknown", "dependency_graph_unknown")


def test_bundle_metadata_only_package_is_never_elevated():
    # Named only by an SBOM or a bundled package.json, imported by no file.
    tar = (AffectedImport("tar", ("x",)),)
    assert tier_advisory(ecosystem="npm", affected=tar, import_sites=[]).tier == "indirect_unknown"
    direct = tier_advisory(ecosystem="npm", affected=tar, import_sites=[], dependency="direct")
    assert direct.tier == "not_imported"


def test_vendored_and_generated_imports_ship_so_they_stay_imported():
    for origin in ("vendored", "generated", "data"):
        result = tier_advisory(
            ecosystem="pypi",
            affected=YAML_LOAD,
            import_sites=[ImportSite("lib/x.py", "yaml")],
            dependency="direct",
            origins={"lib/x.py": origin},
        )
        assert result.tier == "imported", origin


def test_one_shipping_import_beats_any_number_of_test_imports():
    result = tier_advisory(
        ecosystem="pypi",
        affected=YAML_LOAD,
        import_sites=[ImportSite("tests/test_a.py", "yaml"), ImportSite("app/a.py", "yaml")],
        dependency="direct",
    )
    assert result.tier == "imported"
    assert [s.path for s in result.sites] == ["app/a.py"]


# -- Go: import-path granularity -----------------------------------------


GO_AFFECTED = (AffectedImport("golang.org/x/net/html", ("Parse",)),)
GO_RENDER = "internal/page/render.go"


def test_go_reachable_when_a_reached_file_imports_the_affected_path():
    reached = _reach(
        {"cmd/srv/main.go::__module__": [f"{GO_RENDER}::Render"]},
        entry_files=["cmd/srv/main.go"],
    )
    result = tier_advisory(
        ecosystem="go",
        affected=GO_AFFECTED,
        import_sites=[ImportSite(GO_RENDER, "golang.org/x/net/html", 4)],
        reachable=reached,
        call_sites_known=False,
    )
    assert (result.tier, result.basis) == ("reachable", "import_path")
    assert [(s.path, s.line) for s in result.sites] == [(GO_RENDER, 4)]


def test_go_sibling_package_path_stays_imported():
    result = tier_advisory(
        ecosystem="go",
        affected=GO_AFFECTED,
        import_sites=[ImportSite(GO_RENDER, "golang.org/x/net/http2")],
        reachable={f"{GO_RENDER}::Render"},
        call_sites_known=False,
    )
    assert result.tier == "imported"


def test_go_import_in_a_file_nothing_reaches_stays_imported():
    result = tier_advisory(
        ecosystem="go",
        affected=GO_AFFECTED,
        import_sites=[ImportSite(GO_RENDER, "golang.org/x/net/html")],
        reachable={"cmd/srv/main.go::__module__"},
        call_sites_known=False,
    )
    assert result.tier == "imported"


def test_go_advisory_naming_only_paths_stays_imported():
    result = tier_advisory(
        ecosystem="go",
        affected=(AffectedImport("golang.org/x/net/html"),),
        import_sites=[ImportSite(GO_RENDER, "golang.org/x/net/html")],
        reachable={f"{GO_RENDER}::Render"},
        call_sites_known=False,
    )
    assert result.tier == "imported"


def test_sites_are_capped_and_ordered():
    sites = [ImportSite(f"app/m{i}.py", "yaml", i) for i in range(6, 0, -1)]
    result = tier_advisory(ecosystem="pypi", affected=(), import_sites=sites)
    assert len(result.sites) == MAX_SITES
    assert [s.path for s in result.sites] == ["app/m1.py", "app/m2.py", "app/m3.py"]


def test_a_sibling_npm_package_is_not_a_submodule():
    result = tier_advisory(
        ecosystem="npm",
        affected=(AffectedImport("lodash", ("merge",)),),
        import_sites=[ImportSite("src/a.ts", "lodash")],
        call_sites=[Call("src/a.ts::f", "lodash.merge", "", 3)],
        reachable={"src/a.ts::f"},
    )
    assert result.tier == "package_called"


def test_a_call_in_a_file_stored_as_non_shipping_proves_nothing():
    result = tier_advisory(
        ecosystem="pypi",
        affected=YAML_LOAD,
        import_sites=PARSE_IMPORT,
        call_sites=[LOAD_CALL],
        reachable={"app/parse.py::read_config"},
        origins={"app/parse.py": "tooling"},
        dependency="indirect",
    )
    assert result.tier == "indirect_unknown"


def test_an_indirect_dependency_production_imports_is_imported():
    result = tier_advisory(
        ecosystem="pypi", affected=YAML_LOAD, import_sites=PARSE_IMPORT, dependency="indirect"
    )
    assert (result.tier, result.basis) == ("imported", "import")


def test_go_import_only_in_test_code_is_not_reachable():
    result = tier_advisory(
        ecosystem="go",
        affected=GO_AFFECTED,
        import_sites=[ImportSite("internal/page/render_test.go", "golang.org/x/net/html")],
        reachable={"internal/page/render_test.go::TestRender"},
        dependency="direct",
        call_sites_known=False,
    )
    assert result.tier == "test_or_dev_only"
