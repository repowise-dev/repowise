"""The marker vocabulary of the diff-risk rules, as data.

Every rule reads these tables; no rule holds a language branch of its own. A
row names the languages it applies to (``None`` means every code language)
and the regular expression that finds it. Adding a linter or a test framework
is one row here, never new code.

Patterns run on a per-line view of the file (see :mod:`.text`):

- suppression patterns run on the *comment* view, where only comment text
  is left, and start at a comment opener, so a marker named in prose inside a
  comment or quoted in a string literal never counts;
- every other pattern runs on the *text* view (comments blanked, strings
  kept), and its match must start in code, not inside a string literal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

JS = frozenset({"javascript", "typescript", "vue", "svelte", "astro"})
PY = frozenset({"python"})
GO = frozenset({"go"})
RUST = frozenset({"rust"})
JVM = frozenset({"java", "kotlin"})
CSHARP = frozenset({"csharp"})
C_FAMILY = frozenset({"c", "cpp", "objectivec"})
RUBY = frozenset({"ruby"})
PHP = frozenset({"php"})
SWIFT = frozenset({"swift"})
DART = frozenset({"dart"})

#: Languages where ``#`` opens a line comment. Elsewhere ``#`` is code: a
#: preprocessor line, a Rust or PHP attribute, a JS private field.
HASH_COMMENT_LANGUAGES = frozenset(
    {
        "python", "ruby", "php", "shell", "perl", "r", "elixir", "crystal", "nim", "julia",
        "yaml", "toml", "dockerfile", "makefile", "terraform",
    }
)

#: Documentation files whose HTML comments can carry a repowise marker.
PROSE_SUFFIXES = (".md", ".mdx")

#: Where a comment starts: a marker must follow one directly. At the start of
#: a comment any opener counts (``*`` is a docblock continuation line); inside
#: one only a nested ``#`` or ``//`` after a space does, so
#: prose such as ``# a * noqa`` or a URL never matches.
_COMMENT_OPENER = (
    r"(?:(?:^|(?<=\x00))[ \t]*(?:#|//+|/\*+|\*|<!--|--)|(?<=[ \t])(?:#|//+))[ \t]*"
)


@dataclass(frozen=True, slots=True)
class Marker:
    """One textual marker a rule looks for."""

    label: str
    pattern: re.Pattern[str]
    #: ``None``: every code language.
    languages: frozenset[str] | None = None
    #: Also honoured in Markdown prose, inside an HTML comment.
    prose: bool = False

    def applies_to(self, language: str | None, *, is_prose: bool) -> bool:
        if is_prose:
            return self.prose
        if language is None:
            return False
        return self.languages is None or language in self.languages


def _comment(label: str, body: str, languages: frozenset[str] | None, **kw: bool) -> Marker:
    return Marker(label, re.compile(_COMMENT_OPENER + "(?:" + body + ")"), languages, **kw)


def _code(label: str, body: str, languages: frozenset[str] | None, flags: int = 0) -> Marker:
    return Marker(label, re.compile(body, flags), languages)


# -- added-suppression --------------------------------------------------------

#: Comment markers that turn a check off. The comment text from the marker to
#: the end of the line is the marker's identity, so adding a code to an
#: existing noqa list is an added suppression too.
COMMENT_SUPPRESSIONS: tuple[Marker, ...] = (
    _comment("eslint-disable", r"eslint-disable\b", JS),
    _comment("oxlint-disable", r"oxlint-disable\b", JS),
    _comment("biome-ignore", r"biome-ignore\b", JS),
    _comment("deno-lint-ignore", r"deno-lint-ignore\b", JS),
    _comment("@ts-ignore", r"@ts-ignore\b", JS),
    _comment("@ts-expect-error", r"@ts-expect-error\b", JS),
    _comment("@ts-nocheck", r"@ts-nocheck\b", JS),
    _comment("istanbul ignore", r"istanbul[ \t]+ignore\b", JS),
    _comment("c8 ignore", r"c8[ \t]+ignore\b", JS),
    _comment("v8 ignore", r"v8[ \t]+ignore\b", JS),
    _comment("noqa", r"noqa\b", PY),
    _comment("type: ignore", r"type:[ \t]*ignore\b", PY),
    _comment("pyright: ignore", r"pyright:[ \t]*ignore\b", PY),
    _comment("pylint: disable", r"pylint:[ \t]*disable\b", PY),
    _comment("pragma: no cover", r"pragma:[ \t]*no[ \t]+(?:cover|branch)\b", PY),
    _comment("nosec", r"#?nosec\b", PY | GO),
    _comment("nolint", r"nolint\b", GO),
    _comment("NOLINT", r"NOLINT(?:NEXTLINE|BEGIN)?\b", C_FAMILY),
    _comment("cppcheck-suppress", r"cppcheck-suppress\b", C_FAMILY),
    _comment("ReSharper disable", r"ReSharper[ \t]+disable\b", CSHARP),
    _comment("CHECKSTYLE:OFF", r"CHECKSTYLE[:.]OFF\b", JVM),
    _comment("rubocop:disable", r"rubocop[ \t]*:[ \t]*(?:disable|todo)\b", RUBY),
    _comment("phpcs:ignore", r"phpcs:(?:ignore|disable)\b", PHP),
    _comment("@phpstan-ignore", r"@phpstan-ignore", PHP),
    _comment("@psalm-suppress", r"@psalm-suppress\b", PHP),
    _comment("@codeCoverageIgnore", r"@codeCoverageIgnore", PHP),
    _comment("swiftlint:disable", r"swiftlint:disable\b", SWIFT),
    _comment("ignore:", r"ignore(?:_for_file)?[ \t]*:", DART),
    _comment("NOSONAR", r"NOSONAR\b", None),
    # Markers repowise's own gates honour.
    _comment("repowise-security-ignore", r"repowise-security-ignore\b", None, prose=True),
    _comment("repowise-drift-ignore", r"repowise-drift-ignore", None, prose=True),
    _comment("pragma: allowlist secret", r"pragma:[ \t]*allowlist\b", None, prose=True),
    _comment("gitleaks:allow", r"gitleaks:allow\b", None, prose=True),
    _comment("trufflehog:ignore", r"trufflehog:ignore\b", None, prose=True),
)

#: Suppressions written as code: an attribute, annotation or pragma.
CODE_SUPPRESSIONS: tuple[Marker, ...] = (
    _code("#[allow]", r"#!?\[\s*allow\s*\(", RUST),
    _code(
        "@SuppressWarnings",
        r"(?<![\w.])@(?:file:)?Suppress(?:Warnings|Lint|FBWarnings)?\s*\(",
        JVM,
    ),
    _code("#pragma warning disable", r"^\s*#\s*pragma\s+warning\s+disable\b", CSHARP),
    _code(
        "[SuppressMessage]",
        r"\[\s*(?:assembly\s*:\s*|module\s*:\s*)?(?:[\w.]+\.)?SuppressMessage(?:Attribute)?\s*\(",
        CSHARP,
    ),
    _code(
        "#pragma diagnostic ignored",
        r"^\s*#\s*pragma\s+(?:(?:GCC|clang)\s+diagnostic\s+ignored|warning\s*\(\s*disable)\b",
        C_FAMILY,
    ),
)

#: The marker a test may add on purpose: in a test, ``@ts-expect-error``
#: asserts that a type error exists.
TEST_ALLOWED_SUPPRESSIONS = frozenset({"@ts-expect-error"})


# -- weakened-tests -----------------------------------------------------------

#: A skip or focus marker in a test file: the test (or every other test) no
#: longer runs. Conditional skips (``skipif``, ``skipIf``, ``XCTSkipIf``, an
#: ``xfail`` with a condition) guard an environment, so they are absent, as is
#: ``.todo``: a new placeholder weakens nothing, and a test turned into one
#: drops the test-case count instead.
SKIP_MARKERS: tuple[Marker, ...] = (
    _code(
        "skip/only",
        r"(?<![\w$.])(?:it|test|specify|describe|suite|context)(?:\.\w+)*"
        r"\.(?:skip|only|fixme)(?![\w$])",
        JS,
    ),
    _code(
        "x/f prefix",
        r"(?<![\w$.])(?:xit|xtest|xdescribe|xcontext|xspecify|fit|fdescribe|fcontext)\s*\(",
        JS,
    ),
    _code(
        "pytest.mark.skip",
        r"(?<![\w.])(?:pytest\.)?mark\.(?:skip\b(?!if)"
        r"|xfail\b(?!\s*\((?!\s*(?:\)|(?:reason|strict|raises|run)\s*=))))",
        PY,
    ),
    _code("pytest.skip", r"(?<![\w.])pytest\.(?:skip|xfail)\s*\(", PY),
    _code(
        "unittest.skip",
        r"^\s*@(?:unittest\.)?(?:skip|expectedFailure)\b(?!\.)"
        r"|(?<![\w.])self\.skipTest\s*\(",
        PY,
    ),
    _code("t.Skip", r"(?<![\w.])[tb]\.Skip(?:f|Now)?\s*\(", GO),
    _code("#[ignore]", r"#\[\s*ignore\b", RUST),
    _code("@Disabled", r"(?<![\w.])@(?:Disabled|Ignore)(?![\w.])", JVM),
    _code(
        "[Ignore]",
        r"[\[,]\s*Ignore\s*[\](,]|\[\s*(?:Fact|Theory)\s*\([^\]]*\bSkip\s*=",
        CSHARP,
    ),
    _code(
        "xit/pending",
        r"^\s*(?:xit|xspecify|xexample|xscenario|xdescribe|xcontext|xfeature"
        r"|fit|fspecify|fexample|fscenario|fdescribe|fcontext|ffeature)\b(?![?!=:])"
        r"|^\s*(?:pending|skip)\b(?!\s*(?:=[^=~]|\.|::|,|\)|\?|!))"
        r"|^\s*(?:it|specify|example|scenario|describe|context|feature)\b.*"
        r"(?<![\w:])(?:skip|pending|focus):(?!:)",
        RUBY,
    ),
    _code("markTestSkipped", r"(?:->|::)markTest(?:Skipped|Incomplete)\s*\(", PHP),
    _code("XCTSkip", r"(?<![\w.])XCTSkip\s*\(|@(?:Test|Suite)\s*\(.*\.disabled\b", SWIFT),
)

#: One test case. Only the counts matter, so a row need not see every style.
TEST_CASES: tuple[Marker, ...] = (
    _code(
        "test case",
        r"(?<![\w$.])(?:it|test|specify|xit|xtest|fit)"
        r"(?:\.(?:skip|only|each|concurrent|sequential|fails|failing|fixme|skipIf|runIf))*"
        r"\s*[(`]",
        JS,
    ),
    _code("test case", r"^\s*(?:async\s+)?def\s+test\w*\s*\(", PY),
    _code("test case", r"^func\s+(?:\([^)]*\)\s*)?(?:Test|Fuzz)\w*\s*\(", GO),
    _code("test case", r"#\[\s*(?:\w+::)*test\s*[\]\(]", RUST),
    _code(
        "test case",
        # Indented: a class-level TestNG ``@Test`` at column 0 is no test case.
        r"^\s+@(?:[\w.]+\.)?(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)"
        r"(?![\w.])",
        JVM,
    ),
    _code("test case", r"[\[,]\s*(?:Fact|Theory|Test|TestMethod)\s*[\](,]", CSHARP),
    _code(
        "test case",
        r"^\s*(?:it|specify|example|scenario|its|xit|fit|xspecify|fspecify)\b"
        r"(?=\s*(?:[\"'({:]|do\b|$))|^\s*def\s+test_\w*",
        RUBY,
    ),
    _code("test case", r"\bfunction\s+test\w*\s*\(", PHP),
    _code("test case", r"\bfunc\s+test\w*\s*\(|(?<![\w.])@Test\b", SWIFT),
)

#: One assertion.
ASSERTIONS: tuple[Marker, ...] = (
    _code(
        "assertion",
        r"(?<![\w$.])(?:expect(?:\.(?:soft|poll))?|assert(?:\.\w+)*)\s*\(|\.should[.(]",
        JS,
    ),
    _code(
        "assertion",
        r"(?<![\w.])assert\b|(?<![\w.])self\.assert\w*\s*\(|(?<![\w.])pytest\.raises\s*\("
        r"|\.assert_\w+\s*\(",
        PY,
    ),
    _code(
        "assertion",
        r"(?<![\w.])(?:assert|require)\.\w+\s*\("
        r"|(?<![\w.])t\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\s*\(",
        GO,
    ),
    _code("assertion", r"(?<![\w.])(?:debug_)?assert(?:_\w+)?!\s*[(\[]", RUST),
    _code(
        "assertion",
        r"(?<![\w.])assert\w*\s*\(|\bAssert(?:ions)?\.\w+\s*\(|(?<![\w.])verify\s*\("
        r"|\bshould[A-Z]\w*\b",
        JVM,
    ),
    _code("assertion", r"\b\w*Assert\.\w+\s*\(|\.Should\(\)", CSHARP),
    _code(
        "assertion",
        r"(?<![\w.])(?:expect|is_expected)\b|(?<![\w.])(?:assert|refute)\w*\b|\.should\b",
        RUBY,
    ),
    _code("assertion", r"(?:->|::)(?:assert\w+|expectException\w*)\s*\(", PHP),
    _code("assertion", r"\bXCTAssert\w*\s*\(|\bXCTFail\s*\(|#(?:expect|require)\s*\(", SWIFT),
)


@dataclass(frozen=True, slots=True)
class ThresholdFile:
    """A config file whose numeric keys gate coverage or quality."""

    names: frozenset[str]
    prefixes: tuple[str, ...]
    #: Keys where a lower value is less strict.
    minimums: tuple[str, ...]
    #: Keys where a higher value is less strict.
    maximums: tuple[str, ...] = ()

    def matches(self, name: str) -> bool:
        return name in self.names or name.startswith(self.prefixes)


THRESHOLD_FILES: tuple[ThresholdFile, ...] = (
    ThresholdFile(
        frozenset(
            {"pyproject.toml", "setup.cfg", "tox.ini", ".coveragerc", "pytest.ini", ".flake8"}
        ),
        (),
        ("fail_under", "--cov-fail-under"),
        ("max-complexity", "max_complexity"),
    ),
    ThresholdFile(
        frozenset(
            {"package.json", ".nycrc", ".nycrc.json", ".nycrc.yml", ".nycrc.yaml", ".c8rc"}
            | {".c8rc.json"}
        ),
        ("vitest.config.", "vitest.workspace.", "vite.config.", "jest.config.", "karma.conf."),
        ("branches", "functions", "lines", "statements"),
    ),
    ThresholdFile(frozenset({"tarpaulin.toml", ".tarpaulin.toml"}), (), ("fail-under",)),
    ThresholdFile(
        frozenset({"codecov.yml", ".codecov.yml", "codecov.yaml", ".codecov.yaml"}),
        (),
        ("target",),
        ("threshold",),
    ),
)


# -- test-only-branch ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TestEnvCheck:
    """A check that reads whether the code runs under a test.

    *code* must match the text view at a position that is code (the token is
    not quoted); *text*, when set, must match the same line's text view too,
    so a check may name its literal (``'test'``) as well as its token.
    """

    __test__ = False  # not a pytest class

    label: str
    languages: frozenset[str]
    code: re.Pattern[str]
    text: re.Pattern[str] | None = None


def _env(label: str, languages: frozenset[str], code: str, text: str | None = None) -> TestEnvCheck:
    return TestEnvCheck(label, languages, re.compile(code), re.compile(text) if text else None)


_COMPARES_TEST = r"""[!=]==?\s*['"`]test(?:ing)?['"`]|['"`]test(?:ing)?['"`]\s*[!=]==?"""

TEST_ENV_CHECKS: tuple[TestEnvCheck, ...] = (
    _env("process.env.NODE_ENV", JS, r"\bprocess\.env\.NODE_ENV\b", _COMPARES_TEST),
    _env("process.env.VITEST", JS, r"\bprocess\.env\.(?:VITEST|JEST_WORKER_ID)\b"),
    _env(
        "pytest in sys.modules",
        PY,
        r"\bsys\.(?:modules|argv)\b",
        r"""['"]_?pytest['"]\s+(?:not\s+)?in\s+sys\.(?:modules|argv)"""
        r"""|['"]test['"]\s+(?:not\s+)?in\s+sys\.argv""",
    ),
    _env(
        "os.environ test check",
        PY,
        r"\bos\.(?:environ|getenv)\b",
        r"""['"](?:PYTEST_CURRENT_TEST|TESTING|TEST_MODE|UNIT_TESTING|RUNNING_TESTS)['"]"""
        r"""|[!=]=\s*['"]test(?:ing)?['"]""",
    ),
    _env("testing.Testing()", GO, r"\btesting\.Testing\s*\(\s*\)"),
    _env("flag.Lookup(test.v)", GO, r"\bflag\.Lookup\b", r"""flag\.Lookup\(\s*"test\."""),
    _env("cfg!(test)", RUST, r"\bcfg!\s*\(\s*test\s*\)"),
    _env("Rails.env.test?", RUBY, r"\bRails\.env\.test\?"),
    _env("ENV test check", RUBY, r"\bENV\s*(?:\[|\.fetch\b)", r"""[!=]=\s*['"]test['"]"""),
    _env("runningUnitTests()", PHP, r"->runningUnitTests\s*\("),
    _env(
        "environment('testing')",
        PHP,
        r"\b(?:environment|getenv)\s*\(",
        r"""environment\(\s*['"]test(?:ing)?['"]|[!=]==?\s*['"]test(?:ing)?['"]""",
    ),
    _env(
        "IsEnvironment(Test)",
        CSHARP,
        r"\bIsEnvironment\s*\(",
        r"""IsEnvironment\(\s*"Test(?:ing)?\"""",
    ),
)

