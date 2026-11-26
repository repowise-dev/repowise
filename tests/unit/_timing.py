"""Linear-time guards that hold on any machine: a growth ratio, not a budget.

A wall-clock budget fails whenever a shared runner is slow. How the time grows
with the input does not depend on the machine: the same work on an input four
times larger takes about 4x as long when it is linear and about 16x when it is
quadratic. :func:`assert_linear_growth` times both sizes, alternating, keeps
the best of five runs of each, and holds off garbage collection so a pause
lands on neither side.
"""

from __future__ import annotations

import gc
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

#: A 4x input may cost at most this many times the 1x input: between linear
#: (about 4x) and quadratic (about 16x).
MAX_GROWTH = 8


def assert_linear_growth(
    run: Callable[[T], object],
    small: T,
    large: T,
    *,
    reset: Callable[[], object] | None = None,
    rounds: int = 5,
) -> None:
    """Fail when ``run(large)`` costs ``MAX_GROWTH`` times ``run(small)`` or more.

    *large* should be about four times *small*, and *small* big enough to take
    a few milliseconds. *reset* runs before every timed call, outside the
    timing: pass the ``cache_clear`` of a per-input memo so every run does the
    work.
    """
    best = [float("inf"), float("inf")]
    enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(rounds):
            for side, value in enumerate((small, large)):
                if reset is not None:
                    reset()
                started = time.perf_counter()
                run(value)
                best[side] = min(best[side], time.perf_counter() - started)
    finally:
        if enabled:
            gc.enable()
    ratio = best[1] / best[0]
    assert ratio < MAX_GROWTH, (
        f"4x input took {ratio:.2f}x as long ({best[1] * 1e3:.1f} ms vs {best[0] * 1e3:.1f} ms)"
    )
