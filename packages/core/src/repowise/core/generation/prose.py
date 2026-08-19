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
    kept: list[str] = []
    fence: str | None = None
    for line in content.splitlines():
        stripped = line.lstrip()
        if fence is None:
            opener = next((f for f in _FENCE_PREFIXES if stripped.startswith(f)), None)
            if opener is not None:
                fence = opener
                continue
            kept.append(line)
        elif stripped.startswith(fence):
            fence = None
    return _INLINE_CODE_RE.sub(" ", "\n".join(kept))


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
