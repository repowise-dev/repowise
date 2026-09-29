"""The one answer to "is this path a test?" (#1103).

The corpus is the point of this file. Every row is a real layout, and adding an
ecosystem means adding rows here rather than writing a new predicate somewhere
and hoping a reviewer notices. The paths that opened #1103 - the five the twelve
implementations disagreed on - are marked inline.
"""

from __future__ import annotations

import pytest

from repowise.core.test_paths import (
    is_test_path,
    is_test_related_path,
    is_test_support_path,
)

# (path, language or None, expected classification)
_CORPUS: tuple[tuple[str, str | None, str], ...] = (
    # pytest
    ("tests/test_thing.py", None, "test"),
    ("pkg/test_thing.py", None, "test"),
    ("tests/unit/health/test_engine.py", None, "test"),
    ("conftest.py", None, "support"),  # #1103: four said test, five said not
    ("tests/conftest.py", None, "support"),
    ("tests/factories/user.py", None, "support"),
    # a package marker carries no test itself, but it is not production code
    # either, and the tree it sits in settles it
    ("tests/unit/__init__.py", None, "test"),
    ("tests/fixtures/repo/vitest.config.mts", None, "support"),
    # django: a per-app suite module, which no prefix/suffix rule catches
    ("myapp/tests.py", None, "test"),
    ("tests/app/tests.py", None, "test"),  # #1103: five said test, four said not
    # go
    ("app/user_test.go", None, "test"),
    ("pkg/thing_test.go", None, "test"),
    # jest / vitest
    ("src/__tests__/x.js", None, "test"),
    ("src/components/Button.test.tsx", None, "test"),
    ("src/lib/parse.spec.ts", None, "test"),
    # the extensions #288 had to chase twice, covered here by the `.test.`
    # and `.spec.` infixes rather than by a suffix list
    ("src/foo.test.mts", None, "test"),
    ("src/foo.spec.cts", None, "test"),
    ("src/foo.mts", None, ""),
    # maven / gradle / kotlin multiplatform
    ("src/test/java/Foo.java", None, "test"),
    ("src/it/scala/FooSpec.scala", None, "test"),
    ("src/jvmTest/kotlin/A.kt", None, "test"),
    ("FooTest.java", None, "test"),
    ("src/main/java/Latest.java", None, ""),
    # .net sibling test projects
    ("Foo.Tests/Bar.cs", None, "test"),
    # rspec: `spec/` is RSpec for ruby and a specification folder otherwise
    ("spec/models/user_spec.rb", None, "test"),  # #1103: one of nine said test
    ("spec/models/user.rb", "ruby", "test"),
    ("spec/models/user.rb", None, ""),
    ("spec/openapi/users.yaml", None, ""),
    ("spec/support/helper.rb", None, "support"),
    # e2e suites
    ("e2e/login.ts", None, "test"),
    # production code that merely contains the word: the unanchored
    # `test[s_/]` substring rule classified the first three as tests
    ("src/latest/api.py", None, ""),  # #1103 finding 1
    ("protest/main.py", None, ""),  # #1103 finding 1
    ("src/contest/rules.py", None, ""),
    ("lib/contest.py", None, ""),
    ("src/testing/helpers.py", None, ""),
    ("src/testing_utils.py", None, ""),
    # the same word mid-stem, which is how the MCP tools' `test_` substring
    # token demoted this repo's own source in search results
    ("src/analysis/missing_test_signal.py", None, ""),
    ("src/ingestion/pytest_hints.py", None, ""),
    ("alembic/versions/0036_test_coverage.py", None, ""),
    ("src/manifest/loader.py", None, ""),
    ("src/helpers/fmt.py", None, ""),
    ("src/fixtures/data.py", None, ""),
    # #1103 finding 6: golden data and the dunder fixture dir, which the
    # scaffolding tokens missed because those only count inside a test tree.
    # Support rather than test - a golden file is what a test reads - so the
    # union counts them while search still surfaces them by name.
    ("testdata/golden.json", None, "support"),
    ("pkg/parser/testdata/valid/input.go", None, "support"),
    ("src/__fixtures__/x.ts", None, "support"),
    # a test-shaped filename still wins over the directory
    ("testdata/build_test.go", None, "test"),
    # and the same finding's third path stays production code on purpose: bare
    # `fixtures` is an ordinary word that names real product directories, so it
    # still needs a test tree around it
    ("fixtures/data.yml", None, ""),
    ("app/fixtures/premier_league.py", None, ""),
    ("src/testdata_loader.py", None, ""),
    ("tests/testdata/golden.json", None, "support"),
    # compound directory names: a segment's words split on - _ . and its last
    # word, the head of the compound, says what the directory holds
    ("e2e-tests/pages/login.ts", None, "test"),
    ("e2e-tests/playwright.config.ts", None, "test"),
    ("integration_test/driver.dart", None, "test"),  # flutter
    ("mylib/mylib_tests/core/__init__.py", None, "test"),
    ("extensions/api-tests/package.json", None, "test"),
    ("extensions/api-tests/workspace/image.png", None, "test"),
    ("apps/client-e2e/src/app.cy.ts", None, "test"),
    ("apps/client-e2e/src/support/commands.ts", None, "support"),
    ("mylib/unit.tests/run.py", None, "test"),
    # ...so a word that merely contains a test token, or a compound that is
    # *about* testing, is not a test tree
    ("src/latest-release/api.py", None, ""),
    ("contest_results/main.py", None, ""),
    ("docs/test-api/class-test.md", None, ""),
    ("docs/test-tools/index.md", None, ""),
    ("src/generators/e2e-project/index.ts", None, ""),
    # singular `test` heads a compound naming one thing - a generator, an
    # executor, a package, an example project - in either spelling
    ("src/generators/component-test/index.ts", None, ""),
    ("src/executors/unit-test/schema.json", None, ""),
    ("packages/runner-test/cli.js", None, ""),
    ("examples/assets_smoke_test/setup.py", None, ""),
    ("third_party/packages/svg_test/lib/src/finders.dart", None, ""),
    ("example/integration_test/app_test.dart", None, "test"),
    # a library for writing tests is that library's production code
    ("packages/testing-library/src/index.ts", None, ""),
    ("src/testing_library/render.py", None, ""),
    ("src/test-utils/render.ts", None, ""),
    # `test-data` / `test_data` are golden data, the same as Go's `testdata`
    ("test-data/users.json", None, "support"),
    ("pkg/test_data/input.csv", None, "support"),
    ("tests/test-data/expected.yaml", None, "support"),
    # `spec` still needs corroboration when it heads a compound
    ("openapi-specs/users.yaml", None, ""),
    ("acceptance-specs/users.rb", "ruby", "test"),
    # filename rules are source rules: configuration that shares the word is
    # not a test
    ("tsconfig.test.json", None, ""),
    ("packages/app/tsconfig.spec.json", None, ""),
    ("config/app.test.yaml", None, ""),
    ("resources/rpm/app.spec.template", None, ""),
    ("src/generators/files/__name__.spec.ts__tmpl__", None, ""),
    ("conf/app_template/tests.py-tpl", None, ""),
    # ...except snapshot output, which Jest/Vitest's `__snapshots__` owns
    ("src/components/__snapshots__/Button.test.tsx.snap", None, "support"),
    # tsd / vitest type tests
    ("src/types/index.test-d.ts", None, "test"),
    # a bare `test`/`tests` stem is a suite in Python and Rust only; `test.ts` in an
    # examples folder is an example, and `scripts/test.sh` runs the suite
    ("examples/basic/src/test.ts", None, ""),
    ("src/common/test.ts", None, ""),
    ("scripts/test.sh", None, ""),
    ("examples/basic/tests/test.ts", None, "test"),
    ("scripts/test.py", None, "test"),
    ("crates/ide/src/tests.rs", None, "test"),
    ("lib/plug/test.ex", None, ""),
    # repository metadata is never test material, whatever it is named
    (".github/workflows/ci.test.yml", None, ""),
    (".github/workflows/tests.yml", None, ""),
    (".github/skills/unit-tests/SKILL.md", None, ""),
    (".github/workflows/data/test_settings.py.tpl", None, ""),
    # ...but a test of an action's own script is still a test
    (".github/actions/notify/notify.test.mjs", None, "test"),
)


