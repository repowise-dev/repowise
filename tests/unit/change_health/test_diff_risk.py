"""Diff-risk rules: added suppressions, weakened tests, test-only branches."""

from __future__ import annotations

import pytest

from repowise.core.analysis.change_health import GitRevisionSource
from repowise.core.analysis.diff_risk import (
    DiffRiskFinding,
    FileTexts,
    detect_diff_risk,
    diff_risk_for_change,
    is_relevant,
)
from repowise.core.analysis.diff_risk.text import added_line_indexes


def change(path: str, base: str | None, head: str | None, *, old: str | None = None) -> FileTexts:
    return FileTexts(
        base_path=(old or path) if base is not None else None,
        head_path=path if head is not None else None,
        base=base,
        head=head,
    )


def found(*files: FileTexts, **kw) -> list[tuple[str, str, str, int | None]]:
    return [(f.rule, f.kind, f.path, f.line) for f in detect_diff_risk(files, **kw)]


def evidence(*files: FileTexts) -> list[str]:
    return [f.evidence for f in detect_diff_risk(files)]


# -- added lines --------------------------------------------------------------


def test_moved_lines_are_not_added():
    assert added_line_indexes(["a", "b", "c"], ["c", "a", "b", "d", ""]) == [3]


# -- added-suppression --------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "line", "label"),
    [
        ("src/a.py", "x = f()  # noqa: E501", "noqa"),
        ("src/a.py", "x = f()  # type: ignore[attr-defined]", "type: ignore"),
        ("src/a.py", "x = f()  # pyright: ignore", "pyright: ignore"),
        ("src/a.py", "x = f()  # pylint: disable=broad-except", "pylint: disable"),
        ("src/a.py", "x = f()  # nosec B101", "nosec"),
        ("src/a.py", "def f():  # pragma: no cover", "pragma: no cover"),
        ("src/a.ts", "// eslint-disable-next-line no-console", "eslint-disable"),
        ("src/a.ts", "/* istanbul ignore next */", "istanbul ignore"),
        ("src/a.tsx", "{/* @ts-ignore */}", "@ts-ignore"),
        ("src/a.js", "// biome-ignore lint: reason", "biome-ignore"),
        ("src/a.go", "x := f() //nolint:errcheck", "nolint"),
        ("src/a.go", "x := f() // #nosec G104", "nosec"),
        ("src/a.rs", "#[allow(dead_code)]", "#[allow]"),
        ("src/a.rs", "#![allow(clippy::all)]", "#[allow]"),
        ("src/A.java", '@SuppressWarnings("unchecked")', "@SuppressWarnings"),
        ("src/A.kt", '@Suppress("UNUSED")', "@SuppressWarnings"),
        ("src/A.cs", "#pragma warning disable CS0168", "#pragma warning disable"),
        ("src/A.cs", '[SuppressMessage("Style", "IDE0060")]', "[SuppressMessage]"),
        ("src/a.rb", "x = f # rubocop:disable Style/Foo", "rubocop:disable"),
        ("src/a.php", "$x = f(); // @phpstan-ignore-line", "@phpstan-ignore"),
        ("src/a.php", "/** @psalm-suppress InvalidArgument */", "@psalm-suppress"),
        ("src/a.php", "// phpcs:ignore Generic.Foo", "phpcs:ignore"),
        ("src/a.swift", "// swiftlint:disable:next force_cast", "swiftlint:disable"),
        ("src/a.py", "TOKEN = 'abc'  # repowise-security-ignore", "repowise-security-ignore"),
        ("docs/guide.md", "<!-- repowise-drift-ignore -->", "repowise-drift-ignore"),
    ],
)
def test_added_suppression_is_found_in_each_language(path, line, label):
    base = "keep = 1\n"
    head = f"keep = 1\n{line}\n"
    findings = detect_diff_risk([change(path, base, head)])
    assert [(f.rule, f.line) for f in findings] == [("added-suppression", 2)]
    assert label in findings[0].message


