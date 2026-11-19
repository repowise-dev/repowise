"""The build-unit tier: a changed file's units and their dependents, or why they cannot vouch."""

from __future__ import annotations

import pytest

from repowise.core.analysis.test_selection import (
    SelectionInput,
    TestSelectionConfig,
    plan_gap,
    select_tests,
)
from repowise.core.analysis.unit_scope import UnitScope, unit_scopes
from repowise.core.build_units import BuildUnit, BuildUnitIndex
from repowise.core.test_runners.unit_runs import UNIT_RUNS

_CORE = BuildUnit("go:ex/core", "go", "ex/core", "core", "core/go.mod", "go-mod")
_API = BuildUnit("go:ex/api", "go", "ex/api", "api", "api/go.mod", "go-mod", deps=("go:ex/core",))
_WEB = BuildUnit("go:ex/web", "go", "ex/web", "web", "web/go.mod", "go-mod", deps=("go:ex/api",))
_TOOL = BuildUnit("go:ex/tool", "go", "ex/tool", "tool", "tool/go.mod", "go-mod")

_TESTS = (
    "core/core_test.go",
    "api/api_test.go",
    "web/web_test.go",
    "tool/tool_test.go",
)


def _index(*units: BuildUnit, failed: frozenset[str] = frozenset()) -> BuildUnitIndex:
    return BuildUnitIndex.from_units(units, [], failed_ecosystems=failed)


_GO = _index(_CORE, _API, _WEB, _TOOL)


def _scope(path: str, index: BuildUnitIndex | None = _GO, tracked=()) -> UnitScope | str:
    return unit_scopes([path], index, _TESTS, tracked)[path]


def test_a_code_file_runs_its_units_tests_and_every_transitive_dependent():
    scope = _scope("core/new.go")
    assert isinstance(scope, UnitScope)
    assert scope.owners == ("go:ex/core",)
    assert scope.units == ("go:ex/api", "go:ex/core", "go:ex/web")
    assert scope.tests == ("core/core_test.go", "api/api_test.go", "web/web_test.go")
    assert "2 unit(s) depending on it" in scope.why


def test_a_leaf_units_manifest_runs_that_unit_and_its_dependents():
    scope = _scope("api/go.mod")
    assert isinstance(scope, UnitScope)
    assert scope.units == ("go:ex/api", "go:ex/web")
    assert scope.why.startswith("it declares go:ex/api")


def test_a_manifest_with_a_unit_below_it_keeps_its_full_run():
    parent = BuildUnit("maven:g:parent", "maven", "parent", "svc", "svc/pom.xml", "maven")
    child = BuildUnit("maven:g:a", "maven", "a", "svc/a", "svc/a/pom.xml", "maven")
    assert "configures maven:g:a" in _scope("svc/pom.xml", _index(parent, child))


@pytest.mark.parametrize(
    ("index", "path"),
    [
        (None, "core/x.go"),
        (BuildUnitIndex(), "core/x.go"),
        (_GO, "elsewhere/x.go"),
        (_GO, "core/data.json"),
        (_GO, "Cargo.lock"),
    ],
)
def test_the_tier_does_not_apply(index, path):
    assert _scope(path, index) is None


def test_the_repository_root_unit_is_refused():
    root = BuildUnit("go:ex", "go", "ex", "", "go.mod", "go-mod")
    assert "repository-root unit go:ex" in _scope("main.go", _index(root, _CORE))
    assert "repository-root unit go:ex" in _scope("go.mod", _index(root))


def test_an_untrusted_or_unknown_ecosystem_is_refused():
    npm = BuildUnit("npm:a", "npm", "a", "a", "a/package.json", "npm-workspace")
    assert UNIT_RUNS["npm"].why in _scope("a/x.ts", _index(npm))
    cargo = BuildUnit("cargo:c", "cargo", "c", "c", "c/Cargo.toml", "cargo")
    assert "cargo are not used for test selection" in _scope("c/src/lib.rs", _index(cargo))
    fallback = BuildUnit("manifest:r", "manifest", "r", "r", "r/Gemfile", "manifest-root")
    assert "manifest are not used" in _scope("r/x.rb", _index(fallback))


def test_a_failed_reader_refuses_its_ecosystem():
    assert "go reader failed" in _scope("core/x.go", _index(_CORE, failed=frozenset({"go"})))


def test_a_tracked_go_work_lifts_gos_enforcement():
    assert "go.work" in _scope("core/x.go", tracked=["go.work", "core/x.go"])


def test_a_same_language_fallback_unit_refuses_and_a_fixture_one_does_not():
    broken = BuildUnit("manifest:lib", "manifest", "lib", "lib", "lib/go.mod", "manifest-root")
    assert "lib/go.mod fell back" in _scope("core/x.go", _index(_CORE, broken))
    fixture = BuildUnit(
        "manifest:testdata/m", "manifest", "m", "testdata/m", "testdata/m/go.mod", "manifest-root"
    )
    assert isinstance(_scope("core/x.go", _index(_CORE, fixture)), UnitScope)
    ruby = BuildUnit("manifest:r", "manifest", "r", "r", "r/Gemfile", "manifest-root")
    assert isinstance(_scope("core/x.go", _index(_CORE, ruby)), UnitScope)


def test_units_owning_no_known_test_keep_the_full_run():
    assert (
        "own no known test" in unit_scopes(["tool/x.go"], _GO, ["core/core_test.go"])["tool/x.go"]
    )


# -- in the selection ---------------------------------------------------------