#: Languages whose test checks fire only on a branch line. In JS the
#: comparison itself is the signal (``const isTest = ...``);
#: elsewhere a bare read is usually configuration.
BRANCH_FREE_LANGUAGES = JS
BRANCH_KEYWORDS = re.compile(r"\b(?:if|elif|unless|guard|while|when)\b")

#: A skip under one of these, or under a branch, guards an environment.
SKIP_GUARDS = re.compile(
    BRANCH_KEYWORDS.pattern + r"|&&|\|\||\b(?:and|or|except|catch|rescue|else|case|switch)\b"
)

#: The header of a function or test case: the enclosing-block walk of a skip
#: stops there, since a guard above it guards nothing inside.
BLOCK_HEADER = re.compile(
    r"^\s*(?:(?:async\s+)?def|func|fn|function|class)\b"
    r"|(?<![\w.])(?:it|test|specify|describe)\s*\(|^\s*(?:public|private|protected|void)\b"
)

#: A parametrized test: folding cases into one reads as fewer test cases.
PARAMETRIZE = re.compile(
    r"mark\.parametrize\b|\.each\s*[(`]"
    r"|@(?:ParameterizedTest|ValueSource|MethodSource|CsvSource)\b"
    r"|\[\s*(?:Theory|InlineData|TestCase|DataRow)\b|#\[\s*(?:rstest|case)\b|\bt\.Run\s*\("
)

