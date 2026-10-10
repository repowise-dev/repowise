"""The one answer to "is this path a test?" (#1103).

The corpus is the point of this file. Every row is a real layout, and adding an
ecosystem means adding rows here rather than writing a new predicate somewhere
and hoping a reviewer notices. The paths that opened #1103 - the five the twelve
implementations disagreed on - are marked inline.
"""

from __future__ import annotations

import pytest

from repowise.core.pytest_roots import read_pytest_roots
from repowise.core.test_paths import (
    is_skippable_test_path,
    is_test_path,
    is_test_related_path,
    is_test_support_path,
    is_unambiguous_test_path,
    names_test_for,
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
    # PascalCase suite folders, one word with no separator (PowerToys)
    ("src/common/updating/UnitTests/UpdatingTests.cpp", None, "test"),
    ("src/modules/AdvancedPaste/AdvancedPaste.UnitTests/Mocks/Clipboard.cs", None, "support"),
    ("src/modules/peek/Peek.UITests/PeekFilePreviewTests.cs", None, "test"),
    ("src/modules/x/FuzzTests/fuzz.cpp", None, "test"),
    ("src/unitTests/parser.ts", None, "test"),
    # ...plural only, and only with the capital: singular names one thing
    ("src/ui/HitTest/hit.cpp", None, ""),
    ("src/contests/rules.py", None, ""),
    ("src/Latests/x.cs", None, ""),
    # gradle test fixtures and QA projects (elasticsearch)
    ("build-tools/src/testFixtures/java/org/x/Fixture.java", None, "test"),
    ("x-pack/plugin/sql/qa/server/src/main/java/org/x/JdbcBase.java", None, "test"),
    ("qa/logging-spi/src/main/java/org/x/Spi.java", None, "test"),
    ("src/qa/answer.py", None, ""),  # a question-answering module
    ("app/qa/src/model.py", None, ""),  # qa is the project itself
    ("gradle/internal/testfixtures/DeployPlugin.java", None, ""),
    ("src/testfixtures/pkg/mod.py", None, ""),  # the Python testfixtures library
    ("packages/qa/cli/src/main.ts", None, ""),  # shipped QA tooling
    ("src/qa/tools/src/x.py", None, ""),
    ("qa/tools/src/report.py", None, ""),
    ("docs/qa/guide/src/x.md", None, ""),
    # rspec: `spec/` is RSpec for ruby and a specification folder otherwise
    ("spec/models/user_spec.rb", None, "test"),  # #1103: one of nine said test
    ("spec/models/user.rb", "ruby", "test"),
    ("spec/models/user.rb", None, ""),
    ("spec/openapi/users.yaml", None, ""),
    ("spec/support/helper.rb", None, "support"),
    # perl-style t/: `t/` is a test tree for python whatever the filename
    # (celery layout), and needs a test-shaped filename otherwise — same
    # ambiguous-token mechanism as `spec/` above (#2962)
    ("t/unit/app/test_app.py", "python", "test"),
    ("t/unit/conftest.py", "python", "support"),
    ("t/unit/tasks/task_config.py", "python", "test"),
    ("t/integration/tasks.py", "python", "test"),
    ("t/smoke/tasks.py", "python", "test"),
    ("t/unit/contrib/proj/foo/tasks.py", "python", "test"),
    # ...so without the language, only a corroborating filename counts
    ("t/unit/app/test_app.py", None, "test"),
    ("t/unit/tasks/task_config.py", None, ""),
    # ...a non-source file under t/ stays as it is without corroboration
    ("t/data/expected.json", None, ""),
    ("t/assets/image.png", None, ""),
    # ...and a bare `t` that is not a directory segment stays production
    ("src/t.py", None, ""),
    ("src/t.py", "python", ""),
    ("latest/x.py", None, ""),
    ("contest/x.py", None, ""),
    # e2e suites
    ("e2e/login.ts", None, "test"),
    # ...and the `.e2e.` infix beside `.test.`/`.spec.`, suites and their helpers
    ("src/gateway/gateway.e2e.ts", None, "test"),
    ("src/gateway/test-helpers.e2e.ts", None, "test"),
    ("apps/desktop/tsconfig.e2e.json", None, ""),
    ("src/harness/e2e_utils.py", None, ""),
    # one-word suite directories (gcc, flask before 0.11)
    ("flask/testsuite/basic.py", None, "test"),
    ("gcc/testsuite/gcc.dg/pr123.c", None, "test"),
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
    # `test suite(s)` names a suite of tests when it is a build module with its
    # own `src` root: a Gradle module of shared test classes, serde's no_std check
    ("server/server-test-suites/jvm/src/io/x/suites/EngineStressSuite.kt", None, "test"),
    ("server/server-test-suites/common/src/io/x/suites/Utils.kt", None, "test"),
    ("server/x-test-suites/src/main/kotlin/io/x/ContentSuite.kt", None, "test"),
    ("test_suite/no_std/src/main.rs", None, "test"),
    # ...and not as a product feature folder or a docs page set
    ("src/api/test-suites/route.ts", None, ""),
    ("apps/web/src/features/test-suite/List.tsx", None, ""),
    ("docs/test-suites/overview.md", None, ""),
    ("conformance/test-suite/run.py", None, ""),
    # ...so a word that merely contains a test token, or a compound that is
    # *about* testing, is not a test tree
    ("src/latest-release/api.py", None, ""),
    ("contest_results/main.py", None, ""),
    ("docs/test-api/class-test.md", None, ""),
    ("docs/test-tools/index.md", None, ""),
    ("src/generators/e2e-project/index.ts", None, ""),
    ("apps/office-suite/src/main.ts", None, ""),
    ("packages/test-suite-runner/index.js", None, ""),
    ("server/server-test-host/src/io/x/TestEngine.kt", None, ""),
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
    # #3244: but a helper directory named for tests holds what only tests
    # import, wherever it sits, read on its words run together like `testdata`
    ("ui/src/test-helpers/load-styles.ts", None, "support"),
    ("src/test_helpers/x.py", None, "support"),
    ("src/testhelpers/x.ts", None, "support"),
    ("src/test-utils/render.ts", None, "support"),
    ("src/test_utils/x.py", None, "support"),
    ("internal/testutils/x.go", None, "support"),
    ("pkg/testutil/x.go", None, "support"),
    ("src/test-support/x.ts", None, "support"),
    ("src/test_support/x.rb", None, "support"),
    ("tests/test-utils/x.ts", None, "support"),
    # a test-shaped filename inside one is still a test
    ("ui/src/test-helpers/foo.test.ts", None, "test"),
    ("pkg/testutil/x_test.go", None, "test"),
    # the bare words still need a test tree, and `testing` names shipped packages
    ("src/helpers/x.ts", None, ""),
    ("src/support/x.ts", None, ""),
    ("src/utils/x.ts", None, ""),
    ("tests/helpers/x.ts", None, "support"),
    ("src/testing/x.py", None, ""),
    ("src/latest-utils/x.py", None, ""),
    # a helper directory that is a package root ships (preact's `test-utils/`);
    # path-only, its own manifest is the evidence
    ("test-utils/package.json", None, ""),
    ("pkg/testutil/go.mod", None, ""),
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
    # vitest suite modules a `.test.ts` file registers
    ("src/gateway/server.auth.default-token.suite.ts", None, "test"),
    ("src/gateway/suite.ts", None, ""),
    # infixes apply to every source extension (no per-extension infix rule), so a
    # benchmark.js suite reads as test: a known false positive, accepted
    ("bench/array.suite.js", None, "test"),
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
    # Support directories count anywhere, .github included.
    (".github/test-data/expected.json", None, "support"),
    (".github/actions/notify/__snapshots__/Button.snap", None, "support"),
    # C/C++ helpers shared by tests (abseil, leveldb)
    ("absl/strings/cord_test_helpers.h", None, "test"),
    ("absl/random/internal/distribution_test_util.cc", None, "test"),
    ("absl/log/log_basic_test_impl.inc", None, "test"),
    ("util/env_posix_test_helper.h", None, "test"),
    ("absl/strings/str_cat.h", None, ""),
    ("crates/searcher/src/searcher/util.rs", None, ""),
    # #2662: Jest manual mocks live in a root `__mocks__/` beside node_modules or
    # beside the mocked module, never in a test tree, so the dunder dir counts
    # wherever it sits, like `__fixtures__`. Bare `mocks` stays tree-gated: this
    # repo's analysis/health/mocks/ and ktor-client-mock are product code.
    ("__mocks__/axios.js", None, "support"),
    ("src/__mocks__/api.ts", None, "support"),
    ("src/__mocks__/api.test.ts", None, "test"),  # a test-shaped name still wins
    ("src/mocks/handlers.ts", None, ""),
    ("packages/core/src/repowise/core/analysis/health/mocks/lexicon.py", None, ""),
    ("ktor-client-mock/common/src/io/ktor/client/engine/mock/MockEngine.kt", None, ""),
    # #2662: .NET test projects are also named Foo.UnitTests, Foo.IntegrationTests
    # and Foo.FuzzTests (PowerToys). A shipped Foo.Testing library is not one, and a
    # scaffolding dir inside a test project is support, as under tests/.
    ("Settings.UI.UnitTests/ViewModelTests/General.cs", None, "test"),
    ("Hosts.FuzzTests/Fuzz.cs", None, "test"),
    ("Foo.IntegrationTests/Sql/Db.cs", None, "test"),
    ("Foo.IntegrationTests/Helpers/Db.cs", None, "support"),
    ("src/Polly.Testing/ResiliencePipelineExtensions.cs", None, ""),
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


def test_a_test_helper_directory_that_is_a_package_root_stays_production() -> None:
    """#3244: preact's ``test-utils/`` is a package published as
    ``preact/test-utils``. Only the caller knows the directory holds a manifest,
    so it says so through *package_root*; path-only callers read it as support."""
    roots = {"test-utils"}.__contains__
    assert not is_test_related_path("test-utils/src/index.js", package_root=roots)
    assert is_test_support_path("test-utils/src/index.js")
    # Only the helper directory is asked about, so a package root elsewhere on
    # the path does not rescue it, and a test file in a shipped package is a test.
    assert is_test_support_path("src/test-utils/x.ts", package_root={"src"}.__contains__)
    assert is_test_path("test-utils/test/index.test.js", package_root=roots)
    # Golden data is not a package even beside a manifest (Go fixture modules).
    assert is_test_support_path("testdata/mod/x.go", package_root=lambda _d: True)


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


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/unit/test_engine.py", True),
        ("tests/conftest.py", True),
        ("packages/ui/__tests__/table.test.tsx", True),
        ("src/test/java/FooTest.java", True),
        ("pkg/server/handler_test.go", True),
        # Named like tests, but production modules inside a source tree.
        ("packages/core/src/repowise/core/test_paths.py", False),
        ("packages/core/src/repowise/core/analysis/test_impact.py", False),
        ("packages/core/src/repowise/core/distill/filters/test_output.py", False),
        ("src/app/lib/format.test.ts", False),
        ("src/app/lib/format.py", False),
    ],
)
def test_unambiguous_test_paths_need_more_than_a_test_shaped_name_under_src(
    path: str, expected: bool
) -> None:
    assert is_unambiguous_test_path(path) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/api/openapi.yaml", True),
        ("Foo.Tests/Clients/Orders.cs", True),
        ("src/api/client.test.ts", True),
        ("src/__mocks__/api.ts", True),
        ("pkg/server/handler_test.go", True),
        # non-source files need a named test tree
        ("Foo.Specs/openapi.yaml", False),
        ("spec/support/openapi.yaml", False),
        ("testdata/openapi.yaml", False),
        # a PascalCase suite folder alone, or a Python test name under src
        ("LoadTests/Runner.cs", False),
        ("src/api/test_routes.py", False),
        ("src/app/main.ts", False),
    ],
)
def test_skippable_test_paths_never_drop_a_possible_contract(path: str, expected: bool) -> None:
    assert is_skippable_test_path(path) is expected


