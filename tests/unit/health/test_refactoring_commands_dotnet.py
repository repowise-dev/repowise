"""A plan's C# tests run through ``dotnet test`` on the test project that holds them."""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.refactoring.batches import stage_batches
from repowise.core.analysis.health.refactoring.commands import (
    commands_for_tests,
    plan_runs,
    runs_for_tests,
)
from repowise.core.analysis.health.refactoring.recommendations import (
    ValidationEvidence,
    build_validation_plan,
)
from repowise.core.analysis.health.refactoring.serving import validation_from_profile
from repowise.core.analysis.test_reachability import ReachedBy
from repowise.core.ingestion.package_roots import scan_package_manifests
from tests.unit.health.test_recommendations import _plan

SHOP = "src/Shop/Shop.csproj"
TESTS = "tests/Shop.Tests/Shop.Tests.csproj"
CART_TESTS = "tests/Shop.Tests/CartTests.cs"
XUNIT = '<PackageReference Include="Microsoft.NET.Test.Sdk" /><PackageReference Include="xunit" />'
NO_COVERAGE = "-p:CollectCoverage=false"


def _project(root: Path, path: str, *, refs: tuple[str, ...] = (), body: str = "") -> None:
    project = root / path
    project.parent.mkdir(parents=True, exist_ok=True)
    items = "".join(f'<ProjectReference Include="{ref}" />' for ref in refs)
    project.write_text(
        f'<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>{items}{body}</ItemGroup></Project>',
        encoding="utf-8",
    )


def _shop(root: Path) -> None:
    """The issue's repro: a library and an xUnit project referencing it."""
    (root / "Shop.sln").write_text("", encoding="utf-8")
    _project(root, SHOP)
    _project(root, TESTS, refs=("..\\..\\src\\Shop\\Shop.csproj",), body=XUNIT)


def _build_props(root: Path, directory: str, body: str) -> None:
    (root / directory).mkdir(parents=True, exist_ok=True)
    (root / directory / "Directory.Build.props").write_text(
        f"<Project>{body}</Project>", encoding="utf-8"
    )


def _runs(root: Path, tests: list[str]) -> dict[str, list[str | None]]:
    return runs_for_tests(root, scan_package_manifests(root), tests)


def _targets(root: Path, tests: list[str]) -> dict[str, str | None]:
    """The project or solution each test runs through; None for no command."""
    runs = _runs(root, tests)
    return {test: str(runs[test][0]).split()[2] if test in runs else None for test in tests}


def _commands(root: Path, tests: list[str]) -> list[str]:
    return commands_for_tests(tests, runs=_runs(root, tests))


def test_a_test_runs_through_its_own_test_project(tmp_path: Path) -> None:
    _shop(tmp_path)
    one = f"{CART_TESTS}::CartTests::TotalIsZero"
    two = "tests/Shop.Tests/Pricing/PriceTests.cs::PriceTests::Rounds"
    assert _runs(tmp_path, [one]) == {
        one: [f"dotnet test {TESTS} {NO_COVERAGE}", "--filter=CartTests.TotalIsZero"]
    }
    assert _commands(tmp_path, [one]) == [
        f'dotnet test {TESTS} {NO_COVERAGE} --filter "CartTests.TotalIsZero"'
    ]
    assert _commands(tmp_path, [one, two]) == [
        f'dotnet test {TESTS} {NO_COVERAGE} --filter "CartTests.TotalIsZero|PriceTests.Rounds"'
    ]


def test_a_file_runs_the_whole_project(tmp_path: Path) -> None:
    _shop(tmp_path)
    tests = [f"{CART_TESTS}::CartTests::TotalIsZero", CART_TESTS, "tests/test_a.py"]
    assert _commands(tmp_path, tests) == [
        "pytest tests/test_a.py",
        f"dotnet test {TESTS} {NO_COVERAGE}",
    ]
    # A capped list widens to files, so the filter goes too.
    capped = [f"{CART_TESTS}::CartTests::TotalIsZero"]
    runs = _runs(tmp_path, [*capped, CART_TESTS])
    assert commands_for_tests(capped, total=5, runs=runs) == [f"dotnet test {TESTS} {NO_COVERAGE}"]