#: A negated check that guards only these calls keeps tests quiet; production
#: takes the same path.
LOG_CALL = re.compile(
    r"^\s*(?:console\.\w+|print|logger\.\w+|log\.\w+|logging\.\w+|warnings\.warn|puts"
    r"|fmt\.Print\w*)\s*\("
)
NEGATED_CHECK = re.compile(r"!==?|\bnot\b|\bunless\b|^\s*if\s*\(?\s*!")

#: A check that only picks an env file is configuration, not a code path.
ENV_FILE_LITERAL = re.compile(r"""['"`]\.env""")


# -- shared -------------------------------------------------------------------

#: Directories of test fixtures, golden inputs and generated code: a tool or
#: a test wrote what is in them, so their markers are not a choice of the
#: change author.
FIXTURE_OR_GENERATED_DIRS = frozenset(
    {
        "fixture", "fixtures", "__fixtures__", "__snapshots__", "testdata",
        "test_cases", "golden", "goldens", "generated", "__generated__",
    }
)

#: A file name naming a golden or snapshot file (``GOLDEN_PARTIAL.js``).
GOLDEN_NAME = re.compile(r"golden|\.snap$", re.IGNORECASE)

#: Names too common to tie a removed test to removed source code.
NOT_REMOVED_NAMES = frozenset(
    {
        "async", "await", "break", "case", "catch", "class", "console", "const", "continue",
        "default", "delete", "else", "elif", "enum", "except", "export", "exports", "extends",
        "false", "False", "finally", "from", "func", "function", "global", "import", "impl",
        "interface", "lambda", "module", "none", "None", "null", "package", "pass", "private",
        "protected", "public", "raise", "require", "return", "self", "static", "string",
        "struct", "super", "switch", "this", "throw", "throws", "true", "True", "type",
        "typeof", "undefined", "void", "while", "with", "yield",
    }
)  # fmt: skip

#: The shortest name that can tie a removed test to removed code.
MIN_REMOVED_NAME_LEN = 4
