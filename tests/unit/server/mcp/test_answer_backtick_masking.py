"""D2: backtick strings were read as code, so their contents became symbols.

``_string_masked_lines`` tracked Python triple quotes and ``/* */`` but had no
backtick branch, and backtick was not even in ``_QUOTEISH_RE`` -- so a line whose
only quote-ish character was a backtick skipped the character walk entirely.
Go raw strings and TS template literals were therefore scanned as source, and
the GraphQL and interpolated text inside them was emitted as ``withheld_symbols``
entries pointing at ids that resolve to nothing. Measured on cli/cli: 282 of
22,898 sweep entries, 19 of them the HEADLINE the note tells the agent to fetch.

Proof direction:
  test_graphql_in_a_go_raw_string_is_not_a_symbol       -- FAILS at the parent.
  test_a_template_literal_is_not_read_as_code           -- FAILS at the parent.
  test_a_go_function_after_a_raw_string_is_still_found  -- passes both (control).

The last two are GUARDS on the hazard this fix introduces rather than
before/after proofs -- they pass at the parent for the trivial reason that the
parent masks no backticks at all. They are here because over-masking is the
failure this change could cause and it is invisible in a passing corpus run: a
file whose template literals do not balance would be masked to EOF and every
definition in it silently suppressed. Measured on mui, a flat open/close counter
did exactly that to a real file (90 emitted entries down to 0).
"""

from __future__ import annotations

import pytest


def test_graphql_in_a_go_raw_string_is_not_a_symbol(tmp_path) -> None:
    """The cli/cli shape: GraphQL embedded in a Go backtick raw string.

    ``repository(owner: $owner, name: $name) {`` matched the brace-member
    pattern and was emitted as ``name='repository'``, ``kind='member'``,
    ``symbol_id='<path>::repository'`` -- 51 times in the sweep, and the single
    most common fabricated name.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "queries.go").write_text(
        "package api\n"
        "\n"
        "var repoQuery = `\n"
        "\tquery RepositoryInfo($owner: String!, $name: String!) {\n"
        "\t\trepository(owner: $owner, name: $name) {\n"
        "\t\t\tname\n"
        "\t\t\towner { login }\n"
        "\t\t}\n"
        "\t}\n"
        "`\n"
        "\n"
        "func FetchRepo(client *Client) error {\n"
        "\treturn client.Do(repoQuery)\n"
        "}\n",
        encoding="utf-8",
    )

    got = withheld_definitions(tmp_path, "queries.go:3-14")
    names = [d["name"] for d in got]
    assert "repository" not in names, got
    assert "owner" not in names, got
    # The real definition below the raw string must still be reported.
    assert "FetchRepo" in names, got


def test_a_template_literal_is_not_read_as_code(tmp_path) -> None:
    """The mui shape: a definition-looking line inside a TS template literal."""
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "gen.ts").write_text(
        "const header = 'x';\n"
        "\n"
        "export const template = `\n"
        "  function GeneratedThing() {\n"
        "    return null;\n"
        "  }\n"
        "`;\n"
        "\n"
        "export function realExport(a: number) {\n"
        "  return a + 1;\n"
        "}\n",
        encoding="utf-8",
    )

    got = withheld_definitions(tmp_path, "gen.ts:3-11")
    names = [d["name"] for d in got]
    assert "GeneratedThing" not in names, got
    assert "realExport" in names, got


# The mui shape, minimised. Reproduces the real mechanism: a nested template
# literal inside `${...}` whose body holds a CSS colour. The trailing
# `const tick` re-balances the stack so the containment fallback cannot rescue
# it -- without that the guard proves nothing.
_NESTED_WITH_A_CSS_COLOUR = (
    "const Root = styled('div')(\n"
    "  ({ ownerState }) => `\n"
    "    color: red;\n"
    "    ${ownerState.disabled\n"
    "      ? `left: 14px;\n"
    "         background-color: #fff;`\n"
    "      : ''}\n"
    "  `,\n"
    ");\n"
    "\n"
    "export function afterTheTemplate(x: number) {\n"
    "  return x;\n"
    "}\n"
    "\n"
    "const tick = '`';\n"
    "\n"
    "export function afterTheTick(y: number) {\n"
    "  return y;\n"
    "}\n"
)


def test_a_nested_template_literal_does_not_mask_to_eof(tmp_path) -> None:
    """GUARD on the over-mask hazard, not a before/after proof.

    The mechanism, traced on the real file rather than guessed at. A flat
    open/close counter reads the NESTED literal's opening backtick as CLOSING
    the outer one, which puts the walk at code level inside what is really
    string content. A code-level rule then eats the rest of the line -- here the
    ``#`` of a CSS colour, which this lexer-lite treats as a comment start -- and
    with it the real closing backtick. The outer literal never closes and the
    mask runs to EOF.

    So nesting alone is NOT enough; four synthetic nested fixtures all
    re-balanced under a flat counter. It takes nesting plus a ``#`` or ``//``
    inside the nested body, which is exactly what
    ``docs/data/base/getting-started/customization/DisabledDefaultClasses.tsx``
    in mui has (``background-color: #fff``). There the flat walk takes the file
    from 90 emitted entries to 0 and its recall miss from 58/77 cuts to 77/77.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "styled.tsx").write_text(_NESTED_WITH_A_CSS_COLOUR, encoding="utf-8")

    names = [d["name"] for d in withheld_definitions(tmp_path, "styled.tsx:3-17")]
    assert "afterTheTemplate" in names, names
    assert "afterTheTick" in names, names