def test_a_helper_library_runs_through_the_test_project_that_references_it(
    tmp_path: Path,
) -> None:
    _shop(tmp_path)
    _project(
        tmp_path,
        "tests/Shop.TestUtils/Shop.TestUtils.csproj",
        refs=("..\\..\\src\\Shop\\Shop.csproj",),
    )
    _project(
        tmp_path,
        TESTS,
        refs=("..\\..\\src\\Shop\\Shop.csproj", "..\\Shop.TestUtils\\Shop.TestUtils.csproj"),
        body=XUNIT,
    )
    helper = "tests/Shop.TestUtils/FakeClock.cs"
    assert _targets(tmp_path, [helper]) == {helper: TESTS}


def test_a_test_project_another_one_references_keeps_its_own_tests(tmp_path: Path) -> None:
    # eShopOnWeb: IntegrationTests references UnitTests, whose tests stay put.
    _project(tmp_path, "tests/UnitTests/UnitTests.csproj", body=XUNIT)
    _project(
        tmp_path,
        "tests/IntegrationTests/IntegrationTests.csproj",
        refs=("..\\UnitTests\\UnitTests.csproj",),
        body=XUNIT,
    )
    test = "tests/UnitTests/BasketTests.cs::BasketTests::Adds"
    assert _targets(tmp_path, [test]) == {test: "tests/UnitTests/UnitTests.csproj"}


def test_no_single_test_project_falls_back_to_the_root_solution(tmp_path: Path) -> None:
    _shop(tmp_path)
    _project(tmp_path, "tests/Shop.TestUtils/Shop.TestUtils.csproj")
    for name in ("A", "B"):
        _project(
            tmp_path,
            f"tests/{name}/{name}.csproj",
            refs=("..\\Shop.TestUtils\\Shop.TestUtils.csproj",),
            body=XUNIT,
        )
    shared = "tests/Shared/StrongNameTests.cs::StrongNameTests::Signed"  # under no project
    helper = "tests/Shop.TestUtils/Fakes.cs"  # referenced by two test projects
    assert _targets(tmp_path, [shared, helper]) == {shared: "Shop.sln", helper: "Shop.sln"}
    assert _commands(tmp_path, [shared, helper]) == [f"dotnet test Shop.sln {NO_COVERAGE}"]


def test_no_project_and_no_solution_gets_no_command(tmp_path: Path) -> None:
    test = f"{CART_TESTS}::CartTests::TotalIsZero"
    assert _runs(tmp_path, [test]) == {}
    assert _commands(tmp_path, [test]) == []
    # Without a map no project is known, so no command either.
    assert commands_for_tests([test]) == []


def test_two_solutions_pick_the_one_named_after_the_checkout(tmp_path: Path) -> None:
    root = tmp_path / "Shop"
    root.mkdir()
    for name in ("Shop.slnx", "Samples.slnx"):
        (root / name).write_text("", encoding="utf-8")
    assert _targets(root, ["tests/X.cs"]) == {"tests/X.cs": "Shop.slnx"}
    (root / "Shop.slnx").unlink()
    (root / "Other.slnx").write_text("", encoding="utf-8")
    assert _targets(root, ["tests/X.cs"]) == {"tests/X.cs": None}


def test_the_plan_stores_its_run_and_reads_it_back(tmp_path: Path) -> None:
    _shop(tmp_path)
    suggestion = _plan("Total", file_path="src/Shop/Cart.cs")
    inferred = {suggestion.file_path: ReachedBy([CART_TESTS], "import-graph", 1)}
    runs = _runs(tmp_path, [CART_TESTS])
    plan = build_validation_plan(
        suggestion, {}, inferred, evidence=ValidationEvidence(test_runs=runs)
    )
    run = f"dotnet test {TESTS} {NO_COVERAGE}"
    assert plan.commands == [run]
    stored = plan.as_dict()
    assert stored["test_runs"] == {CART_TESTS: [run, None]}
    assert "js_packages" not in stored
    assert validation_from_profile(stored).test_runs == stored["test_runs"]
    assert plan_runs([CART_TESTS, "tests/test_a.py"], runs) == stored["test_runs"]


