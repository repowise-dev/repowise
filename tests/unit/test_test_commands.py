"""``core.test_commands``: the one "does this run tests" predicate."""

from __future__ import annotations

import pytest

from repowise.core.test_commands import (
    DISTILL_TEST_RE,
    TEST_COMMAND_IN_TEXT_RE,
    is_test_command,
)

#: Claimed by the ``test_output`` distill family.
DISTILLED = [
    "pytest -q",
    "py.test",
    "jest --coverage",
    "vitest run",
    "cargo test",
    "cargo.exe test",
    "cargo nextest run",
    "go test ./...",
    "go.exe test ./...",
    "npm test",
    "npm.cmd test",
    "npm run test -- --coverage",
    "npm run test:unit",
    "pnpm test",
    "pnpm run test",
    "yarn test",
]

#: Test runs whose output another family distills, or none does.
OTHER_TEST_RUNS = [
    "cargo llvm-cov --lcov --output-path lcov.info",
    "npm run tests",
    "bun test",
    "mvn test",
    "mvn test -Dtest=AuthTest",
    "gradle test",
    "gradlew test",
    "tox -e py313",
    "nose",
]

NOT_TESTS = [
    "pytester",
    "npm run lint",
    "npm install",
    "go build ./...",
    "cargo build",
    "mvn package",
    "mvn test-compile",
    "mvn test-jar",
    "gradle build",
    "gradle test-fixtures",
    "echo pytest",
    "git log -- tests",
    "toxic",
]


@pytest.mark.parametrize("command", DISTILLED + OTHER_TEST_RUNS)
def test_a_test_runner_is_a_test_command(command: str) -> None:
    assert is_test_command(command)
    assert TEST_COMMAND_IN_TEXT_RE.search(f'{{"command": "cd api && {command}"}}')


@pytest.mark.parametrize("command", NOT_TESTS)
def test_other_commands_are_not(command: str) -> None:
    assert not is_test_command(command)
    assert not DISTILL_TEST_RE.match(command)


@pytest.mark.parametrize("command", OTHER_TEST_RUNS)
def test_the_distill_family_keeps_the_runners_it_always_claimed(command: str) -> None:
    assert not DISTILL_TEST_RE.match(command)


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ('{"command": ".venv/bin/pytest -q"}', True),
        ('{"command": ".venv\\\\Scripts\\\\pytest.exe -q"}', True),
        ('{"command": "uv run pytest"}', True),
        ('{"command": "my-pytest --help"}', False),
        ('{"command": "conftest.pytest"}', False),
        ('{"command": "cat notes.md"}', False),
    ],
)
def test_in_text_finds_a_runner_called_anywhere(text: str, found: bool) -> None:
    assert (TEST_COMMAND_IN_TEXT_RE.search(text) is not None) is found


def test_every_surface_reads_the_one_predicate() -> None:
    from repowise.cli.commands.augment_cmd.coverage_reingest import is_test_command as hook_test
    from repowise.cli.rewrite_hook import FAMILY_PATTERNS
    from repowise.core.distill.filters.test_output import TestOutputFilter

    # The rewrite hook may not import core, so it mirrors the pattern.
    assert dict(FAMILY_PATTERNS)["test_output"].pattern == DISTILL_TEST_RE.pattern
    assert TestOutputFilter().matches_command("pytest -q")
    assert not TestOutputFilter().matches_command("mvn test")
    assert hook_test("mvn test") and not hook_test("mvn test-compile")
