"""``repowise update`` writes its phase timings to ``state.json``.

The index-only persist is where the state write happens on the hook's hot
path, so it is where the run's table lands and where its ``run`` row closes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from repowise.core.pipeline import PhaseTimings, timed


def _quiet(monkeypatch) -> None:
    from repowise.cli.commands.update_cmd import persistence, reporting
    from repowise.core.pipeline import incremental as core_incremental

    async def fake_persist(*_args, timings=None, **_kwargs):
        with timed(timings, "persist.graph_nodes"):
            pass

    monkeypatch.setattr(core_incremental, "persist_incremental_index", fake_persist)
    monkeypatch.setattr(persistence, "full_rescore_due", lambda *_a, **_k: False)
    monkeypatch.setattr(reporting, "show_index_only_completion", lambda **_k: None)


def _persist(tmp_path: Path, timings: PhaseTimings | None) -> dict:
    from repowise.cli.commands.update_cmd.persistence import _persist_index_only_update

    _persist_index_only_update(
        tmp_path,
        object(),
        {},
        None,
        None,
        {"last_sync_commit": "0" * 40},
        "1" * 40,
        time.monotonic(),
        [],
        timings=timings,
    )
    return json.loads((tmp_path / ".repowise" / "state.json").read_text(encoding="utf-8"))


def test_index_only_update_writes_phase_timings(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".repowise").mkdir()
    _quiet(monkeypatch)
    timings = PhaseTimings()
    timings.start("run")

    state = _persist(tmp_path, timings)

    rows = state["phase_timings"]
    assert "run" in rows and "persist.graph_nodes" in rows
    # ``run`` is the denominator: closed at the write, so it bounds every row.
    assert all(v <= rows["run"] for v in rows.values())


def test_no_table_leaves_state_without_the_key(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".repowise").mkdir()
    _quiet(monkeypatch)

    state = _persist(tmp_path, None)

    assert "phase_timings" not in state


def test_rescore_gets_its_own_row(tmp_path: Path, monkeypatch) -> None:
    from repowise.cli.commands.update_cmd import persistence

    (tmp_path / ".repowise").mkdir()
    _quiet(monkeypatch)
    monkeypatch.setattr(persistence, "full_rescore_due", lambda *_a, **_k: True)
    monkeypatch.setattr(persistence, "run_decay_health_rescore", lambda *_a, **_k: True)
    timings = PhaseTimings()
    timings.start("run")

    state = _persist(tmp_path, timings)

    assert "rescore" in state["phase_timings"]
    assert state["health_analyzer_version"]
