"""Whether a shell command runs tests: the one predicate every surface uses.

Two tiers over one runner table:

- :data:`DISTILL_TEST_RE`: the runners the ``test_output`` distill family
  claims. The rewrite hook routes by it and the distill filter matches by it,
  and per-family permissions key off that family, so it stays the set those
  surfaces have always claimed.
- :data:`TEST_COMMAND_RE`: those plus runners whose output another family
  distills (``mvn test``, ``gradle test``) or that distill leaves alone
  (``bun test``, ``tox``). The coverage re-ingest hook and the session
  efficacy ledger ask only "did this run tests", so they read this one.

Imports nothing but ``re``. The rewrite hook may not import ``repowise.core``
at all (its import-cost test), so it mirrors :data:`DISTILL_TEST_RE`;
``test_test_commands.py`` asserts the two patterns are the same string.
"""

from __future__ import annotations

import re

#: Runners the ``test_output`` distill family claims, as regex alternatives
#: over one simple command after normalisation (lower case, environment
#: assignments, wrappers such as ``uv run`` or ``python -m`` and executable
#: paths peeled off).
_DISTILL_RUNNERS = (
    r"pytest\b",
    r"py\.test\b",
    r"jest\b",
    r"vitest\b",
    r"cargo(?:\.exe)? (?:test|nextest)\b",
    r"go(?:\.exe)? test\b",
    r"(?:npm(?:\.cmd)?|pnpm|yarn) (?:test|run test)\b",
)

#: Further runners that run tests but are not the distill family's.
#: ``mvn test`` and ``gradle test`` stop before ``-``, so ``mvn test-compile``
#: is not a test run; a named npm script (``npm run test:unit``, ``npm run
#: tests``) is.
_OTHER_RUNNERS = (
    r"cargo llvm-cov\b",
    r"mvn test(?![\w-])",
    r"gradlew? test(?![\w-])",
    r"bun test\b",
    r"npm run test[\w:-]*",
    r"tox\b",
    # ``nosetests`` is nose's command; ``nose2`` its successor.
    r"nose(?:tests|2)?\b",
)

#: A normalised simple command the ``test_output`` distill family claims.
DISTILL_TEST_RE = re.compile(rf"^(?:{'|'.join(_DISTILL_RUNNERS)})")

_ALL = "|".join((*_DISTILL_RUNNERS, *_OTHER_RUNNERS))

#: A normalised simple command that runs tests, matched at its start.
TEST_COMMAND_RE = re.compile(rf"^(?:{_ALL})")

#: The same runners anywhere in a text, such as a serialised tool input,
#: where a command does not start the string. A runner called by path
#: (``.venv/bin/pytest``) counts; one inside a longer name (``my-pytest``,
#: ``conftest.pytest``) does not.
TEST_COMMAND_IN_TEXT_RE = re.compile(rf"(?<![\w.-])(?:{_ALL})")


def is_test_command(command: str) -> bool:
    """Whether the normalised simple *command* runs tests."""
    return TEST_COMMAND_RE.match(command) is not None


__all__ = ["DISTILL_TEST_RE", "TEST_COMMAND_IN_TEXT_RE", "TEST_COMMAND_RE", "is_test_command"]
