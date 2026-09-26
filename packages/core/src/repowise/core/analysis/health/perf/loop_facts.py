"""What a loop around a performance hit proves about the fix.

A hit says the cost is paid per iteration. These facts say how many iterations
to expect and whether the obvious fix is provably the same program: a bulk
counterpart that takes every key at once, or a bound the fan-out would run
under. Each is read off the syntax of one function by a language dialect, and
each answers ``None`` or ``unknown`` when that syntax does not settle it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from tree_sitter import Node

LoopMagnitude = Literal["grows_with_data", "bounded", "unknown"]

# The boundary kind of a call node when it is an I/O sink, else ``None``.
SinkProbe = Callable[["Node"], str | None]


@dataclass(frozen=True, slots=True)
class BatchForm:
    """The bulk counterpart of a per-key sink, written as the code would call it.

    ``equivalent`` is the result-equivalence proof: batching returns the rows
    the loop would have read, one key at a time, and nothing else in the loop
    can observe the difference.
    """

    call: str
    equivalent: bool


@dataclass(frozen=True, slots=True)
class LoopFacts:
    chunked: bool = False
    magnitude: LoopMagnitude = "unknown"
    batch: BatchForm | None = None
    concurrency_bound: str | None = None

    def as_details(self) -> dict[str, Any]:
        """Finding ``details`` keys; defaults are omitted so old findings are unchanged."""
        details: dict[str, Any] = {}
        if self.chunked:
            details["chunked_iteration"] = True
        if self.magnitude != "unknown":
            details["loop_magnitude"] = self.magnitude
        if self.batch is not None:
            details["batch_form"] = self.batch.call
            details["batch_equivalent"] = self.batch.equivalent
        if self.concurrency_bound:
            details["concurrency_bound"] = self.concurrency_bound
        return details


__all__ = ["BatchForm", "LoopFacts", "LoopMagnitude", "SinkProbe"]