@pytest.mark.parametrize(
    ("path", "head"),
    [
        # Quoted in a string literal.
        ("src/a.py", "s = '# noqa'\nt = \"# type: ignore\"\n"),
        ("src/a.py", 'DOC = """\nx = 1  # noqa\n"""\n'),
        ("src/a.ts", "const s = '// eslint-disable';\nconst u = `/* @ts-ignore */`;\n"),
        ("src/a.rs", 'fn f(x: &\'static str) { let s = "#[allow(x)]"; }\n'),
        ("src/A.java", 'String s = "@SuppressWarnings(x)";\n'),
        # Named in prose inside a comment, not applied.
        ("src/a.py", "# never add noqa here, fix the code\n"),
        ("src/a.ts", "// we removed the old eslint-disable comments\n"),
        # A JS private field is code, not a hash comment.
        ("src/a.ts", "this.#noqa = 1;\n"),
        # A documented marker in a fence or code span.
        ("docs/guide.md", "Use `<!-- repowise-drift-ignore -->` to skip.\n```\n<!-- repowise-drift-ignore -->\n```\n"),
    ],
)
def test_markers_outside_comments_are_not_suppressions(path, head):
    assert found(change(path, "", head)) == []


def test_editing_a_line_that_already_had_the_suppression_is_not_added():
    base = "x = f(a)  # noqa: E501\n"
    head = "x = f(a, b)  # noqa: E501\n"
    assert found(change("src/a.py", base, head)) == []


def test_moving_a_suppression_is_not_added():
    base = "a = 1  # noqa\nb = 2\n"
    head = "b = 2\na = 1  # noqa\n"
    assert found(change("src/a.py", base, head)) == []


def test_widening_a_suppression_is_added():
    base = "x = f()  # noqa: E501\n"
    head = "x = f()  # noqa: E501, F401\n"
    assert evidence(change("src/a.py", base, head)) == ["# noqa: E501, F401"]


def test_ts_expect_error_in_a_test_is_an_assertion_not_a_suppression():
    head = "// @ts-expect-error\nconst x: number = 'a';\n"
    assert found(change("src/a.test.ts", "", head)) == []
    assert found(change("src/a.ts", "", head)) == [("added-suppression", "comment", "src/a.ts", 1)]


def test_fixture_vendored_and_generated_files_are_exempt():
    head = "x = 1  # noqa\n"
    assert found(change("tests/fixtures/sample/a.py", "", head)) == []
    assert found(change("vendor/lib/a.py", "", head)) == []
    assert found(change("src/a_pb2.py", "", "# Generated by the protocol buffer compiler.  DO NOT EDIT!\n" + head)) == []


def test_a_newly_disabled_biomarker_is_an_added_suppression():
    base = '{"disabled_biomarkers": ["dry_violation"]}'
    head = (
        '{"disabled_biomarkers": ["dry_violation", "complex_method"],'
        ' "rules": [{"path": "src/**", "disabled_biomarkers": ["large_file"]}]}'
    )
    findings = detect_diff_risk([change(".repowise/health-rules.json", base, head)])
    assert [(f.rule, f.kind, f.evidence) for f in findings] == [
        ("added-suppression", "config", "*: complex_method"),
        ("added-suppression", "config", "src/**: large_file"),
    ]


def test_a_blanket_suppression_cannot_hide_an_added_suppression():
    # Inline markers never silence a diff-risk finding, so a file-wide
    # disable comment is itself reported, along with what it covers.
    head = "/* eslint-disable */\n// @ts-nocheck\nconst a = 1; // eslint-disable-line\n"
    assert [f[3] for f in found(change("src/a.ts", "", head))] == [1, 2, 3]


# -- weakened-tests: skip and focus markers ------------------------------------


