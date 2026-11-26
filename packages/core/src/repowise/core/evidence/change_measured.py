"""The measured functions a change edits, each with the command that checks it.

Change risk's ``measured`` block. Pure, over rows a host already read, so
``get_change_risk``, ``repowise risk`` and a hosted reader (the PR bot) name
the same functions:

- a changed function is *measured* when a workload in a class that may lead
  (:data:`~.hotness.LEAD_CLASSES`: entry point, load test, production) ranks
  it, or a benchmark with stored results measures it (calls it, or ran its
  lines). A unit-test run never makes one: it says what the tests cost, not
  the product;
- each one carries one verify command, chosen as the measured row's is
  (:func:`.action_row.row_verify`): the benchmark rerun when one measures it,
  else the ranking workload's rerun. A function with no command is left out,
  so every line says what to run.

Function level only: a change to a file that holds a hot function, but not to
the function, is not measured (P6: 33% of commits touch a hot file, 7% a hot
function). Informational: nothing here moves the change-risk score.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

from .action_row import row_verify
from .hotness import LEAD_CLASSES

#: Functions the block lists; ``total`` counts the rest.
MEASURED_SHOWN = 5


class Ranked(NamedTuple):
    """One workload that ranks a function: its newest ranking run's facts."""

    workload_name: str | None
    workload_class: str | None
    rank: int


@dataclass(frozen=True)
class MeasuredChange:
    """One changed function a measurement speaks for, and how to check it."""

    symbol_id: str
    verify: str
    verify_basis: str
    #: The workload that ranks it best, when one in a leading class does.
    ranked: Ranked | None = None

    def basis(self) -> str:
        """What measured it: the ranking workload, else ``benchmark``."""
        if self.ranked is None:
            return "benchmark"
        r = self.ranked
        return f'rank {r.rank} in {r.workload_class} "{r.workload_name or "unnamed"}"'

    def as_dict(self) -> dict[str, Any]:
        return {"fn": self.symbol_id, "measured": self.basis(), "verify": self.verify}


def measured_changes(
    changed: Iterable[str],
    ranked: Iterable[tuple[str, Ranked]],
    benchmarks: Mapping[str, str],
) -> list[MeasuredChange]:
    """The *changed* function ids a measurement speaks for, ranked ones first
    (best rank), then benchmarked ones, each by id.

    *ranked* is ``(symbol_id, Ranked)`` per workload that ranks a function (a
    function two workloads rank comes twice; its best rank among named
    workloads is kept, since only a name gives a rerun), any
    class: only :data:`~.hotness.LEAD_CLASSES` count. *benchmarks* maps a
    function to the command rerunning the stored benchmarks that measure it
    (:func:`.function_tests.benchmark_command`).
    """
    wanted = set(changed)
    best: dict[str, Ranked] = {}
    for symbol_id, row in ranked:
        if symbol_id not in wanted or row.workload_class not in LEAD_CLASSES:
            continue
        held = best.get(symbol_id)
        if held is None or _order(row) < _order(held):
            best[symbol_id] = row
    out: list[MeasuredChange] = []
    for symbol_id in wanted & (set(best) | set(benchmarks)):
        row = best.get(symbol_id)
        verify, basis = row_verify(
            benchmarks.get(symbol_id),
            row.workload_name if row else None,
            row.workload_class if row else None,
        )
        if verify is not None and basis is not None:
            out.append(MeasuredChange(symbol_id, verify, basis, row))
    out.sort(
        key=lambda m: (
            m.ranked is None,
            m.ranked.rank if m.ranked else 0,
            m.symbol_id,
        )
    )
    return out


def _order(row: Ranked) -> tuple[bool, int, str]:
    """A named workload first (only a name gives a rerun), then the best rank."""
    return (row.workload_name is None, row.rank, row.workload_name or "")


def measured_block(found: list[MeasuredChange]) -> dict[str, Any] | None:
    """Change risk's ``measured`` block, or ``None`` when nothing measured is
    changed (the key is then absent: no evidence is never written as cold)."""
    if not found:
        return None
    shown = found[:MEASURED_SHOWN]
    block: dict[str, Any] = {
        "summary": (
            f"This change edits {len(found)} measured function"
            f"{'' if len(found) == 1 else 's'}: run each verify before and after it "
            "and compare."
        ),
        "functions": [m.as_dict() for m in shown],
    }
    if len(found) > len(shown):
        block["total"] = len(found)
    return block


def measured_line(row: Mapping[str, Any]) -> str:
    """One function of the block as a line: ``fn (what measured it): verify``."""
    return f"{row['fn']} ({row['measured']}): {row['verify']}"


__all__ = [
    "MEASURED_SHOWN",
    "MeasuredChange",
    "Ranked",
    "measured_block",
    "measured_changes",
    "measured_line",
]