def test_the_nested_guard_is_not_vacuous(tmp_path) -> None:
    """Runs the rejected design against the fixture above, so the guard bites.

    The first version of this pair was vacuous: an adversarial review showed the
    fixture re-balanced under a flat counter, so the guard passed under the very
    design its docstring claimed to reject. The flat walk is therefore executed
    here rather than described -- same structure as the shipped one, minus the
    interpolation stack.
    """
    from repowise.server.mcp_server.tool_answer.symbols import (
        _skip_quoted,
        _string_masked_lines,
    )

    lines = tuple(_NESTED_WITH_A_CSS_COLOUR.splitlines())

    def _flat(src):
        masked, open_ = set(), False
        for n, raw in enumerate(src, 1):
            if open_:
                masked.add(n)
            i = 0
            while i < len(raw):
                if open_:
                    if raw[i] == "`":
                        open_ = False
                    i += 1
                    continue
                if raw[i] == "`":
                    open_, i = True, i + 1
                    continue
                if raw[i] == "#" or raw.startswith("//", i):
                    break
                if raw[i] in ('"', "'"):
                    i = _skip_quoted(raw, i)
                    continue
                i += 1
        return masked, open_

    target = 11  # the `export function afterTheTemplate` line
    flat_masked, _flat_open = _flat(lines)
    assert target in flat_masked, sorted(flat_masked)
    assert target not in _string_masked_lines(lines), sorted(_string_masked_lines(lines))


def test_a_backtick_inside_a_regex_literal_does_not_open_a_template(tmp_path) -> None:
    """FAILS without regex handling, and the containment CANNOT save it.

    ``/^\\s*[~`]{3}/m`` is real code in angular. The backtick in the character
    class opens a phantom template literal; a later stray backtick closes it
    again, so the walk ends with a BALANCED stack and the unterminated-at-EOF
    fallback never fires. That is the whole danger of this class -- an
    unbalanced file is rescued, a re-balanced one is not. Measured on
    ``vscode-ng-language-service/server/src/text_render.ts``: 212 of 295 lines
    masked, hiding 6 real top-level functions. Found by an adversarial review.

    The trailing ``const tick`` line is load-bearing: without it the fixture
    ends unbalanced, the fallback rescues it, and the test proves nothing.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "render.ts").write_text(
        "export function isFenced(text: string): boolean {\n"
        "  if (/^\\s*[~`]{3}/m.test(text)) {\n"
        "    return true;\n"
        "  }\n"
        "  return false;\n"
        "}\n"
        "\n"
        "export function hiddenByTheRegex(x: number) {\n"
        "  return x;\n"
        "}\n"
        "\n"
        "const tick = '`';\n"
        "\n"
        "export function afterTheTick(y: number) {\n"
        "  return y;\n"
        "}\n",
        encoding="utf-8",
    )

    names = [d["name"] for d in withheld_definitions(tmp_path, "render.ts:2-16")]
    assert "hiddenByTheRegex" in names, names
    assert "afterTheTick" in names, names


def test_an_escaped_backtick_does_not_close_the_literal(tmp_path) -> None:
    """FAILS without escape handling, and the containment cannot save it either.

    ``\\`` inside a template literal escapes the backtick. Closing on it leaves
    an odd count, and a later stray backtick re-balances the stack, so this is
    the same invisible-to-the-fallback class as the regex case above.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "esc.ts").write_text(
        "const a = `x\\`y`;\n"
        "\n"
        "export function hiddenByTheEscape(x: number) {\n"
        "  return x;\n"
        "}\n"
        "\n"
        "const tick = '`';\n"
        "\n"
        "export function afterTheTick(y: number) {\n"
        "  return y;\n"
        "}\n",
        encoding="utf-8",
    )

    names = [d["name"] for d in withheld_definitions(tmp_path, "esc.ts:2-11")]
    assert "hiddenByTheEscape" in names, names
    assert "afterTheTick" in names, names