@pytest.mark.parametrize(
    ("path", "base", "head"),
    [
        ("tests/test_a.py", "def test_a():\n    pass\n", "@pytest.mark.skip(reason='x')\ndef test_a():\n    pass\n"),
        ("tests/test_a.py", "def test_a():\n    pass\n", "@pytest.mark.xfail\ndef test_a():\n    pass\n"),
        ("tests/test_a.py", "def test_a():\n    pass\n", "def test_a():\n    pytest.skip('flaky')\n"),
        ("tests/test_a.py", "class T:\n    def test_a(self):\n        pass\n", "class T:\n    @unittest.skip('x')\n    def test_a(self):\n        pass\n"),
        ("src/a.test.ts", "it('a', () => {});\n", "it.skip('a', () => {});\n"),
        ("src/a.test.ts", "describe('s', () => {});\n", "describe.only('s', () => {});\n"),
        ("src/a.spec.js", "it('a', f);\n", "xit('a', f);\n"),
        ("src/a.spec.js", "it('a', f);\n", "fit('a', f);\n"),
        ("pkg/a_test.go", "func TestA(t *testing.T) {\n}\n", "func TestA(t *testing.T) {\n\tt.Skip(\"flaky\")\n}\n"),
        ("tests/a.rs", "#[test]\nfn a() {}\n", "#[test]\n#[ignore]\nfn a() {}\n"),
        ("src/test/java/ATest.java", "  @Test\n  void a() {}\n", "  @Test\n  @Disabled\n  void a() {}\n"),
        ("tests/ATests.cs", "[Fact]\npublic void A() {}\n", "[Fact(Skip = \"flaky\")]\npublic void A() {}\n"),
        ("spec/a_spec.rb", "it 'works' do\nend\n", "xit 'works' do\nend\n"),
        ("spec/a_spec.rb", "it 'works' do\nend\n", "it 'works' do\n  skip 'later'\nend\n"),
        ("tests/ATest.php", "function testA() {}\n", "function testA() {\n  $this->markTestSkipped('x');\n}\n"),
        ("Tests/ATests.swift", "func testA() {}\n", "func testA() throws {\n  throw XCTSkip(\"x\")\n}\n"),
    ],
)
def test_added_skip_marker_is_found_in_each_language(path, base, head):
    kinds = [(f.rule, f.kind) for f in detect_diff_risk([change(path, base, head)])]
    assert ("weakened-tests", "skip-marker") in kinds


@pytest.mark.parametrize(
    ("path", "head"),
    [
        # Production code may say skip.
        ("src/a.ts", "it.skip('a', f);\n"),
        ("src/a.py", "@pytest.mark.skip\ndef helper():\n    pass\n"),
        # Commented out, quoted, or a different member.
        ("src/a.test.ts", "// it.only('x')\nconst s = 'it.skip(';\nconst profit = obj.fit(1);\n"),
        ("tests/test_a.py", "# @pytest.mark.skip\nreason = 'pytest.skip(x)'\n"),
        # A placeholder is not a weakened test.
        ("src/a.test.ts", "it.todo('write later');\n"),
        ("spec/a_spec.rb", "skip_count = 1\nskip = true\n"),
    ],
)
def test_no_skip_marker_outside_test_code(path, head):
    assert [f for f in detect_diff_risk([change(path, "", head)]) if f.kind == "skip-marker"] == []


def test_existing_skip_marker_is_not_added():
    base = "it.skip('a', () => {});\n"
    head = "it.skip('a renamed', () => {});\n"
    assert found(change("src/a.test.ts", base, head)) == []


# -- weakened-tests: removed tests and assertions -----------------------------

PY_TESTS = "def test_a():\n    assert f() == 1\n\n\ndef test_b():\n    assert keep_me() == 2\n"


def test_removed_tests_are_found():
    head = "def test_a():\n    assert f() == 1\n"
    assert found(change("tests/test_x.py", PY_TESTS, head)) == [
        ("weakened-tests", "removed-tests", "tests/test_x.py", None)
    ]


def test_tests_that_moved_to_another_test_file_are_not_removed():
    head = "def test_a():\n    assert f() == 1\n"
    moved = "def test_b():\n    assert keep_me() == 2\n"
    assert found(change("tests/test_x.py", PY_TESTS, head), change("tests/test_y.py", None, moved)) == []


def test_tests_removed_with_the_code_they_named_are_not_reported():
    source_base = "def f():\n    return 1\n\n\ndef keep_me():\n    return 2\n"
    source_head = "def f():\n    return 1\n"
    head = "def test_a():\n    assert f() == 1\n"
    files = (change("src/x.py", source_base, source_head), change("tests/test_x.py", PY_TESTS, head))
    assert found(*files) == []


