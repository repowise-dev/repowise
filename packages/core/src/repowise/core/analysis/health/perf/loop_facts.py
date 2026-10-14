"""What a loop around a performance hit proves about the fix.

A hit says the cost is paid per iteration. These facts say how many iterations
to expect, what the loop walks, and whether the obvious fix is provably the
same program: a bulk counterpart that takes every key at once, or a bound the
fan-out would run under. Each is read off the syntax of one function by a
language dialect, and each answers ``None`` or ``unknown`` when that syntax
does not settle it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from tree_sitter import Node

LoopMagnitude = Literal["grows_with_data", "bounded", "unknown"]

# What a loop walks. ``query``: rows a database or network read returned;
# ``parameter``: a collection the caller passed in; ``caller_key``: the rows of
# one primary key; ``rare_subset``: rows of a growing table a filter keeps
# only for a rare state (expired, stale, failed); ``config_table``: a registry
# or configuration table (registered repos, providers, plugins, settings);
# ``constant``: a literal, a constant bound or a fixed-width slice;
# ``listing``: a directory listing; ``stream``: lines or chunks read off a
# file, a process or a response.
LoopIterates = Literal[
    "query",
    "parameter",
    "caller_key",
    "rare_subset",
    "config_table",
    "constant",
    "listing",
    "stream",
    "unknown",
]

# Why splitting a loop's per-iteration calls into one batch would change the
# program: ``first_match`` stops at the first call that answers;
# ``turn_loop`` feeds each call's result into the next call.
NotBatchable = Literal["first_match", "turn_loop"]

# The boundary kind of a call node when it is an I/O sink, else ``None``.
SinkProbe = Callable[["Node"], str | None]


@dataclass(frozen=True, slots=True)
class LoopSource:
    """What one loop's iterable is, and how many iterations that proves."""

    iterates: LoopIterates = "unknown"
    magnitude: LoopMagnitude = "unknown"


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
    # No element or index the loop binds reaches the sink call: nothing per-key to batch.
    key_unused: bool = False
    # What the innermost loop walks, and why its calls cannot be one batch.
    iterates: LoopIterates = "unknown"
    not_batchable: NotBatchable | None = None

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
        if self.key_unused:
            details["loop_key_unused"] = True
        if self.iterates != "unknown":
            details["loop_iterates"] = self.iterates
        if self.not_batchable:
            details["loop_not_batchable"] = self.not_batchable
        return details


def combined_magnitude(sources: list[LoopSource]) -> LoopMagnitude:
    """Grows if any enclosing loop grows; bounded only if every one is, since a
    retry loop inside a loop over rows still runs once per row."""
    found = {source.magnitude for source in sources}
    if "grows_with_data" in found:
        return "grows_with_data"
    return "bounded" if found == {"bounded"} else "unknown"


__all__ = [
    "BatchForm",
    "LoopFacts",
    "LoopIterates",
    "LoopMagnitude",
    "LoopSource",
    "NotBatchable",
    "SinkProbe",
    "combined_magnitude",
]
