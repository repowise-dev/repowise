"""A function's normalized body: what tells a moved or renamed function from new code.

The body is the function's token rows (``duplication.tokenizer``: comments,
whitespace and imports dropped) between its first and last line, each token as
its source text and the function's own name blanked, so a reindent, a comment
edit or a rename leaves it unchanged. Line-bounded: code sharing a line with the
function joins its body.

The digest is an unkeyed BLAKE2b of that stream. Unlike the builtin ``hash`` it
does not change between processes, so it can be stored and compared with a
later run's.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left, bisect_right
from collections.abc import Sequence

from .health.duplication.tokenizer import TokenRow

_NAME_MARK = b"\x00"
_SEPARATOR = b"\x1f"


def rows_between(
    rows: Sequence[TokenRow], starts: Sequence[int], start_line: int, end_line: int
) -> Sequence[TokenRow]:
    """The rows starting on lines *start_line* to *end_line*; *starts* is each row's start line."""
    return rows[bisect_left(starts, start_line) : bisect_right(starts, end_line)]


def body_parts(rows: Sequence[TokenRow], raw: bytes, name: str) -> list[bytes]:
    """Each row's source text from *raw*, with identifiers spelled *name* blanked."""
    return [_NAME_MARK if row[5] == name else raw[row[3] : row[4]] for row in rows]


def body_digest(parts: Sequence[bytes]) -> str:
    """The 32-hex digest of a normalized body (:func:`body_parts`)."""
    return hashlib.blake2b(_SEPARATOR.join(parts), digest_size=16).hexdigest()


__all__ = ["body_digest", "body_parts", "rows_between"]