def test_a_deleted_test_file_with_its_deleted_module_is_not_reported():
    files = (
        change("src/legacy_parser.py", "def parse_legacy():\n    return 1\n", None),
        change("tests/test_legacy_parser.py", "from src.legacy_parser import parse_legacy\n\n\ndef test_p():\n    assert parse_legacy() == 1\n", None),
    )
    assert found(*files) == []


def test_assertion_drop_is_found_when_assertions_lead_the_removal():
    base = "it('a', () => {\n  expect(a).toBe(1);\n  expect(b).toBe(2);\n  expect(c).toBe(3);\n});\n"
    head = "it('a', () => {\n  expect(a).toBe(1);\n});\n"
    findings = detect_diff_risk([change("src/a.test.ts", base, head)])
    assert [(f.kind, f.evidence) for f in findings] == [("assertion-drop", "assertions: 3 -> 1")]


def test_a_rewritten_test_is_not_an_assertion_drop():
    base = (
        "it('a', () => {\n  const x = setup();\n  const y = build(x);\n  const z = run(y);\n"
        "  const w = more(z);\n  expect(x).toBe(1);\n  expect(y).toBe(2);\n});\n"
    )
    head = "it('a', () => {\n  const w = helper();\n  expect(w).toBe(1);\n});\n"
    assert found(change("src/a.test.ts", base, head)) == []


@pytest.mark.parametrize(
    ("path", "base", "head"),
    [
        ("pkg/a_test.go", "func TestA(t *testing.T) {}\nfunc TestB(t *testing.T) {}\n", "func TestA(t *testing.T) {}\n"),
        ("tests/a.rs", "#[test]\nfn a() {}\n#[tokio::test]\nasync fn b() {}\n", "#[test]\nfn a() {}\n"),
        ("src/test/java/ATest.java", "  @Test\n  void a() {}\n  @Test\n  void b() {}\n", "  @Test\n  void a() {}\n"),
        ("spec/a_spec.rb", "it 'a' do\nend\nit 'b' do\nend\n", "it 'a' do\nend\n"),
        ("tests/ATests.cs", "[Fact]\nvoid A() {}\n[Fact]\nvoid B() {}\n", "[Fact]\nvoid A() {}\n"),
    ],
)
def test_removed_tests_in_each_language(path, base, head):
    assert [f.kind for f in detect_diff_risk([change(path, base, head)])] == ["removed-tests"]


def test_a_class_level_testng_annotation_is_not_a_test_case():
    base = '@Test(dataProviderClass = P.class)\npublic final class ATest {\n  @Test(dataProvider = "c")\n  public void a() {}\n}\n'
    head = "final class ATest {\n  @ParameterizedTest\n  void a() {}\n}\n"
    assert found(change("src/test/java/ATest.java", base, head)) == []


def test_a_test_turned_into_a_todo_is_a_removed_test():
    base = "it('a', f);\nit('b', g);\n"
    head = "it('a', f);\nit.todo('b');\n"
    assert [f.kind for f in detect_diff_risk([change("src/a.test.ts", base, head)])] == ["removed-tests"]


# -- weakened-tests: thresholds ------------------------------------------------


@pytest.mark.parametrize(
    ("path", "base", "head", "expected"),
    [
        ("pyproject.toml", "[tool.coverage.report]\nfail_under = 90\n", "[tool.coverage.report]\nfail_under = 85.5\n", "fail_under: 90 -> 85.5"),
        ("setup.cfg", "[tool:pytest]\naddopts = --cov-fail-under=80\n", "[tool:pytest]\naddopts = --cov-fail-under=60\n", "--cov-fail-under: 80 -> 60"),
        ("vitest.config.ts", "thresholds: {\n  lines: 90,\n  branches: 80,\n},\n", "thresholds: {\n  lines: 70,\n  branches: 85,\n},\n", "lines: 90 -> 70"),
        ("package.json", '{"jest": {"coverageThreshold": {"global": {"statements": 95}}}}', '{"jest": {"coverageThreshold": {"global": {"statements": 50}}}}', "statements: 95 -> 50"),
        (".flake8", "[flake8]\nmax-complexity = 10\n", "[flake8]\nmax-complexity = 18\n", "max-complexity: 10 -> 18"),
        ("codecov.yml", "coverage:\n  status:\n    project:\n      default:\n        target: 80%\n", "coverage:\n  status:\n    project:\n      default:\n        target: 60%\n", "target: 80 -> 60"),
    ],
)
def test_loosened_threshold_is_found(path, base, head, expected):
    assert evidence(change(path, base, head)) == [expected]


