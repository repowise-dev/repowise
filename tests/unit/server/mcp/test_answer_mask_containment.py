"""Masking containment, and the defect it left open.

Both are found by walking every file in a multi-language corpus rather than
sampling cut points (`local-stash/get-answer-dogfood/overmask_gate.py`), which
is the instrument the random-cut sweep never was:

* **D9** -- a cut inside a multi-line string must keep the symbol enclosing it,
  and a cut inside a block COMMENT must not, because a comment can sit between
  two declarations while a string body cannot.
* **the EOF containment** -- a triple-quote or ``/*`` run still open at end of
  file is a walk that lost track, so it must under-mask rather than hide every
  definition below it. Both fire on a delimiter belonging to another language
  sitting inside a string this walk cannot see.

D8 is not here. Its owner turned out to be `check_symbol_bounds`, which already
clamps to the live file on its other two returns, so it is covered in
`test_verify_bounds.py`.
"""

from pathlib import Path

from repowise.server.mcp_server.tool_answer.symbols import (
    _string_masked_lines,
    withheld_definitions,
)

# A Go test whose body is cut inside a raw string. The line below the string is
# a top-level `func` at column 0, which is what the anchor used to read.
_GO_CUT_IN_A_RAW_STRING = """\
package main

func TestThing(t *testing.T) {
\trun(`
\tquery {
\t\trepository(owner: $owner) {
\t\t\tname
\t\t}
\t}
\t`)
}

func AfterIt() {
}
"""

# A block comment BETWEEN two top-level functions, indented deeper than the code
# around it. `first` has ended by the time the comment starts, so nothing may be
# reported as continuing.
#
# The indent is what makes this discriminating rather than decorative. The
# comment's opening line is not masked and pins the backward walk's running
# minimum indent, so a comment at column 0 stops the walk on its own whatever
# the anchor says. Here the `}` above is skipped as a bracket tail without
# updating that minimum, so the walk runs on and reaches `first`.
_JS_CUT_IN_A_BLOCK_COMMENT = """\
export function first() {
  return 1;
}
    /* prose about what comes next
       more prose
       and more
    */
export function second() {
  return 2;
}
"""

# `${0%/*}` inside a Rust raw string. `/*` is not a comment here, and nothing
# closes it, so the parent masked to EOF and lost every `fn` below.
_RUST_SLASH_STAR_IN_A_RAW_STRING = '''\
fn shim() {
    let script = r#"#!/bin/sh
record_dir=${0%/*}
printf '%s' "$@"
"#;
    write(script);
}

fn after_the_raw_string() {
    ok()
}
'''

# A triple quote is a Python/Kotlin/Java delimiter, not a Rust one. Same shape,
# other delimiter: nothing closes it, so the parent masked to EOF. Verbatim from
# `gleam/compiler-core/src/error.rs`, where it cost 3,002 lines.
_RUST_TRIPLE_QUOTE_IN_A_STRING = '''\
fn describe() {
    let text = r#"
description = """#
"#;
    emit(text);
}

fn after_the_triple_quote() {
    ok()
}
'''


def _write(tmp_path: Path, name: str, body: str) -> Path:
    (tmp_path / name).write_text(body, encoding="utf-8")
    return tmp_path


def test_a_cut_inside_a_multi_line_string_keeps_the_enclosing_symbol(tmp_path):
    """FAILS on c3fdccbd with `[]` -- D9, 8 measured instances on cli/cli + mui.

    The cut is inside the raw string, so every line of it is masked and the
    first usable withheld line is `func AfterIt` at column 0. The anchor read 0
    and the backward walk exited on its first iteration.
    """
    root = _write(tmp_path, "thing_test.go", _GO_CUT_IN_A_RAW_STRING)

    got = withheld_definitions(root, "thing_test.go:5-14")

    head = next((d for d in got if d.get("body_continues")), None)
    assert head is not None, got
    assert head["name"] == "TestThing", got


def test_a_cut_inside_a_block_comment_does_not_resurrect_the_symbol_above_it(tmp_path):
    """The control for the fix above, and the reason it is not one line.

    Verified to be discriminating rather than assumed: swap the anchor test to
    `lo in mask.all` and this returns `first` with `body_continues`, four lines
    after `first` ended. That is the whole argument for splitting the mask by
    what is hiding the line.
    """
    root = _write(tmp_path, "pair.js", _JS_CUT_IN_A_BLOCK_COMMENT)

    got = withheld_definitions(root, "pair.js:5-11")

    assert not any(d.get("body_continues") for d in got), got


def test_an_unterminated_block_comment_does_not_mask_to_end_of_file():
    """26 real Rust definitions lost across the corpus before this.

    Measured on `goose`: `${0%/*}` in a raw string masked 103 lines and took
    six real `fn` with it. This does not "fail on the parent" in a useful way --
    the parent's masker returns a bare frozenset, so it raises AttributeError
    rather than the defect. The non-vacuity proof is `ablate_masking.py`'s
    `eof-containment-block` row, which disables the branch on THIS build.
    """
    lines = tuple(_RUST_SLASH_STAR_IN_A_RAW_STRING.splitlines())

    mask = _string_masked_lines(lines, False)

    target = next(i for i, ln in enumerate(lines, 1) if "after_the_raw_string" in ln)
    assert target not in mask.all, sorted(mask.all)


def test_an_unterminated_triple_quote_run_does_not_mask_to_end_of_file():
    """The same defect one delimiter over.

    Measured on `gleam`: a triple quote inside a Rust string masked 3,002 lines
    and took ten real definitions with it. Non-vacuity is `ablate_masking.py`'s
    `eof-containment-delim` row, for the reason above.
    """
    lines = tuple(_RUST_TRIPLE_QUOTE_IN_A_STRING.splitlines())

    mask = _string_masked_lines(lines, False)

    target = next(i for i, ln in enumerate(lines, 1) if "after_the_triple_quote" in ln)
    assert target not in mask.all, sorted(mask.all)


def test_the_masker_reports_strings_and_comments_separately():
    """The split D9's fix needs, from ONE walk on a documented hot path.

    Both directions, because a split that files everything under one bucket
    still satisfies a one-sided assertion.
    """
    comment_mask = _string_masked_lines(tuple(_JS_CUT_IN_A_BLOCK_COMMENT.splitlines()))
    assert comment_mask.comments and not comment_mask.strings, comment_mask

    string_mask = _string_masked_lines(tuple(_GO_CUT_IN_A_RAW_STRING.splitlines()))
    assert string_mask.strings and not string_mask.comments, string_mask

    for mask in (comment_mask, string_mask):
        assert mask.all == mask.strings | mask.comments
