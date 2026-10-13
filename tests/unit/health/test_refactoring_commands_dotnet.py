"""A plan's C# tests run through ``dotnet test`` on the test project that holds them."""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.refactoring.batches import stage_batches
from repowise.core.analysis.health.refactoring.commands import (
    commands_for_tests,
    packages_for_tests,
    plan_packages,
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


def _packages(root: Path, tests: list[str]) -> dict[str, str | None]:
    return packages_for_tests(root, scan_package_manifests(root), tests)


def test_a_test_runs_through_its_own_test_project(tmp_path: Path) -> None:
    _shop(tmp_path)
    one = f"{CART_TESTS}::CartTests::TotalIsZero"
    two = "tests/Shop.Tests/Pricing/PriceTests.cs::PriceTests::Rounds"
    packages = _packages(tmp_path, [one, two])
    assert packages == {CART_TESTS: TESTS, "tests/Shop.Tests/Pricing/PriceTests.cs": TESTS}
    assert commands_for_tests([one], packages=packages) == [
        f"dotnet test {TESTS} --filter CartTests.TotalIsZero -p:CollectCoverage=false"
    ]
    assert commands_for_tests([one, two], packages=packages) == [
        f'dotnet test {TESTS} --filter "CartTests.TotalIsZero|PriceTests.Rounds"'
        " -p:CollectCoverage=false"
    ]


def test_a_file_runs_the_whole_project(tmp_path: Path) -> None:
    _shop(tmp_path)
    tests = [f"{CART_TESTS}::CartTests::TotalIsZero", CART_TESTS, "tests/test_a.py"]
    assert commands_for_tests(tests, packages=_packages(tmp_path, tests)) == [
        "pytest tests/test_a.py",
        f"dotnet test {TESTS}",
    ]
    # A capped list widens to files, so the filter goes too.
    capped = [f"{CART_TESTS}::CartTests::TotalIsZero"]
    assert commands_for_tests(capped, total=5, packages=_packages(tmp_path, capped)) == [
        f"dotnet test {TESTS}"
    ]


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
    assert _packages(tmp_path, [helper]) == {helper: TESTS}


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
    assert _packages(tmp_path, [test]) == {
        "tests/UnitTests/BasketTests.cs": "tests/UnitTests/UnitTests.csproj"
    }


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
    shared = "tests/Shared/StrongNameTests.cs"  # under no project
    helper = "tests/Shop.TestUtils/Fakes.cs"  # referenced by two test projects
    tests = [f"{shared}::StrongNameTests::Signed", helper]
    assert _packages(tmp_path, tests) == {shared: "Shop.sln", helper: "Shop.sln"}
    assert commands_for_tests(tests, packages=_packages(tmp_path, tests)) == [
        "dotnet test Shop.sln"
    ]


def test_no_project_and_no_solution_gets_no_command(tmp_path: Path) -> None:
    test = f"{CART_TESTS}::CartTests::TotalIsZero"
    packages = _packages(tmp_path, [test])
    assert packages == {CART_TESTS: None}
    assert commands_for_tests([test], packages=packages) == []
    # Without a map no project is known, so no command either.
    assert commands_for_tests([test]) == []


def test_two_solutions_pick_the_one_named_after_the_checkout(tmp_path: Path) -> None:
    root = tmp_path / "Shop"
    root.mkdir()
    for name in ("Shop.slnx", "Samples.slnx"):
        (root / name).write_text("", encoding="utf-8")
    assert _packages(root, ["tests/X.cs"]) == {"tests/X.cs": "Shop.slnx"}
    (root / "Shop.slnx").unlink()
    (root / "Other.slnx").write_text("", encoding="utf-8")
    assert _packages(root, ["tests/X.cs"]) == {"tests/X.cs": None}


def test_the_plan_stores_its_project_and_reads_it_back(tmp_path: Path) -> None:
    _shop(tmp_path)
    suggestion = _plan("Total", file_path="src/Shop/Cart.cs")
    inferred = {suggestion.file_path: ReachedBy([CART_TESTS], "import-graph", 1)}
    packages = _packages(tmp_path, [CART_TESTS])
    plan = build_validation_plan(
        suggestion, {}, inferred, evidence=ValidationEvidence(js_packages=packages)
    )
    assert plan.commands == [f"dotnet test {TESTS}"]
    stored = plan.as_dict()
    assert stored["js_packages"] == {CART_TESTS: TESTS}
    assert validation_from_profile(stored).js_packages == {CART_TESTS: TESTS}
    assert plan_packages([CART_TESTS, "tests/test_a.py"], packages) == {CART_TESTS: TESTS}


def test_a_batch_runs_its_tests_through_the_stored_project() -> None:
    tests = [f"{CART_TESTS}::CartTests::Case{n}" for n in range(2)]
    detail = {
        "refactoring_type": "extract_method",
        "validation": {"tests": tests, "total": 2, "js_packages": {CART_TESTS: TESTS}},
    }
    batches = stage_batches(detail, [{"n": n, "action": "extract"} for n in range(1, 8)])
    assert batches is not None
    assert batches[0]["commands"] == [
        f'dotnet test {TESTS} --filter "{"|".join(f"CartTests.Case{n}" for n in range(2))}"'
        " -p:CollectCoverage=false"
    ]