def test_tightened_or_unrelated_threshold_is_not_found():
    assert found(change("pyproject.toml", "fail_under = 80\nmaxLines = 5\n", "fail_under = 90\nmaxLines = 1\n")) == []
    assert found(change("src/lines.ts", "const lines = 90;\n", "const lines = 10;\n")) == []


# -- test-only-branch ---------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("src/a.ts", "if (process.env.NODE_ENV === 'test') return;"),
        ("src/a.ts", "const w = process.env.JEST_WORKER_ID;"),
        ("src/a.py", "if 'pytest' in sys.modules:"),
        ("src/a.py", "if os.environ.get('TESTING'):"),
        ("src/a.py", "if os.getenv('APP_ENV') == 'test':"),
        ("pkg/a.go", "if testing.Testing() {"),
        ("src/a.rs", "if cfg!(test) {"),
        ("app/models/a.rb", "return if Rails.env.test?"),
        ("app/Services/A.php", "if (app()->runningUnitTests()) {"),
        ("src/A.cs", 'if (env.IsEnvironment("Testing")) {'),
    ],
)
def test_added_test_env_check_is_found(path, line):
    assert found(change(path, "", f"{line}\n")) == [("test-only-branch", "test-env-check", path, 1)]


@pytest.mark.parametrize(
    ("path", "head"),
    [
        # Test code, tool configs and settings modules check on purpose.
        ("tests/test_a.py", "if 'pytest' in sys.modules:\n    pass\n"),
        ("vite.config.ts", "if (process.env.NODE_ENV === 'test') x();\n"),
        ("myapp/settings.py", "if 'test' in sys.argv:\n    DEBUG = False\n"),
        # Inside a string or a comment.
        ("src/a.ts", "const s = \"process.env.VITEST\";\n// if (process.env.NODE_ENV === 'test')\n"),
        # A comparison against another environment.
        ("src/a.ts", "if (process.env.NODE_ENV === 'production') x();\n"),
        # Picks an env file.
        ("src/a.ts", "config({ path: process.env.NODE_ENV === 'test' ? '.env.test' : '.env' });\n"),
        # Keeps tests quiet; production takes the same path.
        ("src/a.ts", "if (process.env.NODE_ENV !== 'test') {\n  console.error(message);\n}\n"),
        ("src/a.py", "if 'pytest' not in sys.modules:\n    logger.warning('x')\nrun()\n"),
        # A read with no branch outside JS is configuration.
        ("src/a.py", "TESTING = os.environ.get('TESTING')\n"),
        # The Rust test module.
        ("src/a.rs", "#[cfg(test)]\nmod tests {}\n"),
    ],
)
def test_no_test_only_branch(path, head):
    assert found(change(path, "", head)) == []


def test_a_negated_check_guarding_real_work_is_found():
    head = "if (process.env.NODE_ENV !== 'test') {\n  sendMetrics();\n}\n"
    assert found(change("src/a.ts", "", head)) == [("test-only-branch", "test-env-check", "src/a.ts", 1)]


# -- shape, severity, identity ------------------------------------------------


def test_severity_is_warn_by_default_and_configurable():
    head = "x = 1  # noqa\n"
    (default,) = detect_diff_risk([change("src/a.py", "", head)])
    assert default.severity == "warn"
    (error,) = detect_diff_risk([change("src/a.py", "", head)], severities={"added-suppression": "error"})
    assert error.severity == "error"
    assert detect_diff_risk([change("src/a.py", "", head)], severities={"added-suppression": "off"}) == []


