"""Column-select results as named tuples, for folds over every row of a table.

A tuple's attributes read in C, while a SQLAlchemy ``Row`` or entity resolves
each one in Python: over a large repository's rows that difference is most of
a read's cost.
"""

from __future__ import annotations

from collections import namedtuple
from functools import cache
from typing import Any


@cache
def row_shape(names: tuple[str, ...]) -> type:
    """One named-tuple class per column list, built once."""
    return namedtuple("Row", names)  # type: ignore[misc]


def plain_rows(result: Any) -> list[Any]:
    """Every row of a column select's *result* as a named tuple."""
    make = row_shape(tuple(result.keys()))._make
    return [make(row) for row in result]


__all__ = ["plain_rows", "row_shape"]