def test_a_language_without_backtick_strings_is_untouched(tmp_path) -> None:
    """FAILS without the language gate, and this was a live corpus regression.

    A backtick only opens a string in Go and the JS/TS family. Everywhere else
    it is punctuation -- Rust doc comments, Ruby heredocs and Python docstrings
    all carry markdown fences -- so masking on it can only misfire. Measured
    before the gate: ``goose/crates/goose-cli/src/session/export.rs`` lost FIVE
    real ``pub fn`` definitions, because a markdown fence inside an ordinary
    Rust string opened a phantom frame that a stray backtick 260 lines later
    re-closed, leaving the walk balanced and the containment inert. Three more
    corpus files did the same.

    Verified as an invariant, not just here: across 39,070 backtick-bearing
    files in non-backtick languages, 0 masks differ from the parent's.

    A Rust DOC-COMMENT fence would not do -- ``///`` breaks the walk before it
    reaches the backticks, so the fixture would be vacuous. The real shape is a
    multi-line Rust string literal: the quote skip runs off the end of its line,
    the next line is walked as code, and its ``` opens the phantom frame.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    body = (
        "pub fn render() -> String {\n"
        '    let s = "a long message that continues\n'
        "``` fenced block inside the string\n"
        'more text";\n'
        "    s\n"
        "}\n"
        "\n"
        "pub fn after_the_string() -> u32 {\n"
        "    1\n"
        "}\n"
        "\n"
        'const TICK: &str = "`";\n'
        "\n"
        "pub fn after_the_tick() -> u32 {\n"
        "    2\n"
        "}\n"
    )
    (tmp_path / "export.rs").write_text(body, encoding="utf-8")

    names = [d["name"] for d in withheld_definitions(tmp_path, "export.rs:2-16")]
    assert "after_the_string" in names, names
    assert "after_the_tick" in names, names


def test_a_regex_after_return_is_not_read_as_division(tmp_path) -> None:
    """The preceder test cannot be punctuation-only.

    ``if (/re/...)`` is covered because ``(`` precedes it, but
    ``return /[`]/.test(x)`` ends in an alphanumeric and would read as a
    division, re-opening the phantom-frame hole for the most common way to
    write a regex.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "r.ts").write_text(
        "export function isTick(x: string) {\n"
        "  return /[`]/.test(x);\n"
        "}\n"
        "\n"
        "export function hiddenAfterReturn(y: number) {\n"
        "  return y;\n"
        "}\n"
        "\n"
        "const tick = '`';\n"
        "\n"
        "export function afterTheTick(z: number) {\n"
        "  return z;\n"
        "}\n",
        encoding="utf-8",
    )

    names = [d["name"] for d in withheld_definitions(tmp_path, "r.ts:2-13")]
    assert "hiddenAfterReturn" in names, names
    assert "afterTheTick" in names, names


def test_a_regex_inside_an_interpolation_is_skipped(tmp_path) -> None:
    """The ``${...}`` branch runs code, so it needs the regex probe too.

    Without it a backtick in a regex inside an interpolation opens exactly the
    phantom frame ``_skip_regex`` exists to prevent, and a later ordinary
    closing brace can leave the stack balanced. No corpus instance, so this is a
    guard on a latent hole rather than a measured regression.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "i.ts").write_text(
        "export function fence(s: string) {\n"
        '  return `x ${ s.replace(/[`]/g, "") } y`;\n'
        "}\n"
        "\n"
        "export function hiddenAfterInterp(y: number) {\n"
        "  return y;\n"
        "}\n"
        "\n"
        "const tick = '`';\n"
        "\n"
        "export function afterTheTick(z: number) {\n"
        "  return z;\n"
        "}\n",
        encoding="utf-8",
    )

    names = [d["name"] for d in withheld_definitions(tmp_path, "i.ts:2-13")]
    assert "hiddenAfterInterp" in names, names
    assert "afterTheTick" in names, names


@pytest.mark.parametrize(
    "src,expect_end",
    [
        ("/[/]/", 5),      # a slash inside a character class does not close it
        ("/a\\/b/", 6),    # nor does an escaped slash
        ("/x[y/", 0),      # unterminated class: not a regex, index unchanged
        ("/abc", 0),       # no closing slash on the line: not a regex
        ("/a/g", 3),       # ordinary case, flags are not consumed
    ],
)
def test_skip_regex_boundaries(src, expect_end) -> None:
    """``_skip_regex``'s class and escape handling, which nothing else pins.

    Round two flagged both as correctness-critical to the newest code and
    completely untested.
    """
    from repowise.server.mcp_server.tool_answer.symbols import _skip_regex

    assert _skip_regex(src, 0) == expect_end, src


def test_go_division_is_not_mistaken_for_a_regex(tmp_path) -> None:
    """Control on the regex branch, which is the riskiest thing here.

    The masker is language-agnostic, so the regex test runs over Go and Python
    too, where ``/`` is only ever division. It keys on the preceding non-space
    character, and every way a division's left operand can end -- an identifier,
    a digit, ``)``, ``]`` -- is excluded.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "calc.go").write_text(
        "package main\n"
        "\n"
        "func Ratio(a int, b int) int {\n"
        "\tmid := (a + b) / 2\n"
        "\tarr := []int{1, 2}\n"
        "\tn := arr[0] / mid\n"
        "\treturn n / a\n"
        "}\n"
        "\n"
        "func AfterDivision() int { return 1 }\n",
        encoding="utf-8",
    )

    names = [d["name"] for d in withheld_definitions(tmp_path, "calc.go:3-10")]
    assert "Ratio" in names and "AfterDivision" in names, names


