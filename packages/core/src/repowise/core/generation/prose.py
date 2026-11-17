"""Prose extraction for generated markdown pages.

Several checks care about what a page *says* rather than what it *quotes*: a
loop counter named ``I`` inside a C snippet is not the model addressing the
reader, and a JSON payload is not prose the reader has to wade through.  This
module isolates that distinction in one place so every check draws the line
the same way.

Fenced blocks are tracked line by line rather than matched with a regex so an
unterminated fence — a truncated response — still removes everything after it
instead of leaking the whole tail back into the prose.
"""

from __future__ import annotations

import re

# Single-backtick spans: inline code, symbol names, paths.
_INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")

_FENCE_PREFIXES = ("```", "~~~")

# A token counts as a word only if it carries a letter or a digit, so bullet
# dashes, em dashes and stray pipes do not inflate the count.
_WORDISH = re.compile(r"[^\W_]")


def prose_text(content: str) -> str:
    """``content`` with fenced code blocks and inline code spans removed.

    Everything else is kept verbatim, including headings, list markers and
    table pipes, so line structure survives for callers that need it.
    """
    lines = content.splitlines()
    kept = [line for line, fenced in zip(lines, _fenced(lines), strict=True) if not fenced]
    return _INLINE_CODE_RE.sub(" ", "\n".join(kept))


def _fenced(lines: list[str]) -> list[bool]:
    """Per line, whether it belongs to a fenced block (its fence lines included)."""
    flags: list[bool] = []
    fence: str | None = None
    for line in lines:
        stripped = line.lstrip()
        if fence is None:
            fence = next((f for f in _FENCE_PREFIXES if stripped.startswith(f)), None)
            flags.append(fence is not None)
        else:
            flags.append(True)
            if stripped.startswith(fence):
                fence = None
    return flags


# An inline link to a relative target: not an image, not a web URL.
_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]\n]+)\]\((?!https?://)[^)\n]+\)")


def unwrap_markdown_links(content: str) -> str:
    """*content* with each inline link outside code fences replaced by its text.

    For pages whose template has the wiki do the linking: a model-written link
    carries a relative href that resolves only by accident, while the text
    (usually a backticked path) is what the interlinking pass turns into a
    working link.
    """
    lines = content.splitlines(keepends=True)
    return "".join(
        line if fenced else _unwrap_line(line)
        for line, fenced in zip(lines, _fenced(lines), strict=True)
    )


def _unwrap_line(line: str) -> str:
    """One line's links unwrapped, except a match that starts inside inline
    code: ``handlers[name](event)`` is a call, not a link."""
    code = [span.span() for span in _INLINE_CODE_RE.finditer(line)]

    def text_unless_code(link: re.Match[str]) -> str:
        inside = any(start < link.start() < end for start, end in code)
        return link.group(0) if inside else link.group(1)

    return _MARKDOWN_LINK_RE.sub(text_unless_code, line)


def prose_word_count(content: str) -> int:
    """How many words of prose a page asks the reader to read.

    Code, inline code and table rows are excluded: they are looked up rather
    than read, and counting them would let a page of tables look as expensive
    as a page of argument.  Headings and list text are counted, because the
    reader does read those.
    """
    total = 0
    for line in prose_text(content).splitlines():
        if line.lstrip().startswith("|"):
            continue
        total += sum(1 for token in line.split() if _WORDISH.search(token))
    return total