def test_finding_id_is_line_free_and_serializable():
    head = "x = 1  # noqa\n"
    (first,) = detect_diff_risk([change("src/a.py", "", head)])
    (shifted,) = detect_diff_risk([change("src/a.py", "", "\n\nimport os\n" + head)])
    assert first.finding_id == shifted.finding_id
    assert first.finding_id.startswith("drf_")
    assert first.line != shifted.line
    assert set(first.as_dict()) == set(DiffRiskFinding.__dataclass_fields__)


def test_custom_test_classifier():
    head = "it.skip('a', f);\n"
    assert detect_diff_risk([change("checks/a.ts", "", head)]) == []
    findings = detect_diff_risk([change("checks/a.ts", "", head)], is_test=lambda p, _lang: p.startswith("checks/"))
    assert [f.kind for f in findings] == ["skip-marker"]


def test_relevance():
    for path in ("src/a.py", "docs/a.md", "pyproject.toml", "package.json", ".repowise/health-rules.json"):
        assert is_relevant(path), path
    for path in ("assets/logo.png", "tests/fixtures/a.py", "yarn.lock", "t/GOLDEN_PARTIAL.js"):
        assert not is_relevant(path), path


# -- git adapter --------------------------------------------------------------


def test_git_adapter_reads_a_rename_at_its_old_path(make_repo):
    repo = make_repo()
    body = "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(12))
    base = repo.commit("base", {"src/old.py": body + "x = 1  # noqa\n"})
    repo.move("src/old.py", "src/new.py")
    repo.write("src/new.py", body + "x = 1  # noqa\ny = 2  # type: ignore\n")
    head = repo.commit("move")
    source = GitRevisionSource(str(repo.path))
    findings = diff_risk_for_change(source, source.resolve(f"{base}..{head}"))
    assert [(f.path, f.evidence) for f in findings] == [("src/new.py", "# type: ignore")]


def test_git_adapter_reads_the_working_tree(make_repo):
    repo = make_repo()
    repo.commit("base", {"src/a.ts": "const a = 1;\n"})
    repo.write("src/a.ts", "// @ts-ignore\nconst a = 1;\n")
    source = GitRevisionSource(str(repo.path))
    findings = diff_risk_for_change(source, source.resolve(None))
    assert [(f.rule, f.line) for f in findings] == [("added-suppression", 1)]


# -- change-wide netting and guards -------------------------------------------


def test_a_suppression_that_moved_to_another_file_is_not_added():
    # A module split carries its suppressions along.
    base = "import a\nctx.x = 1  # type: ignore[attr-defined]\nctx.y = 2  # type: ignore[attr-defined]\n"
    files = (
        change("src/pkg.py", base, "import a\n"),
        change("src/pkg_x.py", None, "ctx.x = 1  # type: ignore[attr-defined]\n"),
        change("src/pkg_y.py", None, "ctx.y = 2  # type: ignore[attr-defined]\n"),
    )
    assert found(*files) == []


def test_a_suppression_out_of_a_deleted_file_is_not_added():
    files = (
        change("src/old.py", "x = 1  # noqa: F401\n", None),
        change("src/new.py", None, "x = 1  # noqa: F401\n"),
    )
    assert found(*files) == []


def test_only_the_net_extra_suppression_is_reported():
    files = (
        change("src/a.py", "a = 1  # noqa\n", "a = 1\n"),
        change("src/b.py", None, "b = 1  # noqa\nc = 2  # noqa\n"),
    )
    assert found(*files) == [("added-suppression", "comment", "src/b.py", 1)]


@pytest.mark.parametrize(
    ("path", "head"),
    [
        ("tests/test_a.py", "@pytest.mark.skipif(sys.platform == 'win32', reason='x')\ndef test_a():\n    pass\n"),
        ("tests/test_a.py", "@pytest.mark.xfail(sys.platform == 'win32', reason='x')\ndef test_a():\n    pass\n"),
        ("tests/test_a.py", "def test_a():\n    if not HAS_GO:\n        pytest.skip('go missing')\n"),
        ("tests/test_a.py", "@unittest.skipIf(PY2, 'x')\ndef test_a(self):\n    pass\n"),
        ("src/a.test.ts", "it.skipIf(process.platform === 'win32')('a', f);\n"),
        ("pkg/a_test.go", "func TestA(t *testing.T) {\n\tif testing.Short() {\n\t\tt.Skip()\n\t}\n}\n"),
        ("spec/a_spec.rb", "it 'a' do\n  skip 'later' unless windows?\nend\n"),
        ("tests/test_a.py", "def test_a():\n    try:\n        import x\n    except ImportError:\n        pytest.skip('x missing')\n"),
        ("tests/test_a.py", "@skip.if_pypy\ndef test_a():\n    pass\n"),
    ],
)
def test_conditional_skips_guard_an_environment(path, head):
    assert [f for f in detect_diff_risk([change(path, "", head)]) if f.kind == "skip-marker"] == []