def test_a_batch_runs_its_tests_through_the_stored_run() -> None:
    run = f"dotnet test {TESTS} {NO_COVERAGE}"
    tests = [f"{CART_TESTS}::CartTests::Case{n}" for n in range(2)]
    detail = {
        "refactoring_type": "extract_method",
        "validation": {
            "tests": tests,
            "total": 2,
            "test_runs": {test: [run, f"--filter=CartTests.{test[-5:]}"] for test in tests},
        },
    }
    batches = stage_batches(detail, [{"n": n, "action": "extract"} for n in range(1, 8)])
    assert batches is not None
    assert batches[0]["commands"] == [f'{run} --filter "CartTests.Case0|CartTests.Case1"']


def test_an_unreferenced_app_is_not_a_test_project(tmp_path: Path) -> None:
    # A console app nothing references holds a test-named file; it runs no tests.
    _project(tmp_path, "src/Tool/Tool.csproj")
    test = "src/Tool/Tests/ToolTests.cs"
    assert _targets(tmp_path, [test]) == {test: None}
    (tmp_path / "Shop.sln").write_text("", encoding="utf-8")
    assert _targets(tmp_path, [test]) == {test: "Shop.sln"}


def test_a_project_that_says_it_is_a_test_project_is_one(tmp_path: Path) -> None:
    project = tmp_path / "test/Specs/Specs.csproj"
    project.parent.mkdir(parents=True)
    project.write_text(
        "<Project><PropertyGroup><IsTestProject>true</IsTestProject></PropertyGroup></Project>",
        encoding="utf-8",
    )
    test = "test/Specs/RetryCases.cs"
    assert _targets(tmp_path, [test]) == {test: "test/Specs/Specs.csproj"}


def test_a_root_level_test_project_runs_its_tests(tmp_path: Path) -> None:
    _project(tmp_path, "Shop.csproj", body=XUNIT)
    _project(tmp_path, "lib/Lib.csproj")
    assert _targets(tmp_path, ["CartTests.cs", "Unit/PriceTests.cs", "lib/LibTests.cs"]) == {
        "CartTests.cs": "Shop.csproj",
        "Unit/PriceTests.cs": "Shop.csproj",
        "lib/LibTests.cs": None,
    }
    _project(tmp_path, "Shop.csproj")  # no longer declares a test package
    assert _targets(tmp_path, ["CartTests.cs"]) == {"CartTests.cs": None}


def test_a_shared_build_file_that_declares_tests_makes_test_projects(tmp_path: Path) -> None:
    # The test packages live in a shared build file under test/, not the project.
    _build_props(tmp_path, "test", f"<ItemGroup>{XUNIT}</ItemGroup>")
    _project(tmp_path, "test/Core/Core.csproj")
    _project(tmp_path, "src/App/App.csproj")
    tests = ["test/Core/RetryCases.cs", "src/App/AppTests.cs"]
    assert _targets(tmp_path, tests) == {
        "test/Core/RetryCases.cs": "test/Core/Core.csproj",
        "src/App/AppTests.cs": None,
    }


def test_a_conditional_shared_test_package_does_not_make_every_project_a_test_project(
    tmp_path: Path,
) -> None:
    _build_props(
        tmp_path,
        "",
        "<ItemGroup Condition=\"'$(IsTestProject)' == 'true'\">"
        f"{XUNIT}</ItemGroup>"
        "<PropertyGroup><IsTestProject Condition=\"$(MSBuildProjectName.EndsWith('Tests'))\">"
        "true</IsTestProject></PropertyGroup>",
    )
    _project(tmp_path, "src/Tool/Tool.csproj")
    assert _targets(tmp_path, ["src/Tool/ToolTests.cs"]) == {"src/Tool/ToolTests.cs": None}


def test_a_project_named_for_tests_runs_its_own_files_only(tmp_path: Path) -> None:
    _project(tmp_path, "tests/Shop.Specs/Shop.Specs.csproj", refs=("../Helpers/Helpers.csproj",))
    _project(tmp_path, "tests/Helpers/Helpers.csproj")
    tests = ["tests/Shop.Specs/CartSpecs.cs", "tests/Helpers/FakeClock.cs"]
    assert _targets(tmp_path, tests) == {
        "tests/Shop.Specs/CartSpecs.cs": "tests/Shop.Specs/Shop.Specs.csproj",
        # The name vouches only for the project's own files, not a library it uses.
        "tests/Helpers/FakeClock.cs": None,
    }
