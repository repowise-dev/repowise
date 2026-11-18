"""``evidence:`` in ``.repowise/config.yaml``: caps on stored runtime evidence.

Read by ``repowise evidence add``. The caps hold for every evidence kind
(profiles, benchmarks, test timings, query counts), so the block is named for
the store, not for performance.
"""

from __future__ import annotations

from repowise.core.settings.areas._common import factory

AREA = "evidence"
_s = factory(AREA)

SETTINGS = (
    _s(
        "evidence.max_file_mb",
        "float",
        20.0,
        "Largest evidence file read, in MB; a bigger one is refused before it is read.",
        minimum=0,
        exclusive_minimum=True,
    ),
    _s(
        "evidence.max_symbols_per_run",
        "int",
        1500,
        "Most functions stored from one profile, costliest first; the rest are counted.",
        minimum=1,
    ),
    _s(
        "evidence.max_rows_per_run",
        "int",
        30000,
        "Most benchmark, test or query rows stored from one run; the rest are counted.",
        minimum=1,
    ),
    _s(
        "evidence.history_runs",
        "int",
        20,
        "Runs kept per workload and environment for each evidence kind; oldest go first.",
        minimum=1,
    ),
    _s(
        "evidence.max_runs",
        "int",
        50,
        "Runs kept per evidence kind across all workloads; oldest go first, but each workload "
        "and environment always keeps its newest run, so the kind can exceed this by up to "
        "the number of workloads.",
        minimum=1,
    ),
)
