"""C/C++ ``#if`` groups whose arms open braces they do not close (issue #4850)."""

from __future__ import annotations

import pytest

from repowise.core.ingestion.lang_helpers.preproc_arms import blank_unbalanced_preproc_arms
from repowise.core.ingestion.lang_helpers.source_prep import prepare_cpp_source
from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

UNBALANCED = b"""int f(int a) {
#ifdef X
  if (a) {
    return 1;
#else
  if (a) {
    return 0;
#endif
  }
  return 2;
}
"""

BALANCED = b"""int f(int a) {
#ifdef X
  if (a) {
    return 1;
  }
#else
  if (a) {
    return 0;
  }
#endif
  return 2;
}
"""

# ninja's ``ReadFile``: the unbalanced group sits inside an outer ``#ifdef``
# whose own arms balance once the inner group is settled.
NESTED = b"""int ReadFile(const string& path, string* contents, string* err) {
#ifdef _WIN32
  for (;;) {
    if (done) {
      break;
    }
  }
  return 0;
#else
  FILE* f = fopen(path.c_str(), "rb");
#ifdef __USE_LARGEFILE64
  struct stat64 st;
  if (fstat64(fileno(f), &st) < 0) {
#else
  struct stat st;
  if (fstat(fileno(f), &st) < 0) {
#endif
    err->assign(strerror(errno));
    return -errno;
  }
  return 0;
#endif
}

int after() { return 1; }
"""

# The opener and the closer are each split across the same condition.
CROSS_GROUP = b"""void g(int x, int y) {
#ifdef A
  while (x) {
#else
  while (y) {
#endif
    step();
#ifdef A
  }
#else
  }
#endif
}
"""

DEAD_ARM_FIRST = b"""int h(int a) {
#if 0
  if (a) {
    old();
#else
  if (a) {
    live();
#endif
  }
  return 1;
}
"""

ELIF_CHAIN = b"""int k(int a) {
#if defined(A)
  if (a) {
#elif defined(B)
  if (a > 1) {
#else
  if (a > 2) {
#endif
    return 1;
  }
  return 0;
}
"""

SIGNATURE_PER_PLATFORM = b"""#ifdef _WIN32
int open_it(const char* path) {
#else
int open_it(const char* path, int flags) {
#endif
  return 0;
}

int next_one() { return 1; }
"""


def _symbols(parser: ASTParser, source: bytes, path: str, lang: str) -> list[tuple[str, int, int]]:
    result = parser.parse_file(_make_file_info(path, lang), source)
    return [(s.name, s.start_line, s.end_line) for s in result.symbols]


LANGS = [pytest.param("a.cpp", "cpp", id="cpp"), pytest.param("a.c", "c", id="c")]


class TestUnbalancedGroupsParse:
    @pytest.mark.parametrize(("path", "lang"), LANGS)
    def test_issue_repro_yields_one_function_over_its_full_span(
        self, parser: ASTParser, path: str, lang: str
    ) -> None:
        assert _symbols(parser, UNBALANCED, path, lang) == [("f", 1, 11)]

    @pytest.mark.parametrize(("path", "lang"), LANGS)
    def test_balanced_control_is_unchanged(self, parser: ASTParser, path: str, lang: str) -> None:
        assert blank_unbalanced_preproc_arms(BALANCED) is BALANCED
        assert _symbols(parser, BALANCED, path, lang) == [("f", 1, 12)]

    @pytest.mark.parametrize(("path", "lang"), LANGS)
    def test_nested_group_keeps_the_function_and_what_follows(
        self, parser: ASTParser, path: str, lang: str
    ) -> None:
        symbols = _symbols(parser, NESTED, path, lang)
        assert ("ReadFile", 1, 23) in symbols
        assert ("after", 25, 25) in symbols

    @pytest.mark.parametrize(("path", "lang"), LANGS)
    def test_condition_split_across_two_groups(
        self, parser: ASTParser, path: str, lang: str
    ) -> None:
        assert _symbols(parser, CROSS_GROUP, path, lang) == [("g", 1, 13)]

    @pytest.mark.parametrize(("path", "lang"), LANGS)
    def test_dead_if_zero_arm_loses_to_the_live_one(
        self, parser: ASTParser, path: str, lang: str
    ) -> None:
        assert _symbols(parser, DEAD_ARM_FIRST, path, lang) == [("h", 1, 11)]

    @pytest.mark.parametrize(("path", "lang"), LANGS)
    def test_elif_chain(self, parser: ASTParser, path: str, lang: str) -> None:
        assert _symbols(parser, ELIF_CHAIN, path, lang) == [("k", 1, 12)]

    @pytest.mark.parametrize(("path", "lang"), LANGS)
    def test_signature_that_differs_per_platform(
        self, parser: ASTParser, path: str, lang: str
    ) -> None:
        symbols = _symbols(parser, SIGNATURE_PER_PLATFORM, path, lang)
        assert ("open_it", 2, 7) in symbols
        assert any(name == "next_one" for name, _, _ in symbols)


