"""Doctor's evidence rows: coverage and runtime, present or absent, commit, age, stale."""

from __future__ import annotations

from pathlib import Path

from repowise.cli.commands import _evidence_freshness
from repowise.cli.commands.doctor_cmd._checks.context import CheckContext
from repowise.cli.commands.doctor_cmd._checks.evidence import evidence_rows

ABSENT = {"present": False, "commit_sha": None, "ingested_at": None, "age_days": None,
          "freshness": "unknown"}


def _rows(monkeypatch, block):
    def read(_path):
        if isinstance(block, Exception):
            raise block
        return block

    monkeypatch.setattr(_evidence_freshness, "stored_freshness", read)
    ctx = CheckContext(repo_path=Path("."), repowise_dir=Path(".repowise"))
    return {r.id: r for r in evidence_rows(ctx)}


def test_absent_and_stale_rows_name_the_setup_command(monkeypatch) -> None:
    stale = {"present": True, "commit_sha": "a" * 40, "ingested_at": "2026-11-01T00:00:00+00:00",
             "age_days": 18, "freshness": "stale"}
    rows = _rows(monkeypatch, {"reference_commit": "b" * 40, "coverage": stale,
                               "runtime": {**ABSENT, "runs": 0}})
    cov, rt = rows["coverage-evidence"], rows["runtime-evidence"]
    assert cov.detail == (
        "stale: measured at aaaaaaaa, 18 days ago; HEAD is bbbbbbbb; run repowise coverage setup"
    )
    assert (cov.level, cov.fix, cov.data["age_days"]) == ("ok", "repowise coverage setup", 18)
    assert rt.detail == "none stored; run repowise evidence setup"
    assert (rt.level, rt.fix, rt.data["runs"]) == ("ok", "repowise evidence setup", 0)


def test_current_evidence_needs_nothing(monkeypatch) -> None:
    current = {"present": True, "commit_sha": "b" * 40, "ingested_at": "2026-11-19T00:00:00+00:00",
               "age_days": 0, "freshness": "current"}
    rows = _rows(monkeypatch, {"reference_commit": "b" * 40, "coverage": current,
                               "runtime": {**current, "runs": 4}})
    assert rows["runtime-evidence"].detail == "measured at HEAD (bbbbbbbb), today"
    assert rows["runtime-evidence"].fix is None


def test_no_index_no_rows_and_an_unreadable_store_warns(monkeypatch) -> None:
    assert _rows(monkeypatch, None) == {}
    (row,) = _rows(monkeypatch, RuntimeError("locked")).values()
    assert (row.id, row.level, row.ok) == ("evidence-freshness", "warn", True)


def test_an_older_store_names_the_update(monkeypatch) -> None:
    from repowise.cli.helpers import StaleIndexError

    (row,) = _rows(monkeypatch, StaleIndexError()).values()
    assert (row.level, row.fix) == ("warn", "repowise update")
    assert "StaleIndexError" not in row.detail
