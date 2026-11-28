"""C# ``#if`` groups the grammar cannot place, or whose arms do not balance their braces."""

from __future__ import annotations

import pytest

from repowise.core.ingestion.lang_helpers.preproc_arms import blank_csharp_preproc_arms
from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

# Each group balances, but sits where the grammar allows no directive: between
# a property and its initializer, among a switch's sections, and around a
# statement. The method used to end at the switch and a ``lock`` block became
# a method of its own.
MISPLACED = b"""namespace Demo
{
    public class Builder
    {
        public bool Validate { get; set; }
#if DEBUG
            = true;
#endif

        public int Build(int kind)
        {
            int result;
            switch (kind)
            {
#if DESKTOP
                case 1:
                    result = 10;
                    break;
#endif
                default:
                    result = 0;
                    break;
            }
#if DESKTOP
            lock (Sync)
            {
                result += Write(result);
            }
#endif
            return result;
        }

        public int Write(int value)
        {
            return value;
        }
    }
}
"""

UNBALANCED = b"""namespace Demo
{
    public class Loader
    {
        public int Load(int a)
        {
#if FAST
            if (a > 0) {
#elif SAFE
            if (a > 1) {
#else
            if (a > 2) {
#endif
                return 1;
            }
            return 0;
        }

        public int After() { return 1; }
    }
}
"""

NESTED = b"""class C
{
    int F(int a)
    {
#if A
#if B
        if (a) {
#else
        if (!a) {
#endif
#region kept
#else
        while (a) {
#pragma warning disable CS0168
#endif
            return 1;
        }
    }
}
"""

DEAD_ARM_FIRST = b"""class C
{
    void F(int a)
    {
#if false
        if (a) { Old();
#else
        if (a) { Live();
#endif
        }
    }
}
"""

# A directive-shaped line inside a verbatim string, a brace in a raw string
# and a ``//`` comment ending in a backslash must not move the brace count.
LEXICAL = b'''class C
{
    void F(int a)
    {
        var s = @"
#if NOT_A_DIRECTIVE
{";
        var r = """
            {
            """;
        // C:\\path\\
#if A
        if (a) {
#else
        if (!a) {
#endif
        }
    }
}
'''


@pytest.fixture
def parser() -> ASTParser:
    return ASTParser()


def _symbols(parser: ASTParser, source: bytes) -> list[tuple[str, int, int]]:
    result = parser.parse_file(_make_file_info("a.cs", "csharp"), source)
    return [(s.name, s.start_line, s.end_line) for s in result.symbols]


class TestParse:
    def test_directives_the_grammar_cannot_place_keep_the_method_whole(
        self, parser: ASTParser
    ) -> None:
        assert _symbols(parser, MISPLACED) == [
            ("Demo", 1, 38),
            ("Builder", 3, 37),
            ("Validate", 5, 7),
            ("Build", 10, 31),
            ("Write", 33, 36),
        ]

    def test_unbalanced_elif_chain(self, parser: ASTParser) -> None:
        symbols = _symbols(parser, UNBALANCED)
        assert ("Load", 5, 17) in symbols
        assert ("After", 19, 19) in symbols

    def test_lexical_traps_do_not_shift_the_brace_count(self, parser: ASTParser) -> None:
        assert ("F", 3, 18) in _symbols(parser, LEXICAL)


class TestBlanking:
    def test_file_without_if_is_returned_as_is(self) -> None:
        source = b"#region R\nclass C { }\n#endregion\n"
        assert blank_csharp_preproc_arms(source) is source

    def test_balanced_group_loses_only_its_directive_lines(self) -> None:
        out = blank_csharp_preproc_arms(MISPLACED)
        assert b"#" not in out
        assert b"case 1:" in out and b"lock (Sync)" in out

    def test_unbalanced_group_keeps_the_first_arm(self) -> None:
        out = blank_csharp_preproc_arms(UNBALANCED)
        assert b"if (a > 0) {" in out
        assert b"if (a > 1)" not in out and b"if (a > 2)" not in out

    def test_nested_groups_and_other_directives(self) -> None:
        out = blank_csharp_preproc_arms(NESTED)
        assert b"if (a) {" in out
        assert b"if (!a)" not in out and b"while (a)" not in out
        assert b"#if" not in out and b"#else" not in out and b"#endif" not in out
        assert b"#region kept" in out
        assert b"#pragma warning disable CS0168" in out

    def test_if_false_arm_is_not_the_one_kept(self) -> None:
        out = blank_csharp_preproc_arms(DEAD_ARM_FIRST)
        assert b"Live();" in out and b"Old();" not in out

    def test_lone_if_false_arm_goes_even_when_it_balances_or_not(self, parser: ASTParser) -> None:
        source = b"class C\n{\n#if false\n    void Old() { }\n    }\n#endif\n    void M() { }\n}\n"
        out = blank_csharp_preproc_arms(source)
        assert b"Old" not in out
        assert _symbols(parser, source) == [("C", 1, 8), ("M", 7, 7)]

    def test_directive_after_a_byte_order_mark(self) -> None:
        source = b"\xef\xbb\xbf#if A\nusing X;\n#endif\nclass C { }\n"
        out = blank_csharp_preproc_arms(source)
        assert out.startswith(b"\xef\xbb\xbf     \n")

    def test_lexical_traps(self) -> None:
        out = blank_csharp_preproc_arms(LEXICAL)
        assert b"#if NOT_A_DIRECTIVE" in out
        assert b"if (a) {" in out and b"if (!a)" not in out

    @pytest.mark.parametrize("source", [MISPLACED, UNBALANCED, NESTED, DEAD_ARM_FIRST, LEXICAL])
    def test_offsets_and_line_breaks_are_preserved(self, source: bytes) -> None:
        for text in (source, source.replace(b"\n", b"\r\n")):
            out = blank_csharp_preproc_arms(text)
            assert len(out) == len(text)
            assert [i for i, b in enumerate(out) if b in b"\r\n"] == [
                i for i, b in enumerate(text) if b in b"\r\n"
            ]

    def test_is_idempotent(self) -> None:
        once = blank_csharp_preproc_arms(NESTED)
        assert blank_csharp_preproc_arms(once) is once
