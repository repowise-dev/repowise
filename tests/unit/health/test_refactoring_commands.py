"""A plan's JS/TS test command runs from the package whose test script owns the test."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.agent_prompts import render_plan
from repowise.core.analysis.health.refactoring.batches import stage_batches
from repowise.core.analysis.health.refactoring.commands import (
    commands_for_tests,
    js_test_packages,
    plan_packages,
)
from repowise.core.analysis.health.refactoring.recommendations import (
    ValidationEvidence,
    build_validation_plan,
)
from repowise.core.analysis.health.refactoring.serving import validation_from_profile
from repowise.core.analysis.test_reachability import ReachedBy
from repowise.core.ingestion.package_roots import owning_package, scan_package_manifests
from tests.unit.health.test_recommendations import _plan


def _packages(root: Path, tests: list[str]) -> dict[str, str | None]:
    return js_test_packages(root, scan_package_manifests(root), tests)


def _package(root: Path, directory: str, scripts: dict[str, str] | None = None) -> None:
    folder = root / directory
    folder.mkdir(parents=True, exist_ok=True)
    body = {"name": directory or "root", **({"scripts": scripts} if scripts else {})}
    (folder / "package.json").write_text(json.dumps(body), encoding="utf-8")


NESTED = "ui-tui/src/__tests__/virtualHeights.test.ts"


def test_nested_package_runs_from_its_own_directory(tmp_path: Path) -> None:
    # The root declares workspaces but no test script, as in the reported repo.
    _package(tmp_path, "", None)
    _package(tmp_path, "ui-tui", {"test": "vitest run"})
    packages = _packages(tmp_path, [NESTED])
    assert packages == {NESTED: "ui-tui"}
    assert commands_for_tests([NESTED], packages=packages) == [
        "npm --prefix ui-tui test -- src/__tests__/virtualHeights.test.ts"
    ]


def test_root_package_keeps_the_root_command(tmp_path: Path) -> None:
    _package(tmp_path, "", {"test": "jest"})
    tests = ["src/a.test.ts", "tests/test_b.py"]
    packages = _packages(tmp_path, tests)
    assert packages == {"src/a.test.ts": ""}
    assert commands_for_tests(tests, packages=packages) == [
        "pytest tests/test_b.py",
        "npm test -- src/a.test.ts",
    ]


def test_nearest_package_without_a_script_defers_to_the_one_above(tmp_path: Path) -> None:
    _package(tmp_path, "web", {"test": "vitest run"})
    _package(tmp_path, "web/packages/ink", None)
    test = "web/packages/ink/src/x.test.tsx"
    packages = _packages(tmp_path, [f"{test}::renders"])
    assert packages == {test: "web"}
    assert commands_for_tests([f"{test}::renders"], packages=packages) == [
        "npm --prefix web test -- packages/ink/src/x.test.tsx::renders"
    ]


def test_no_package_with_a_test_script_gets_no_command(tmp_path: Path) -> None:
    _package(tmp_path, "", {"test": 'echo "Error: no test specified" && exit 1'})
    _package(tmp_path, "app", {"build": "tsc"})
    tests = ["app/src/a.test.ts", "tests/test_b.py"]
    packages = _packages(tmp_path, tests)
    assert packages == {"app/src/a.test.ts": None}
    assert commands_for_tests(tests, packages=packages) == ["pytest tests/test_b.py"]


def test_two_packages_get_one_command_each(tmp_path: Path) -> None:
    _package(tmp_path, "web", {"test": "vitest run"})
    _package(tmp_path, "ui-tui", {"test": "vitest run"})
    tests = ["web/a.test.ts", NESTED]
    commands = commands_for_tests(tests, packages=_packages(tmp_path, tests))
    assert commands == [
        "npm --prefix ui-tui test -- src/__tests__/virtualHeights.test.ts",
        "npm --prefix web test -- a.test.ts",
    ]


def test_without_a_checkout_the_command_runs_from_the_root() -> None:
    assert commands_for_tests([NESTED]) == [f"npm test -- {NESTED}"]
    assert plan_packages([NESTED], None) == {}


def test_a_test_the_map_does_not_hold_gets_no_command() -> None:
    assert commands_for_tests(["web/a.test.ts", NESTED], packages={NESTED: "ui-tui"}) == [
        "npm --prefix ui-tui test -- src/__tests__/virtualHeights.test.ts"
    ]


def test_suffixes_decide_the_runner_not_substrings() -> None:
    tests = ["fixtures/data.json", "fixtures/rows.jsonl", "stubs/mod.pyi", "tests/test_a.py"]
    assert commands_for_tests(tests, packages={}) == ["pytest tests/test_a.py"]


def test_a_path_with_a_space_is_quoted() -> None:
    packages = {"my app/a.test.ts": "my app"}
    assert commands_for_tests(["my app/a.test.ts", "tests/test b.py"], packages=packages) == [
        'pytest "tests/test b.py"',
        'npm --prefix "my app" test -- a.test.ts',
    ]


def test_the_plan_stores_its_packages_and_reads_them_back() -> None:
    suggestion = _plan("estimatedMsgHeight", file_path="ui-tui/src/lib/virtualHeights.ts")
    inferred = {suggestion.file_path: ReachedBy([NESTED], "import-graph", 1)}
    plan = build_validation_plan(
        suggestion, {}, inferred, evidence=ValidationEvidence(js_packages={NESTED: "ui-tui"})
    )
    assert plan.commands == ["npm --prefix ui-tui test -- src/__tests__/virtualHeights.test.ts"]
    stored = plan.as_dict()
    assert stored["js_packages"] == {NESTED: "ui-tui"}
    assert validation_from_profile(stored).js_packages == {NESTED: "ui-tui"}
    unmapped = build_validation_plan(suggestion, {}, inferred)
    assert "js_packages" not in unmapped.as_dict()


def test_a_plan_no_package_can_run_renders_without_a_run_block() -> None:
    detail = {
        "id": "rp_js",
        "refactoring_type": "extract_method",
        "file_path": "app/src/a.ts",
        "target_symbol": "app/src/a.ts::run",
        "line_start": 1,
        "line_end": 40,
        "plan": {},
        "validation": {
            "tests": ["app/src/a.test.ts"],
            "total": 1,
            "commands": commands_for_tests(["app/src/a.test.ts"], packages={}),
            "basis": "inferred",
        },
    }
    assert detail["validation"]["commands"] == []
    text = render_plan(detail)
    assert "`app/src/a.test.ts`" in text
    assert "Run:" not in text and "```" not in text


def test_a_batch_runs_its_tests_from_the_plans_package() -> None:
    tests = [f"{NESTED}::case{n}" for n in range(4)]
    detail = {
        "refactoring_type": "extract_method",
        "validation": {
            "tests": tests,
            "total": 4,
            "commands": ["npm --prefix ui-tui test -- src/__tests__/virtualHeights.test.ts"],
            "js_packages": {NESTED: "ui-tui"},
        },
    }
    steps = [{"n": n, "action": "extract"} for n in range(1, 8)]
    batches = stage_batches(detail, steps)
    assert batches is not None
    assert batches[0]["commands"] == [
        "npm --prefix ui-tui test -- src/__tests__/virtualHeights.test.ts"
    ]


def test_the_owner_lookup_takes_any_manifest() -> None:
    manifests = {"svc": frozenset({"go.mod"}), "svc/web": frozenset({"package.json"})}
    assert owning_package("svc/web/a_test.go", manifests, "go.mod", lambda d: True) == "svc"
    assert owning_package("lib/a_test.go", manifests, "go.mod", lambda d: d == "") == ""
    assert owning_package("lib/a_test.go", manifests, "go.mod", lambda d: False) is None