def _select(changed, inferred=(), unknown=(), deleted=(), units=_GO, **kwargs):
    tiers = {"covered": {}, "inferred": list(inferred), "unknown": list(unknown)}
    return select_tests(
        SelectionInput(
            changed,
            deleted,
            tiers,
            TestSelectionConfig(),
            known_tests=_TESTS,
            units=units,
            **kwargs,
        )
    )


def test_a_file_no_test_reaches_runs_its_units_tests_counted_apart():
    selection = _select(["core/new.go"], unknown=["core/new.go"])
    assert not selection.run_all, selection.reasons
    assert set(selection.test_files) == {"core/core_test.go", "api/api_test.go", "web/web_test.go"}
    assert selection.basis["core/new.go"] == "build-unit"
    assert selection.build_unit == dict.fromkeys(selection.test_files, "go:ex/core")
    assert selection.reached_tests() == []
    assert "build-unit:" in selection.why["api/api_test.go"]


def test_a_filename_guess_alone_goes_to_the_units():
    guess = {
        "source_file": "core/x.go",
        "test_file": "tool/tool_test.go",
        "via": "filename-pattern",
    }
    selection = _select(["core/x.go"], inferred=[guess])
    assert not selection.run_all
    assert "tool/tool_test.go" not in selection.test_files


def test_a_graph_answer_wins_and_its_tests_are_not_counted_apart():
    edge = {"source_file": "core/x.go", "test_file": "core/core_test.go", "via": "import-graph"}
    selection = _select(["core/x.go", "api/new.go"], inferred=[edge], unknown=["api/new.go"])
    assert selection.basis == {"core/x.go": "import-graph", "api/new.go": "build-unit"}
    assert "core/core_test.go" not in selection.build_unit
    assert set(selection.build_unit) == {"api/api_test.go", "web/web_test.go"}


def test_a_declined_file_keeps_its_reason_and_says_why():
    unknown = _select(["core/new.go"], unknown=["core/new.go"], units=None)
    assert unknown.run_all
    assert unknown.reasons[0] == (
        "core/new.go: no coverage, no test reaching it in the graph, no paired test."
    )
    root = BuildUnit("go:ex", "go", "ex", "", "go.mod", "go-mod")
    refused = _select(["main.go"], unknown=["main.go"], units=_index(root, _CORE))
    assert refused.run_all
    assert "build units cannot vouch: the repository-root unit go:ex" in refused.reasons[0]


def test_a_units_own_manifest_runs_it_and_a_shared_lockfile_does_not():
    dotnet = BuildUnit("dotnet:a/A.csproj", "dotnet", "A", "a", "a/A.csproj", "msbuild")
    index = _index(dotnet)
    selection = select_tests(
        SelectionInput(
            ["a/A.csproj"],
            (),
            {"covered": {}, "inferred": [], "unknown": ["a/A.csproj"]},
            known_tests=["a/ATests.cs"],
            units=index,
        )
    )
    assert not selection.run_all, selection.reasons
    assert selection.test_files == ("a/ATests.cs",)
    locked = _select(["go.sum"])
    assert locked.run_all


def test_a_build_file_full_run_on_lists_is_never_answered():
    maven = BuildUnit("maven:g:a", "maven", "a", "a", "a/pom.xml", "maven")
    config = TestSelectionConfig(full_run_on=("**/pom.xml",))
    selection = select_tests(
        SelectionInput(
            ["a/pom.xml"],
            (),
            {"covered": {}, "inferred": [], "unknown": []},
            config,
            known_tests=["a/src/test/java/ATest.java"],
            units=_index(maven),
        )
    )
    assert selection.run_all


def test_a_dependencies_reason_no_longer_skips_the_index_gap_trace():
    plan = plan_gap(["core/old.go"], TestSelectionConfig(), change=["a/pom.xml"])
    assert plan.candidates == ("core/old.go",)
    # Any other full-run reason still makes the trace moot.
    assert (
        plan_gap(["core/old.go"], TestSelectionConfig(), change=[".gitlab-ci.yml"]).candidates == ()
    )


def test_a_dependent_holding_test_code_but_no_known_test_keeps_the_full_run():
    # A Reqnroll project: step definitions are test code, but no file is test-shaped.
    lib = BuildUnit(
        "dotnet:src/Lib/Lib.csproj", "dotnet", "Lib", "src/Lib", "src/Lib/Lib.csproj", "msbuild"
    )
    unit = BuildUnit(
        "dotnet:tests/Lib.UnitTests/Lib.UnitTests.csproj",
        "dotnet",
        "Lib.UnitTests",
        "tests/Lib.UnitTests",
        "tests/Lib.UnitTests/Lib.UnitTests.csproj",
        "msbuild",
        deps=(lib.id,),
    )
    accept = BuildUnit(
        "dotnet:tests/Accept/Accept.csproj",
        "dotnet",
        "Accept",
        "tests/Accept",
        "tests/Accept/Accept.csproj",
        "msbuild",
        deps=(lib.id,),
    )
    files = [
        "src/Lib/Lib.cs",
        "tests/Lib.UnitTests/LibTests.cs",
        "tests/Accept/Steps/LoginSteps.cs",
    ]
    index = BuildUnitIndex.from_units([lib, unit, accept], files)
    known = ["tests/Lib.UnitTests/LibTests.cs"]
    scope = unit_scopes(["src/Lib/Lib.cs"], index, known)["src/Lib/Lib.cs"]
    assert scope == f"{accept.id} holds test code but no known test, so its tests cannot be listed"
    # Without the step project, the unit tests stand for the change.
    index = BuildUnitIndex.from_units([lib, unit], files[:2])
    assert isinstance(unit_scopes(["src/Lib/Lib.cs"], index, known)["src/Lib/Lib.cs"], UnitScope)