class TestBlanking:
    def test_keeps_the_first_arm_and_removes_the_directive_lines(self) -> None:
        out = blank_unbalanced_preproc_arms(UNBALANCED)
        lines = out.split(b"\n")
        assert lines[0] == b"int f(int a) {"
        assert lines[2] == b"  if (a) {"
        assert lines[3] == b"    return 1;"
        assert b"#" not in out
        assert lines[5].strip() == b"" and lines[6].strip() == b""
        assert lines[8] == b"  }"

    @pytest.mark.parametrize(
        "source", [UNBALANCED, NESTED, CROSS_GROUP, DEAD_ARM_FIRST, ELIF_CHAIN, b"#if 0\n{\n#endif"]
    )
    def test_length_and_line_breaks_are_preserved(self, source: bytes) -> None:
        out = blank_unbalanced_preproc_arms(source)
        assert len(out) == len(source)
        assert out.count(b"\n") == source.count(b"\n")
        assert [i for i, b in enumerate(out) if b == 0x0A] == [
            i for i, b in enumerate(source) if b == 0x0A
        ]

    def test_crlf_line_endings_survive(self) -> None:
        source = UNBALANCED.replace(b"\n", b"\r\n")
        out = blank_unbalanced_preproc_arms(source)
        assert len(out) == len(source)
        assert out.count(b"\r\n") == source.count(b"\r\n")

    def test_is_idempotent(self) -> None:
        once = blank_unbalanced_preproc_arms(NESTED)
        assert blank_unbalanced_preproc_arms(once) is once

    def test_dead_if_zero_arm_is_not_the_one_kept(self) -> None:
        out = blank_unbalanced_preproc_arms(DEAD_ARM_FIRST)
        assert b"live();" in out
        assert b"old();" not in out

    def test_include_and_define_lines_in_the_dropped_arm_survive(self) -> None:
        source = (
            b"int f(int a) {\n"
            b"#ifdef X\n"
            b"  if (a) {\n"
            b"#else\n"
            b"#include <dropped.h>\n"
            b"#define DROPPED 1\n"
            b"  if (a) {\n"
            b"#endif\n"
            b"  }\n"
            b"}\n"
        )
        out = blank_unbalanced_preproc_arms(source)
        assert b"#include <dropped.h>" in out
        assert b"#define DROPPED 1" in out
        assert b"#else" not in out

    def test_prepare_cpp_source_applies_it(self) -> None:
        assert prepare_cpp_source(UNBALANCED) == blank_unbalanced_preproc_arms(UNBALANCED)


class TestLeftAlone:
    @pytest.mark.parametrize(
        "source",
        [
            pytest.param(b"int a;\n", id="no-directives"),
            pytest.param(
                b"#ifndef H\n#define H\n#ifdef __cplusplus\nextern \"C\" {\n#endif\n"
                b"int f(void);\n#ifdef __cplusplus\n}\n#endif\n#endif\n",
                id="lone-extern-c-wrapper",
            ),
            pytest.param(
                b"void f() {\n#ifdef X\n  if (a) {\n#endif\n  g();\n#ifdef X\n  }\n#endif\n}\n",
                id="lone-groups-closed-by-a-later-lone-group",
            ),
            pytest.param(
                b"#if 0\nvoid old() {\n#endif\nvoid live() {}\n", id="lone-dead-if-zero-block"
            ),
            pytest.param(
                b"#if 0\nvoid old() { x(); }\n#else\nvoid live() { y(); }\n#endif\n",
                id="balanced-if-zero-else",
            ),
            pytest.param(
                b"void f() {\n#if A\n  x();\n#elif B\n  y();\n#else\n  z();\n#endif\n}\n",
                id="balanced-elif-chain",
            ),
            pytest.param(
                b'#ifdef A\nconst char* s = "{";\n#else\nconst char* s = "}}";\n#endif\n',
                id="braces-in-strings",
            ),
            pytest.param(
                b"#ifdef A\nint c = '{';  // {{ \n#else\nint c = '}';  /* } } */\n#endif\n",
                id="braces-in-char-literals-and-comments",
            ),
            pytest.param(
                b"#ifdef A\n#define OPEN {\nint a;\n#else\n#define OPEN }}\nint a;\n#endif\n",
                id="braces-in-macro-definitions",
            ),
            pytest.param(
                b'#ifdef A\nconst char* s = R"x({ { { )x";\n#else\nint s;\n#endif\n',
                id="braces-in-raw-string",
            ),
            pytest.param(
                b"#ifdef A\nint big = 1'000'000;\n#else\nint big = 2'000'000;\n#endif\n",
                id="digit-separators",
            ),
            pytest.param(
                b"/*\n#ifdef A\n{\n#else\n{\n#endif\n*/\nint a;\n",
                id="directives-inside-a-block-comment",
            ),
            pytest.param(
                b"#ifdef A\nint a;\n#else\n#endif\n#endif\n#else\nint b;\n",
                id="stray-else-and-endif",
            ),
        ],
    )
    def test_source_is_returned_untouched(self, source: bytes) -> None:
        assert blank_unbalanced_preproc_arms(source) is source

    def test_unterminated_group_is_returned_untouched(self) -> None:
        source = b"void f() {\n#ifdef A\n  if (a) {\n#else\n  if (b) {\n  }\n"
        assert blank_unbalanced_preproc_arms(source) is source
