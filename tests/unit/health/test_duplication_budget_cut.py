"""A splice of a state the window budget cut short must equal the full recompute.

Each _BODY file holds 31 windows, so a 70-window budget keeps a.py and b.py,
stops at c.py and never reads d.py. The splice is taken only when the cut
would fall on the same file; otherwise the run falls back to the full pipeline.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.duplication.detector import clones_against_index
from repowise.core.analysis.health.duplication.limits import DuplicationLimits
from repowise.core.analysis.health.duplication.pair_index import load_pair_index
from tests.unit.health.test_duplication_incremental import (
    _BODY,
    WINDOW,
    _full,
    _incremental,
    _key,
    _parsed,
    _seed,
)

CUT = DuplicationLimits(time_budget_secs=0, max_total_windows=70)
_FILES = {"a.py": _BODY, "b.py": _BODY, "c.py": _BODY, "d.py": _BODY}
_TINY = "x = 1\n"


def _seed_cut(tmp_path: Path) -> Path:
    cache_dir = _seed(tmp_path, _FILES, limits=CUT)
    idx = load_pair_index(cache_dir, WINDOW, CUT)
    assert idx is not None and idx.window_budget_hit
    assert idx.budget_stop == "c.py" and idx.unread == {"d.py"}
    return cache_dir


def _splice(tmp_path: Path, cache_dir: Path, changed: set[str]) -> dict:
    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, changed, limits=CUT)
    assert _key(report) == _key(_full(parsed, limits=CUT))
    return report.diagnostics


def test_edit_before_the_cut_splices(tmp_path: Path):
    cache_dir = _seed_cut(tmp_path)
    (tmp_path / "a.py").write_text(_BODY.replace("doit", "edited"))
    diagnostics = _splice(tmp_path, cache_dir, {"a.py"})
    assert diagnostics.get("incremental") is True
    assert diagnostics["window_budget_hit"] is True


def test_edit_after_the_cut_splices_without_reading_it(tmp_path: Path):
    cache_dir = _seed_cut(tmp_path)
    (tmp_path / "d.py").write_text(_BODY.replace("doit", "edited"))
    assert _splice(tmp_path, cache_dir, {"d.py"}).get("incremental") is True


def test_new_gated_file_before_the_cut_splices(tmp_path: Path):
    cache_dir = _seed_cut(tmp_path)
    (tmp_path / "aa.py").write_text(_TINY)
    assert _splice(tmp_path, cache_dir, {"aa.py"}).get("incremental") is True


def test_chained_splices_keep_the_cut(tmp_path: Path):
    cache_dir = _seed_cut(tmp_path)
    (tmp_path / "a.py").write_text(_BODY.replace("doit", "first"))
    assert _splice(tmp_path, cache_dir, {"a.py"}).get("incremental") is True
    (tmp_path / "b.py").write_text(_BODY.replace("doit", "second"))
    assert _splice(tmp_path, cache_dir, {"b.py"}).get("incremental") is True


def test_shrinking_the_prefix_past_the_cut_falls_back(tmp_path: Path):
    # With b.py gated out, c.py fits and the full run stops at d.py instead.
    cache_dir = _seed_cut(tmp_path)
    (tmp_path / "b.py").write_text(_TINY)
    assert "incremental" not in _splice(tmp_path, cache_dir, {"b.py"})


def test_growing_the_prefix_past_the_budget_falls_back(tmp_path: Path):
    cache_dir = _seed_cut(tmp_path)
    (tmp_path / "a.py").write_text(_BODY + _BODY.replace("doit", "again"))
    assert "incremental" not in _splice(tmp_path, cache_dir, {"a.py"})


def test_edited_stop_file_falls_back(tmp_path: Path):
    cache_dir = _seed_cut(tmp_path)
    (tmp_path / "c.py").write_text(_TINY)
    assert "incremental" not in _splice(tmp_path, cache_dir, {"c.py"})


def test_an_unchanged_file_crossing_the_cut_falls_back(tmp_path: Path):
    # Same files, d.py now collected before c.py: the state is not this run's.
    cache_dir = _seed_cut(tmp_path)
    by_path = {pf.file_info.path: pf for pf in _parsed(tmp_path)}
    parsed = [by_path[p] for p in ("a.py", "b.py", "d.py", "c.py")]
    report = _incremental(parsed, cache_dir, set(), limits=CUT)
    assert "incremental" not in report.diagnostics
    assert _key(report) == _key(_full(parsed, limits=CUT))


def test_read_only_splice_refuses_a_cut_state(tmp_path: Path):
    cache_dir = _seed_cut(tmp_path)
    parsed = [pf for pf in _parsed(tmp_path) if pf.file_info.path == "a.py"]
    assert (
        clones_against_index(parsed, None, cache_dir, window_tokens=WINDOW, limits=CUT) is None
    )
