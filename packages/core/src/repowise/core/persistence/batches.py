"""Fixed-size pieces for the ``IN`` lists a batched read is built from."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TypeVar

# SQLite caps bound parameters per statement, so every ``IN`` list is issued in
# fixed-size pieces rather than one query per item.
_IN_CLAUSE_CHUNK = 500

T = TypeVar("T")


def chunked(items: Sequence[T]) -> Iterator[Sequence[T]]:
    """Slice *items* into pieces of at most ``_IN_CLAUSE_CHUNK``, in order."""
    for start in range(0, len(items), _IN_CLAUSE_CHUNK):
        yield items[start : start + _IN_CLAUSE_CHUNK]
