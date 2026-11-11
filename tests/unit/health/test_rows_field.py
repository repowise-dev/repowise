"""``rows.field`` reads every row shape the same way, however often it is asked."""

from __future__ import annotations

from collections import UserDict, namedtuple
from dataclasses import dataclass

from repowise.core.analysis.health.rows import field


@dataclass
class _Metric:
    file_path: str
    score: float


class _Settings(dict):
    """A ``dict`` subclass: read as a mapping, not by attribute."""

    file_path = "attribute, not key"


class _Pairs(tuple):
    """A tuple subclass that also has a ``get``: read by attribute, as a tuple."""

    def get(self, name, default=None):  # pragma: no cover - must never be called
        raise AssertionError("a tuple is read by attribute")


def test_each_shape_reads_the_same_on_every_call() -> None:
    Row = namedtuple("Row", "file_path score")
    rows = [
        {"file_path": "a.py", "score": 1.0},
        UserDict({"file_path": "a.py", "score": 1.0}),
        _Settings({"file_path": "a.py", "score": 1.0}),
        Row("a.py", 1.0),
        _Metric("a.py", 1.0),
    ]
    for _ in range(3):  # the first read of a type decides how later ones go
        for row in rows:
            assert field(row, "file_path") == "a.py"
            assert field(row, "score") == 1.0
            assert field(row, "missing", "d") == "d"
    assert field(_Pairs(), "missing", 0) == 0