def test_an_unbalanced_backtick_does_not_silence_the_rest_of_the_file(tmp_path) -> None:
    """GUARD: when the lexer-lite loses track it must under-mask, not over-mask.

    A template literal that opens and never closes would otherwise mask every
    remaining line, suppressing real definitions with no error and no signal.
    The walk detects that it ended inside a literal and falls back to the
    pre-backtick behaviour for the file. Not hypothetical: across ~121,000 files
    in the local corpora, 244 end the walk still inside a backtick string.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "odd.go").write_text(
        "package main\n"
        "\n"
        "var broken = `this literal never closes\n"
        "\n"
        "func StillFound(x int) int {\n"
        "\treturn x\n"
        "}\n",
        encoding="utf-8",
    )

    names = [d["name"] for d in withheld_definitions(tmp_path, "odd.go:3-7")]
    assert "StillFound" in names, names


def test_a_go_function_after_a_raw_string_is_still_found(tmp_path) -> None:
    """Control: masking must not swallow ordinary code around a raw string."""
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "m.go").write_text(
        "package main\n"
        "\n"
        "func Before() {}\n"
        "\n"
        "const usage = `usage: tool [flags]`\n"
        "\n"
        "func After() {}\n",
        encoding="utf-8",
    )

    names = [d["name"] for d in withheld_definitions(tmp_path, "m.go:2-7")]
    assert "Before" in names and "After" in names, names


def test_a_python_docstring_example_is_still_masked(tmp_path) -> None:
    """Control: the case the masker was built for must not regress.

    The Python corpus is the control for this change -- measured byte-identical
    across 44,331 sweep entries before and after -- and this is its unit-level
    counterpart.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "d.py").write_text(
        "import os\n"
        "\n"
        "def outer():\n"
        '    """Docs.\n'
        "\n"
        "    Example::\n"
        "\n"
        "        def not_a_real_symbol():\n"
        "            pass\n"
        '    """\n'
        "    return 1\n"
        "\n"
        "def real_one():\n"
        "    return 2\n",
        encoding="utf-8",
    )

    names = [d["name"] for d in withheld_definitions(tmp_path, "d.py:4-14")]
    assert "not_a_real_symbol" not in names, names
    assert "real_one" in names, names


def test_the_fallback_costs_at_most_one_extra_walk(tmp_path) -> None:
    """Bounds the worst case rather than asserting the cache exists.

    An earlier version of this test claimed the fallback "must not defeat the
    cache", which it structurally cannot -- the fallback runs INSIDE the cached
    function, so the assertion could not fail. What is actually worth pinning is
    that the fallback re-walks once and only once, since the fast-path skip is
    disabled while a template frame is open and a pathological file therefore
    pays a full per-character walk twice.
    """
    from repowise.server.mcp_server.tool_answer import symbols as mod

    calls = []
    real = mod._walk_string_state

    def _counting(lines, *, backticks):
        calls.append(backticks)
        return real(lines, backticks=backticks)

    mod._walk_string_state = _counting
    try:
        mod._string_masked_lines.cache_clear()
        mod._string_masked_lines(("const x = `unclosed", "def real():", "    return 1"))
        assert calls == [True, False], calls
        calls.clear()
        mod._string_masked_lines.cache_clear()
        mod._string_masked_lines(("def real():", "    return 1"))
        assert calls == [True], calls
    finally:
        mod._walk_string_state = real