def test_unconditional_xfail_is_a_skip_marker():
    head = "@pytest.mark.xfail(reason='flaky')\ndef test_a():\n    pass\n"
    assert [f.kind for f in detect_diff_risk([change("tests/test_a.py", "", head)])] == ["skip-marker"]


def test_a_marker_quoted_in_a_comment_code_span_is_documented():
    head = "# Never add ``# noqa`` here; fix the import instead.\nx = 1\n"
    assert found(change("src/a.py", "", head)) == []


def test_a_quote_the_whole_file_mask_misreads_does_not_read_as_lost_tests():
    # The apostrophe in the regex literal opens a "string" for the rest of
    # the whole-file mask; the lines the change removed hold no test case.
    base = "it('a', f);\nit('b', g);\nit('c', h);\n"
    head = "it('new', () => expect(() => x()).toThrow(/NG0: 'bad/));\n" + base
    assert found(change("src/a.spec.ts", base, head)) == []


def test_folding_two_expectations_into_one_is_not_an_assertion_drop():
    base = (
        "function testA() {\n  $this->expectException(E::class);\n"
        "  $this->expectExceptionMessage('m');\n  run();\n}\n"
    )
    head = "function testA() {\n  $this->expectExceptionObject(new E('m'));\n  run();\n}\n"
    assert found(change("tests/ATest.php", base, head)) == []


# -- review regressions -------------------------------------------------------


@pytest.mark.parametrize(
    "head",
    [
        "# repowise-security-ignore\n",
        "* gitleaks:allow suppresses a secret\n",
        "text -- repowise-drift-ignore here\n",
    ],
)
def test_prose_outside_an_html_comment_is_not_a_suppression(head):
    assert found(change("docs/a.md", "", head)) == []


def test_a_multi_line_html_comment_in_prose_is_read():
    head = "Intro\n<!--\nrepowise-drift-ignore-file\n-->\n"
    assert found(change("docs/a.md", "", head)) == []
    head = "Intro\n<!-- note\n-- repowise-drift-ignore -->\n"
    assert [f[3] for f in found(change("docs/a.md", "", head))] == [3]


@pytest.mark.parametrize(
    ("path", "head"),
    [
        ("src/a.py", "x = 1  # we never write -- noqa here\n"),
        ("src/a.py", "x = 1  # a * noqa\n"),
        ("src/a.ts", "// see http://eslint-disable.example\n"),
    ],
)
def test_a_marker_in_the_middle_of_a_comment_is_prose(path, head):
    assert found(change(path, "", head)) == []


def test_a_nested_hash_comment_marker_still_counts():
    assert [f[3] for f in found(change("src/a.py", "", "x = 1  # why  # noqa: E501\n"))] == [1]


@pytest.mark.parametrize(
    ("path", "head"),
    [
        ("tests/test_a.py", "def test_a():\n    if not x:\n        log('a')\n        pytest.skip('no')\n"),
        ("pkg/a_test.go", "func TestA(t *testing.T) {\n\tif !ok {\n\t\tt.Log(1)\n\t\tt.Skip()\n\t}\n}\n"),
    ],
)
def test_a_skip_inside_a_guard_block_is_conditional(path, head):
    assert [f for f in detect_diff_risk([change(path, "", head)]) if f.kind == "skip-marker"] == []


