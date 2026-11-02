"""The update's decision-evolution step: evidence scope, concurrency knob, cost label."""

from __future__ import annotations

import json
from types import SimpleNamespace

from repowise.cli.commands.update_cmd.command import _run_decision_evolution
from repowise.core.generation.cost_tracker import CostTracker


def _diff(path: str, status: str = "modified") -> SimpleNamespace:
    return SimpleNamespace(path=path, status=status, trigger_commit_message=None, diff_text=None)


def test_evolution_judge_calls_are_booked_as_decision_spend(tmp_path, monkeypatch):
    import repowise.core.analysis.decision_evolution as evo_mod

    seen: dict = {}
    tracker = CostTracker()

    async def fake_run(session, repo_id, *, evidence_by_file, max_concurrency, **kw):
        # The label a provider would read at record() time, mid-judge.
        seen["operation"] = tracker.operation
        seen["evidence"] = evidence_by_file
        seen["max_concurrency"] = max_concurrency
        return {"regen_files": set(), "superseded": 0, "amended": 0, "reaffirmed": 0}

    monkeypatch.setattr(evo_mod, "run_update_evolution", fake_run)
    monkeypatch.setattr(evo_mod, "commits_in_range", lambda *a: {"aaaaaaaa"})
    meta = {
        "src/a.py": {
            "significant_commits_json": json.dumps(
                [
                    {"sha": "aaaaaaaa", "message": "migrate a"},
                    {"sha": "bbbbbbbb", "message": "replace b"},
                ]
            )
        }
    }

    out = _run_decision_evolution(
        tmp_path,
        [_diff("src/a.py")],
        meta,
        base_ref="HEAD~1",
        head="HEAD",
        provider=object(),
        cost_tracker=tracker,
        max_concurrency=7,
    )

    assert out is not None
    assert seen["operation"] == "decision_extraction"
    assert tracker.operation == "doc_generation"  # restored afterwards
    assert seen["max_concurrency"] == 7
    assert "migrate a" in seen["evidence"]["src/a.py"]
    assert "replace b" not in seen["evidence"]["src/a.py"]


def test_no_changed_file_skips_evolution(tmp_path):
    out = _run_decision_evolution(
        tmp_path,
        [_diff("x.py", "deleted")],
        {},
        base_ref="HEAD~1",
        head="HEAD",
        provider=None,
        cost_tracker=CostTracker(),
        max_concurrency=1,
    )
    assert out is None
