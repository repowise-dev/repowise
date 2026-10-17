"""``_grade_answer`` pinned over a table of inputs.

Each row was graded once and its full ``_Grade`` recorded. The rows cover every
outcome shape and every gate that demotes a grade, so a reordered or rewritten
gate changes a recorded verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.server.mcp_server.tool_answer.confidence import _grade_answer

_CASES = json.loads(
    (Path(__file__).parents[3] / "fixtures" / "mcp" / "grade_answer_cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("row", _CASES, ids=[f"case{i}" for i in range(len(_CASES))])
def test_grade_matches_recorded_verdict(row: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in row["env"].items():
        monkeypatch.setenv(name, value)
    kwargs = dict(row["kwargs"], question_ids=set(row["kwargs"]["question_ids"]))
    got = json.loads(json.dumps(_grade_answer(**kwargs)._asdict()))
    assert got == row["expected"]


def test_table_reaches_every_grade() -> None:
    assert {row["expected"]["confidence"] for row in _CASES} == {"high", "medium", "low"}
