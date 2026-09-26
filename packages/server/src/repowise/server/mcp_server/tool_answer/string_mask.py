"""Which source lines start inside a string or a comment.

A lexer-lite over whole files, used to keep definition-shaped text inside
docstrings, template literals and block comments out of ``withheld_symbols``.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import NamedTuple

# Anything that could open a string or a comment. A line with none of these
# cannot change the scanner's state, so it skips the character walk.
_QUOTEISH_RE = re.compile(r"""["'`#]|/[*/]""")


def _skip_quoted(raw: str, i: int) -> int:
    """Index just past the single- or double-quoted run starting at ``i``."""
    quote, i = raw[i], i + 1
    while i < len(raw):
        if raw[i] == "\\":
            i += 2
            continue
        if raw[i] == quote:
            return i + 1
        i += 1
    return i


# Extensions where a backtick actually opens a string: Go raw strings and the
# JS/TS template-literal family. Everywhere else a backtick is punctuation --
# Rust doc comments, Ruby heredocs and Python docstrings all carry markdown
# fences and shell quotes -- so masking on it can only ever be a misfire.
#
# Not a style choice, a measured one. Before this gate, a markdown fence inside
# an ordinary Rust string opened a phantom frame that a stray backtick 260 lines
# later re-closed, and `goose/crates/goose-cli/src/session/export.rs` lost FIVE
# real `pub fn` definitions from `withheld_symbols` with the containment below
# provably inert. Three more files in the corpus did the same (two Ruby
# heredocs, one Rust raw string).
_BACKTICK_STRING_SUFFIXES = frozenset(
    {".go", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
)

# Characters after which a ``/`` starts a REGEX rather than a division. Notably
# excludes ``)``, ``]`` and anything alphanumeric, which are the positions where
# a division's left operand ends -- so Go's and Python's ``a / b`` is never
# mistaken for a regex.
_REGEX_CAN_START_AFTER = frozenset("(,=:[!&|?{};+-*%~^<>\n")

# ...and the keywords a regex can follow, which end in an alphanumeric and so
# would otherwise read as a division's left operand. `return /[`]/.test(x)` is
# the one that matters here: it opens a phantom frame exactly like the
# character-class case `_skip_regex` exists for.
_REGEX_CAN_START_AFTER_WORD = frozenset(
    {
        "return", "case", "typeof", "yield", "await", "throw", "in", "of",
        "new", "delete", "instanceof", "do", "else", "void",
    }
)


def _regex_position(raw: str, i: int, prev: str) -> bool:
    """Whether the ``/`` at ``i`` starts a regex rather than a division."""
    if prev in _REGEX_CAN_START_AFTER:
        return True
    if not (prev.isalnum() or prev == "_"):
        return False
    word = ""
    j = i - 1
    while j >= 0 and raw[j].isspace():
        j -= 1
    while j >= 0 and (raw[j].isalnum() or raw[j] == "_"):
        word = raw[j] + word
        j -= 1
    return word in _REGEX_CAN_START_AFTER_WORD


def _skip_regex(raw: str, i: int) -> int:
    """Index past a regex literal starting at ``i``, or ``i`` if it is not one.

    Only exists because a backtick inside a regex otherwise opens a phantom
    template literal. When a later backtick closes that phantom again the walk
    ends with an EMPTY stack, so the containment in ``_string_masked_lines``
    never fires and the mask silently eats everything between.

    Isolated on one real file: ``mui/packages/markdown/parseMarkdown.js:203``,
    ``return matches[1].replace(/`/g, '');``. Without this branch that file
    masks 61 lines instead of 52, stays balanced, and hides the real
    ``function getDescription`` at line 206.

    Regex literals cannot span lines, so a run with no closing ``/`` on the same
    line is not one and is left alone.
    """
    j = i + 1
    in_class = False
    while j < len(raw):
        c = raw[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            return j + 1
        j += 1
    return i


def _walk_string_state(
    lines: tuple[str, ...], *, backticks: bool
) -> tuple[set[int], set[int], bool]:
    """(lines starting inside a string, inside a block comment, literal left open).

    The two sets are kept apart because callers need to tell them apart and this
    is a hot path: a string body proves the enclosing expression is still open,
    a comment between two declarations proves nothing.

    ``stack`` models template-literal nesting: a ``None`` frame is the string
    part of a backtick literal, an ``int`` frame is the unclosed-brace depth
    inside a ``${...}`` interpolation. An interpolation holds CODE, so its lines
    are not masked and a backtick inside one opens its own nested literal rather
    than closing the outer one.

    That nesting is not optional sophistication, and the reason is not the one
    it looks like. Nesting alone does NOT unbalance a flat open/close counter --
    four synthetic nested fixtures all re-balance. What breaks is the
    combination: a flat counter reads the nested literal's OPENING backtick as
    closing the outer one, which puts the walk at code level inside what is
    really string content, and a code-level rule then eats the rest of the line
    along with the real closing backtick. On mui's
    ``DisabledDefaultClasses.tsx`` that rule is ``#`` firing on the CSS colour
    ``#fff``, and the outer literal then never closes.

    The same shape has two other triggers, and those two are worse because they
    leave the stack BALANCED, which the containment in ``_string_masked_lines``
    cannot see: an escaped backtick (handled here) and a backtick inside a regex
    (handled by ``_skip_regex``). The nesting case usually ends UNBALANCED and
    so is caught by the fallback anyway -- interpolation tracking is here for
    precision, measured as 1,926 fabrications against 60 on the 16 corpus files
    where a flat walk and this one disagree.
    """
    strings: set[int] = set()
    comments: set[int] = set()
    delim: str | None = None
    in_block = False
    # Line the currently-open ``delim`` run or ``/* */`` block started on. The
    # two are mutually exclusive (a frame is only ever opened at code level), so
    # one variable serves both.
    open_line = 0
    stack: list[int | None] = []
    for n, raw in enumerate(lines, 1):
        # The three states are mutually exclusive: a frame is only ever pushed
        # at code level, so an if/elif chain is faithful to the walk below.
        if delim is not None or (stack and stack[-1] is None):
            strings.add(n)
        elif in_block:
            comments.add(n)
        elif not stack and not _QUOTEISH_RE.search(raw):
            # Nothing on this line can open a string or comment, so the
            # character walk below cannot change state. Most lines are this
            # line, and skipping them is what keeps a whole-file scan cheap.
            continue
        i = 0
        # Last non-space character seen at CODE level, for the regex test below.
        prev = "\n"
        while i < len(raw):
            if stack:
                if stack[-1] is None:
                    if raw[i] == "\\":
                        # An escaped backtick does not close the literal. Without
                        # this, `` `x\`y` `` closes early and re-opens on the real
                        # terminator, inverting the parity for the rest of the
                        # file with a balanced stack the containment cannot see.
                        i += 2
                    elif raw.startswith("${", i):
                        stack.append(1)
                        i += 2
                    elif raw[i] == "`":
                        stack.pop()
                        i += 1
                    else:
                        i += 1
                    continue
                # Inside ${...}, so the ordinary code tokens apply again --
                # including the regex probe, without which a backtick in a
                # character class here (``${s.replace(/[`]/g, "")}``) opens the
                # same phantom frame _skip_regex exists to prevent.
                if raw[i] == "{":
                    stack[-1] += 1
                elif raw[i] == "}":
                    stack[-1] -= 1
                    if stack[-1] <= 0:
                        stack.pop()
                elif raw[i] == "`":
                    stack.append(None)
                elif raw.startswith("//", i):
                    break
                elif raw[i] == "/" and _regex_position(raw, i, prev):
                    j = _skip_regex(raw, i)
                    if j > i:
                        i, prev = j, "/"
                        continue
                elif raw[i] in ('"', "'"):
                    i = _skip_quoted(raw, i)
                    prev = '"'
                    continue
                if not raw[i].isspace():
                    prev = raw[i]
                i += 1
                continue
            if delim is not None:
                if raw.startswith(delim, i):
                    delim, open_line, i = None, 0, i + len(delim)
                else:
                    i += 1
                continue
            if in_block:
                if raw.startswith("*/", i):
                    in_block, open_line, i = False, 0, i + 2
                else:
                    i += 1
                continue
            if raw.startswith('"""', i) or raw.startswith("'''", i):
                delim, open_line, i = raw[i : i + 3], n, i + 3
                continue
            if backticks and raw[i] == "`":
                stack.append(None)
                i += 1
                continue
            if raw.startswith("/*", i):
                in_block, open_line, i = True, n, i + 2
                continue
            if raw[i] == "#" or raw.startswith("//", i):
                break
            if raw[i] == "/" and _regex_position(raw, i, prev):
                j = _skip_regex(raw, i)
                if j > i:
                    i, prev = j, "/"
                    continue
            if raw[i] in ('"', "'"):
                i = _skip_quoted(raw, i)
                prev = '"'
                continue
            if not raw[i].isspace():
                prev = raw[i]
            i += 1
    # A run still open at EOF is a walk that lost track, not a file with an
    # unterminated construct, and masking to EOF hides every definition below
    # it. The template-literal stack has had this containment since the backtick
    # work; ``delim`` and ``/* */`` never did, and both fire on the same shape --
    # a delimiter belonging to another language, sitting inside a string this
    # walk cannot see. Measured on Rust: ``${0%/*}`` in a raw string masked 103
    # lines and cost 6 real ``fn``; ``description = """#`` masked 3,002 and cost
    # 10. Discarding the trailing run under-masks instead, which costs a
    # spurious name in a list rather than an absent real one.
    if delim is not None:
        strings = {n for n in strings if n < open_line}
    elif in_block:
        comments = {n for n in comments if n < open_line}
    return strings, comments, bool(stack)


def _has_backtick_strings(file_path: str) -> bool:
    """Whether a backtick opens a string in this file's language."""
    dot = file_path.rfind(".")
    return dot != -1 and file_path[dot:].lower() in _BACKTICK_STRING_SUFFIXES


class _Masked(NamedTuple):
    """1-based line numbers, split by what is hiding them.

    ``all`` is precomputed rather than unioned per call: every caller wants it,
    and this is a cached whole-file walk.
    """

    strings: frozenset[int]
    comments: frozenset[int]
    all: frozenset[int]


@lru_cache(maxsize=8)
def _string_masked_lines(lines: tuple[str, ...], backticks: bool = True) -> _Masked:
    """1-based line numbers that START inside a multi-line string or comment.

    Repowise's own docstrings are full of indented ``def``/``class`` examples,
    and without this every one of them becomes a ``symbol_id`` the note tells the
    agent to fetch and that resolves to nothing.

    Deliberately a lexer-lite: it tracks Python triple quotes, backtick template
    literals / Go raw strings, and C-style ``/* */`` blocks, and stops at ``#`` /
    ``//``. Known ceilings, all measured rather than assumed:

    * C# verbatim strings (``@"..."``) and Rust raw strings (``r#"..."#``) are
      not tracked. C# because the exposure is 27 multi-line literals in 4,284
      files; Rust because its 35,484 interior lines yielded 0 fabrications --
      Go raw strings hold GraphQL, which matches the definition patterns, while
      Rust raw strings hold TOML, which does not.
    * Inside a ``${...}`` interpolation, ``/* */`` and ``#`` are not handled, so
      a ``}`` inside a block comment there can close the frame early. Every
      constructed case ended unbalanced and was caught by the fallback below.
    * Markdown fenced blocks and inline code spans are now masked too, which is
      wanted (a ``def`` inside a ```` ``` ```` fence is not a definition) but is
      a behaviour change worth knowing about.

    Cached on the line tuple: this is a per-character Python loop over the whole
    file (68 ms on a 424 KB one), and the homonym-union path calls it once per
    truncated body, re-masking the same file each time. The fallback below can
    double that on a file whose literals do not balance, because the fast-path
    line skip is disabled while a frame is open -- measured at 8x on a
    synthetic 1.2 MB file with one stray backtick on line 1. Bounded at two
    walks, and the cache means it is paid once per file.
    """
    strings, comments, template_left_open = _walk_string_state(
        lines, backticks=backticks
    )
    if template_left_open:
        # The lexer-lite lost track: a template literal opened and never closed,
        # so every line below it is masked to EOF and every definition there is
        # silently suppressed. That failure is invisible -- no error, just
        # missing symbols -- so it must not be the one we ship. Fall back to the
        # pre-backtick walk for this file, which under-masks instead: the cost is
        # a spurious name in a list, not an absent real one.
        strings, comments, _ = _walk_string_state(lines, backticks=False)
    return _Masked(
        frozenset(strings), frozenset(comments), frozenset(strings | comments)
    )
