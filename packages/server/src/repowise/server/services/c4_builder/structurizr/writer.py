"""Block and indentation bookkeeping for the emitted DSL.

The emitted file is read by people in a diff, so it is laid out the way
someone would write it by hand: four-space indents, one blank line between
sibling blocks, no trailing whitespace. Pure string work — knows nothing about
C4 or Structurizr beyond the brace-and-indent shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

_INDENT = " " * 4


class Writer:
    """Accumulates indented lines, with nesting handled by a context manager."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._depth = 0

    def line(self, text: str = "") -> None:
        """Write one line at the current depth. Empty text writes a blank line.

        Blank lines carry no indentation: trailing whitespace shows up in a
        diff and in most editors' warnings, and means nothing here.
        """
        self._lines.append(f"{_INDENT * self._depth}{text}" if text else "")

    def comment(self, text: str = "") -> None:
        """Write a ``#`` comment, or a bare ``#`` for a spacer line.

        Line breaks are folded to spaces first. A comment runs to the end of
        the line, so a newline in the text puts everything after it into the
        file as a bare token and the parser stops there. Much of what reaches
        here is curated prose — a tour title, a repo name — and folding at the
        one place that writes a ``#`` means no caller has to remember.

        Only line breaks are touched. The header's include snippet is indented
        so it can be pasted as written, and collapsing every run of whitespace
        would flatten that too.
        """
        folded = " ".join(str(text).splitlines()).rstrip()
        self.line(f"# {folded}" if folded else "#")

    def blank(self) -> None:
        """One blank line, collapsed if the previous line was already blank."""
        if self._lines and self._lines[-1] == "":
            return
        self.line()

    @contextmanager
    def block(self, header: str) -> Iterator[None]:
        """Open ``header {``, indent the body, close with ``}``."""
        self.line(f"{header} {{")
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            self.line("}")

    def render(self) -> str:
        """The finished document, newline-terminated."""
        body = "\n".join(self._lines).rstrip("\n")
        return f"{body}\n" if body else ""


def quote(text: str) -> str:
    """Quote a value for the DSL.

    Newlines are flattened: a literal newline inside a quoted value is a parse
    error.

    A quote is **escaped**, not replaced. ``\\"`` is the one escape the parser
    honours, and it survives being nested inside a view filter expression
    (``include "element.tag==Layer: Data \\"Access\\""`` both parses and selects
    the element tagged that way), so the tag and the filter that selects it stay
    identical without either of them losing the character.

    A **trailing** backslash is dropped, because it cannot be expressed at all.
    ``\\\\`` is not an escape — backslashes pass through literally — so the last
    one in a value would pair with the closing quote instead, escape it, and run
    the string on into the rest of the file. Interior backslashes are left
    alone; a Windows path stays readable.
    """
    flattened = " ".join(str(text).split())
    # Order matters: escaping quotes introduces backslashes of its own, but only
    # ever in front of a quote, so nothing it adds can end up trailing.
    # The space goes with it: dropping the backslash off "Escape Hatch \" would
    # otherwise leave a trailing one. Structurizr trims both a tag and the
    # filter that selects it, so it would still match — but only by symmetry,
    # and layer_tag's guarantee is that the two strings are byte-identical.
    # Whitespace is already collapsed above, so nothing legitimate is stripped.
    escaped = flattened.replace('"', '\\"').rstrip("\\ ")
    return f'"{escaped}"'