_ROOTS = read_pytest_roots(
    [("pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')]
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # production modules named for what they do, outside testpaths
        ("packages/core/src/repowise/core/test_paths.py", ""),
        ("packages/core/src/repowise/core/analysis/test_selection.py", ""),
        ("mytool/cli/approvals_test.py", ""),
        # a test directory still settles it, inside testpaths or not
        ("tests/unit/test_engine.py", "test"),
        ("packages/ui/tests/test_a.py", "test"),
        ("pkg/tests/helpers/test_builders.py", "test"),
        ("conftest.py", "support"),
        # pytest config says nothing about other languages
        ("src/app/format.test.ts", "test"),
        ("pkg/server/handler_test.go", "test"),
    ],
)
def test_pytest_config_overrules_a_test_shaped_python_name(path: str, expected: str) -> None:
    got = "test" if is_test_path(path, roots=_ROOTS) else ""
    got = "support" if is_test_support_path(path, roots=_ROOTS) else got
    assert got == expected
    # Without the config the name decides, as it does for a bare pytest.
    assert is_test_related_path(path)


@pytest.mark.parametrize(
    ("test_path", "source", "siblings", "named"),
    [
        ("tests/test_loader.py", "src/loader.py", (), True),
        ("tests/test_attention_golden.py", "src/attention.py", (), True),
        ("tests/test_loaders.py", "src/loader.py", (), False),
        ("tests/test_foo_bar.py", "src/foo.py", (), True),
        # A sibling the qualified name names exactly is that file's test.
        ("tests/test_foo_bar.py", "src/foo.py", ("src/foo_bar.py",), False),
        ("tests/test_foo_bar.py", "src/foo_bar.py", ("src/foo.py",), True),
        ("src/attempt.spawn-workspace.test.ts", "src/attempt.ts", (), True),
        # Multi-dot stems keep PurePath semantics in both directions.
        ("src/foo.test.ts", "src/foo.config.ts", (), False),
        ("src/foo.config.test.ts", "src/foo.config.ts", (), True),
        ("src/foo.config.test.ts", "src/foo.ts", ("src/foo.config.ts",), False),
        ("pkg/foo_bar_test.go", "pkg/foo.go", (), True),
        ("pkg/foo_bar_test.go", "pkg/foo.go", ("pkg/foo_bar.go",), False),
        ("src/FooBarTest.java", "src/Foo.java", (), True),
        ("src/FooBarTests.cs", "src/Foo.cs", ("src/FooBar.cs",), False),
        ("src/Footest.java", "src/Foo.java", (), False),
    ],
)
def test_a_test_named_for_a_file_may_carry_a_qualifier(test_path, source, siblings, named):
    assert names_test_for(test_path, source, siblings) is named
