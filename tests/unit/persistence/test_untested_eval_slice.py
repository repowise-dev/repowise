"""The "untested" rule against hand labels, on a recorded slice of three real indexes.

``tests/fixtures/untested_eval/labels.json`` labels about 150 files;
``slice.json`` holds the graph rows ``pr_blast.file_tests`` reads for a fixed
26 of them (the cases the rule has been tuned on, plus Go and Java files).
This replays the slice and fails when the rule gets worse against the labels
than it was when the slice was recorded. The full set is scored by
``scripts/eval_untested_rule.py``; the fixture README says how to re-record.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.analysis.pr_blast import file_tests
from repowise.core.persistence.models import GraphEdge, GraphNode
from tests.unit.persistence.helpers import insert_repo

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "untested_eval"
LABELS = {
    f["id"]: f for f in json.loads((FIXTURE / "labels.json").read_text(encoding="utf-8"))["files"]
}
SLICE = json.loads((FIXTURE / "slice.json").read_text(encoding="utf-8"))["repos"]


def _score(states: dict[str, str], ids: list[str]) -> tuple[int, int, int]:
    """(right "untested" claims, wrong ones, real gaps called tested)."""
    right = wrong = missed = 0
    for i in ids:
        label, state = LABELS[i]["label"], states[LABELS[i]["path"]]
        if state == "untested":
            right += label == "untested"
            wrong += label != "untested"
        elif label == "untested" and state == "tested":
            missed += 1
    return right, wrong, missed


async def _replay(session, recorded: dict) -> dict[str, str]:
    # No checkout on disk, so nothing is judged type-only; the recorder refuses type-only files.
    repo = await insert_repo(session, local_path=str(FIXTURE / "no-checkout"))
    session.add_all(GraphNode(repository_id=repo.id, node_id=p) for p in recorded["files"])
    session.add_all(
        GraphNode(repository_id=repo.id, node_id=p, is_test=True) for p in recorded["tests"]
    )
    session.add_all(
        GraphEdge(
            repository_id=repo.id,
            source_node_id=src,
            target_node_id=dst,
            edge_type=edge_type,
            resolution_origin=origin,
            hint_source=hint,
        )
        for src, dst, edge_type, origin, hint in recorded["edges"]
    )
    await session.commit()
    paths = [LABELS[i]["path"] for i in recorded["ids"]]
    return {p: row.state for p, row in (await file_tests(session, repo.id, paths)).items()}


@pytest.mark.parametrize("repo", sorted(SLICE))
async def test_untested_rule_does_not_regress_on_labelled_slice(async_session, repo):
    recorded = SLICE[repo]
    states = await _replay(async_session, recorded)
    right, wrong, missed = _score(states, recorded["ids"])
    was_right, was_wrong, was_missed = _score(recorded["states"], recorded["ids"])
    detail = {
        LABELS[i]["path"]: (LABELS[i]["label"], states[LABELS[i]["path"]]) for i in recorded["ids"]
    }
    assert right >= was_right, f"real gaps no longer called untested: {detail}"
    assert wrong <= was_wrong, f"more wrong untested claims: {detail}"
    assert missed <= was_missed, f"more real gaps called tested: {detail}"


def test_slice_ids_are_labelled():
    for recorded in SLICE.values():
        assert set(recorded["ids"]) <= set(LABELS)