@pytest.mark.parametrize(("path", "language", "expected"), _CORPUS, ids=lambda v: str(v))
def test_corpus(path: str, language: str | None, expected: str) -> None:
    actual = (
        "test"
        if is_test_path(path, language)
        else "support"
        if is_test_support_path(path, language)
        else ""
    )
    assert actual == expected, f"{path} (language={language}) classified {actual!r}"


@pytest.mark.parametrize(("path", "language", "expected"), _CORPUS, ids=lambda v: str(v))
def test_test_and_support_are_never_both_true(
    path: str, language: str | None, expected: str
) -> None:
    """The two questions partition test material; the copies they replace did not."""
    assert not (is_test_path(path, language) and is_test_support_path(path, language))


@pytest.mark.parametrize(("path", "language", "expected"), _CORPUS, ids=lambda v: str(v))
def test_related_is_the_union(path: str, language: str | None, expected: str) -> None:
    assert is_test_related_path(path, language) is (expected != "")


@pytest.mark.parametrize("path", ["", ".", "/"])
def test_degenerate_paths_are_not_tests(path: str) -> None:
    assert not is_test_related_path(path)


def test_windows_separators_match_posix() -> None:
    """Callers pass repo-relative paths from both a walk and a database row."""
    assert is_test_path("tests\\unit\\test_engine.py")
    assert is_test_support_path("tests\\conftest.py")
    assert not is_test_related_path("src\\latest\\api.py")


def test_case_sensitive_rules_stay_case_sensitive() -> None:
    """``FooTest.java`` is a test; ``latest.java`` and bare ``Test.java`` are not.

    The camel rule needs a lowercase boundary before the suffix, which is what
    keeps every word ending in "test" out.
    """
    assert is_test_path("src/FooTest.java")
    assert not is_test_path("src/latest.java")
    assert not is_test_path("src/Test.java")


def test_camel_prefix_rule_mirrors_the_suffix_rule() -> None:
    """``TestKeymap.dpr`` is a test (Pascal's camel-boundary prefix
    convention); ``Testing.dpr`` and bare ``Test.dpr`` are not.

    The prefix rule needs an uppercase boundary right after "Test", the
    mirror of the suffix rule's lowercase-boundary requirement.
    """
    assert is_test_path("src/tools/TestKeymap.dpr", "pascal")
    assert not is_test_path("src/tools/Testing.dpr", "pascal")
    assert not is_test_path("src/tools/Test.dpr", "pascal")
    # Case-sensitive and scoped to Pascal's own extensions -- a bare
    # lowercase-boundary word never matches, and the convention doesn't
    # leak onto an unrelated extension.
    assert not is_test_path("src/testkeymap.dpr", "pascal")
    assert not is_test_path("src/TestKeymap.txt", "pascal")