def test_an_unguarded_skip_below_a_finished_guard_still_counts():
    head = "def test_a():\n    if x:\n        y()\n\n\ndef test_b():\n    pytest.skip('off')\n"
    kinds = [f.kind for f in detect_diff_risk([change("tests/test_a.py", "", head)])]
    assert kinds == ["skip-marker"]


def test_an_added_or_reordered_threshold_is_not_paired_by_position():
    base = '{"coverageThreshold": {"global": {"lines": 90}}}'
    head = '{"coverageThreshold": {"./src": {"lines": 50}, "global": {"lines": 90}}}'
    assert found(change("package.json", base, head)) == []
    base = "[tool.coverage.report]\nfail_under = 90\n"
    head = "[tool.coverage.report]\n# was fail_under = 70\nfail_under = 90\n"
    assert found(change("pyproject.toml", base, head)) == []


def test_narrowing_a_global_disable_to_a_path_is_not_a_suppression():
    base = '{"disabled_biomarkers": ["x"]}'
    head = '{"rules": [{"path": "src/a.py", "disabled_biomarkers": ["x"]}]}'
    assert found(change(".repowise/health-rules.json", base, head)) == []


def test_tests_folded_into_a_parametrized_test_are_not_removed():
    base = "def test_one():\n    assert f(1)\n\n\ndef test_two():\n    assert f(2)\n"
    head = "@pytest.mark.parametrize('x', [1, 2])\ndef test_f(x):\n    assert f(x)\n"
    assert found(change("tests/test_a.py", base, head)) == []


def test_a_plain_default_read_is_not_a_test_only_branch():
    assert found(change("src/app.py", "", "testing = os.environ.get('TESTING') or ''\n")) == []


def test_a_base_marker_the_mask_missed_does_not_make_an_edited_line_added():
    # The apostrophe in the base regex literal derails the base mask; the
    # head edits a line that already carried the same suppression.
    base = "const r = /it's/;\nfoo(); // eslint-disable-line no-console\n"
    head = "const r = /it's/;\nfoo(1); // eslint-disable-line no-console\n"
    assert found(change("src/a.ts", base, head)) == []


def test_an_unchanged_block_comment_suppression_on_an_edited_line_is_not_added():
    base = "/* istanbul ignore next */ foo();\n"
    head = "/* istanbul ignore next */ bar();\n"
    assert found(change("src/a.ts", base, head)) == []


def test_detection_touches_no_filesystem_or_git(monkeypatch):
    import builtins
    import subprocess

    def refuse(*_a, **_k):
        raise AssertionError("detect_diff_risk must be pure")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    files = (
        change("src/a.py", "", "x = 1  # noqa\n"),
        change("tests/test_a.py", "def test_a():\n    pass\n", "@pytest.mark.skip\ndef test_a():\n    pass\n"),
        change("src/b.py", "", "if 'pytest' in sys.modules:\n    pass\n"),
    )
    assert len(detect_diff_risk(files)) == 3


class _Source:
    """A revision source whose reads fail or return binary bytes."""

    def __init__(self, base: dict[str, bytes], head: dict[str, bytes]) -> None:
        self.base, self.head = base, head

    def read(self, sha: str, paths: list[str]) -> dict[str, bytes]:
        side = self.base if sha == "b" else self.head
        return {p: side[p] for p in paths if p in side}

    def read_working_tree(self, paths: list[str]) -> dict[str, bytes]:
        return {}


def test_a_failed_or_binary_read_drops_the_file():
    from repowise.core.analysis.change_health import FileChange, RevisionPair
    from repowise.core.analysis.diff_risk import read_change_texts

    pair = RevisionPair(
        "b", "h", "b", "h", False,
        [
            FileChange(head_path="src/a.py", base_path="src/a.py", status="modified"),
            FileChange(head_path="src/b.py", base_path="src/b.py", status="modified"),
            FileChange(head_path="src/c.py", base_path=None, status="added"),
        ],
    )  # fmt: skip
    source = _Source(
        {"src/b.py": b"x = 1\n"},
        {"src/a.py": b"x = 1  # noqa\n", "src/b.py": b"\x00binary", "src/c.py": b"y = 2  # noqa\n"},
    )
    assert [f.path for f in read_change_texts(source, pair)] == ["src/c.py"]
