"""Every snapshot writer has to record the same thing.

``save_health_snapshot`` is called from four places: the ``repowise health``
persister, the upgrade flow, the full-index pipeline, and the store wrapper
that forwards to crud. A repo's history is whichever of those wrote it last.

This has already gone wrong once here, with a different argument: four
``HealthAnalyzer`` call sites of which only one passed ``community_label_map``, so a
repo's module namespace flipped depending on which command last ran. The same
shape applied to ``per_file_deductions`` would make a floored file's trend
depth appear and disappear as the user alternated between ``repowise health``,
``repowise upgrade`` and a full index — a bug that reproduces only for people
who use more than one of them.

So this walks the source rather than any one code path. A fifth writer added
later gets caught the day it lands, which is the only time the fix is cheap.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._source_tree import PACKAGES, sources_under

# Only the shipped Python, not the UI packages' ``node_modules``.
_SOURCE_ROOTS = sorted(PACKAGES.glob("*/src"))

# The store wrapper forwards whatever it is handed; it builds no maps of its
# own, so it is a pass-through rather than a writer with an opinion.
_FORWARDERS = {"_sql_analysis.py"}


def _snapshot_calls() -> list[tuple[Path, ast.Call]]:
    out: list[tuple[Path, ast.Call]] = []
    for src in sources_under(*_SOURCE_ROOTS):
        if src.path.name in _FORWARDERS or src.tree is None:
            continue
        for node in ast.walk(src.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "save_health_snapshot":
                out.append((src.path, node))
    return out


def test_the_writers_this_test_guards_still_exist() -> None:
    """Without this the sweep below passes vacuously if the call is renamed."""
    calls = _snapshot_calls()
    assert len(calls) >= 3, [str(p) for p, _ in calls]


def test_every_writer_records_the_depth_as_well_as_the_score() -> None:
    missing = []
    for path, call in _snapshot_calls():
        kwargs = {kw.arg for kw in call.keywords}
        # ``**fields`` is ``trends.snapshot_fields``, which carries both maps.
        if None in kwargs:
            continue
        if not {"per_file_scores", "per_file_deductions"} <= kwargs:
            missing.append(f"{path.name}:{call.lineno} passes {sorted(kwargs)}")
    assert not missing, "snapshot writers that would record a different history: " + "; ".join(
        missing
    )


def test_no_writer_builds_the_maps_by_hand() -> None:
    """Both maps come from ``snapshot_file_maps``.

    The score map used to be an inline dict comprehension repeated at each
    site. Two comprehensions that agree today drift the moment one of them
    learns about rounding, exclusions or test files and the others do not.
    """
    inline = []
    for path, call in _snapshot_calls():
        for kw in call.keywords:
            if kw.arg in {"per_file_scores", "per_file_deductions"} and not isinstance(
                kw.value, ast.Name
            ):
                inline.append(f"{path.name}:{call.lineno} builds {kw.arg} inline")
    assert not inline, "; ".join(inline)
