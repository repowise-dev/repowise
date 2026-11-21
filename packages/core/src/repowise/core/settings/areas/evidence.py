"""``evidence:`` in ``.repowise/config.yaml``: caps on stored runtime evidence
and the rules that rank measured functions.

The caps are read by ``repowise evidence add`` and hold for every evidence
kind (profiles, benchmarks, test timings, query counts), so the block is named
for the store, not for performance. The ranking rules are read wherever
``symbol_hotness`` is rebuilt (``repowise.core.evidence.hotness``).
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
        "Most functions stored from one profile, costliest self time (waits excluded) first; "
        "the rest are counted.",
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
    _s(
        "evidence.rank_top_n",
        "int",
        10,
        "A measured function is ranked when it is among this many production functions with "
        "the most self time in its run (built-ins and libraries it calls included, waits "
        "excluded).",
        minimum=1,
    ),
    _s(
        "evidence.rank_min_samples",
        "int",
        30,
        "Fewest self samples on a function for it to be ranked; below this the run has not enough "
        "samples in repo code. Runs that count no samples rank by their order alone.",
        minimum=1,
    ),
    # Calibrated on two real profiles (2026-11-20; a 46k-sample CLI update and a
    # 30k-sample unit-test run of a web backend), hand-triaged per function.
    # Of the 19 functions the two ranked, the real, fixable hotspots held 2.9%
    # to 16% self time each (one more, split over three functions, 1.3% each);
    # the inherent work beside them (a renderer, a disk read, regexes) held
    # 0.5% to 1.9%, with one parser at 3.4%. 2.5% sits in that gap: with the
    # 2% lower bound below it admits 6 of the 7 real hotspots and 1 of the 10
    # inherent rows, which has no fix strategy and so stays an observation.
    # The old 5% admitted 2 of the 7.
    _s(
        "evidence.lead_min_share",
        "float",
        0.025,
        "Self share of the run's time a ranked function needs to count as a measured cost from one "
        "run.",
        minimum=0,
        maximum=1,
    ),
    _s(
        "evidence.lead_min_lower_bound",
        "float",
        0.02,
        "Lower bound of the 95% interval on that share a lead candidate needs from one run.",
        minimum=0,
        maximum=1,
    ),
    _s(
        "evidence.lead_min_runs",
        "int",
        2,
        "Independent runs, in workload classes that may lead, in which a function must be "
        "ranked to become a lead candidate without the share rule.",
        minimum=2,
    ),
)
