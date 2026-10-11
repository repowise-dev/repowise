"""A plan's JS/TS test command runs from the package whose test script owns the test."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.analysis.health.refactoring.batches import stage_batches
from repowise.core.analysis.health.refactoring.commands import (
    commands_for_tests,
    js_packages_in,
    js_test_packages,
)


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
    packages = js_test_packages(tmp_path, [NESTED])
    assert packages == {NESTED: "ui-tui"}
    assert commands_for_tests([NESTED], packages=packages) == [
        "npm --prefix ui-tui test -- src/__tests__/virtualHeights.test.ts"
    ]


def test_root_package_keeps_the_root_command(tmp_path: Path) -> None:
    _package(tmp_path, "", {"test": "jest"})
    tests = ["src/a.test.ts", "tests/test_b.py"]
    packages = js_test_packages(tmp_path, tests)
    assert packages == {"src/a.test.ts": ""}
    assert commands_for_tests(tests, packages=packages) == [
        "pytest tests/test_b.py",
        "npm test -- src/a.test.ts",
    ]


def test_nearest_package_without_a_script_defers_to_the_one_above(tmp_path: Path) -> None:
    _package(tmp_path, "web", {"test": "vitest run"})
    _package(tmp_path, "web/packages/ink", None)
    test = "web/packages/ink/src/x.test.tsx"
    packages = js_test_packages(tmp_path, [f"{test}::renders"])
    assert packages == {test: "web"}
    assert commands_for_tests([f"{test}::renders"], packages=packages) == [
        "npm --prefix web test -- packages/ink/src/x.test.tsx::renders"
    ]


def test_no_package_with_a_test_script_gets_no_command(tmp_path: Path) -> None:
    _package(tmp_path, "", {"test": 'echo "Error: no test specified" && exit 1'})
    _package(tmp_path, "app", {"build": "tsc"})
    tests = ["app/src/a.test.ts", "tests/test_b.py"]
    packages = js_test_packages(tmp_path, tests)
    assert packages == {"app/src/a.test.ts": None}
    assert commands_for_tests(tests, packages=packages) == ["pytest tests/test_b.py"]


def test_two_packages_get_one_command_each(tmp_path: Path) -> None:
    _package(tmp_path, "web", {"test": "vitest run"})
    _package(tmp_path, "ui-tui", {"test": "vitest run"})
    tests = ["web/a.test.ts", NESTED]
    commands = commands_for_tests(tests, packages=js_test_packages(tmp_path, tests))
    assert commands == [
        "npm --prefix ui-tui test -- src/__tests__/virtualHeights.test.ts",
        "npm --prefix web test -- a.test.ts",
    ]
    assert js_packages_in(commands, [*tests, "web/a.test.ts::case"]) == {
        "web/a.test.ts": "web",
        NESTED: "ui-tui",
    }


def test_without_a_checkout_the_command_runs_from_the_root() -> None:
    assert commands_for_tests([NESTED]) == [f"npm test -- {NESTED}"]
    assert js_packages_in([f"npm test -- {NESTED}"], [NESTED]) == {NESTED: ""}


def test_a_batch_runs_its_tests_from_the_plans_package() -> None:
    tests = [f"{NESTED}::case{n}" for n in range(4)]
    detail = {
        "refactoring_type": "extract_method",
        "validation": {
            "tests": tests,
            "total": 4,
            "commands": ["npm --prefix ui-tui test -- src/__tests__/virtualHeights.test.ts"],
        },
    }
    steps = [{"n": n, "action": "extract"} for n in range(1, 8)]
    batches = stage_batches(detail, steps)
    assert batches is not None
    assert batches[0]["commands"] == [
        "npm --prefix ui-tui test -- src/__tests__/virtualHeights.test.ts"
    ]
