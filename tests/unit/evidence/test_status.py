"""The evidence status view: freshness against the index, where the time went."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from repowise.cli.commands.evidence_cmd import _status_line
from repowise.core.evidence.status import evidence_status, run_share, run_status, split_words


def _run(run_id: str, workload: str, commit: str | None, **fields) -> SimpleNamespace:
    base = {
        "id": run_id, "kind": "profile", "workload_name": workload,
        "workload_class": "entry_point", "environment": "local", "value_kind": "wall",
        "unit": "samples", "tool_name": None, "tool_version": None, "commit_sha": commit,
        "dirty": None, "ingested_at": datetime(2026, 1, 1, tzinfo=UTC), "measured_at": None,
        "units_total": None,
        "units_matched": None, "units_skipped": None, "units_outside_repo": None,
        "units_truncated": None, "skipped_json": None, "total_value": None,
        "explained_value": None, "repo_self_value": None, "wait_value": None,
        "library_value": None, "mapping_partial": False, "tests_failed": None,
    }
    return SimpleNamespace(**{**base, **fields})


def test_freshness_is_against_the_indexed_commit() -> None:
    head = "a" * 40
    status = evidence_status(
        [_run("r3", "build", head), _run("r2", "test", "b" * 40), _run("r1", "build", "c" * 40),
         _run("r0", "load", None)],
        head,
    )
    assert status["runs_stored"] == {"profile": 4}
    assert [(w["run_id"], w["freshness"]) for w in status["workloads"]] == [
        ("r3", "current"), ("r2", "stale"), ("r0", "unknown"),
    ]
    # Unknown values stay unknown.
    assert status["workloads"][0]["units"]["matched"] is None
    assert status["workloads"][0]["skipped"] is None


def test_a_run_share_needs_both_values() -> None:
    assert run_share(5300, 583) == 0.11
    assert run_share(None, 5) is None
    assert run_share(0, 0) is None
    assert run_share(10, 12) == 1.0


def test_the_split_leads_with_repo_code_and_is_unknown_without_it() -> None:
    shares = {"repo_self_share": 0.467, "wait_share": 0.3, "library_share": 0.116}
    assert split_words(shares) == "repo code itself 47%, waits 30%, libraries 12%"
    assert split_words({"repo_self_share": None, "wait_share": 0.3}) is None


def test_unknown_values_read_as_unknown_in_the_table() -> None:
    run = run_status(
        _run("r1", "[b]x[/b]", None, ingested_at=datetime(2026, 1, 1), total_value=10.0,
             explained_value=9.0, repo_self_value=5.0, wait_value=3.0, library_value=1.0,
             unit=None),
        None,
    )
    assert run["ingested_at"] == "2026-01-01T00:00:00+00:00"
    line = _status_line(run)
    assert "entry counts unknown" in line
    assert "repo code itself 50%, waits 30%, libraries 10% of 10." in line
    assert "explain" not in line
    older = _status_line(run_status(_run("r0", "old", None, total_value=10.0), None))
    assert "time split unknown." in older
    assert "None" not in line
    assert "\\[b]x\\[/b]" in line


def test_a_red_suites_failures_show_with_its_run() -> None:
    run = run_status(_run("r1", "python tests", "a" * 40, workload_class="unit_test", tests_failed=3), None)

    assert run["tests_failed"] == 3
    assert "; 3 tests failed." in _status_line(run)
    assert "tests failed" not in _status_line(run_status(_run("r2", "x", None, tests_failed=0), None))


def test_newest_is_by_measurement_time_like_the_ranking() -> None:
    # r2 was re-added later (newer ingest) but measured earlier than r1.
    status = evidence_status(
        [
            _run("r2", "build", None, ingested_at=datetime(2026, 3, 1, tzinfo=UTC),
                 measured_at=datetime(2026, 1, 1, tzinfo=UTC)),
            _run("r1", "build", None, ingested_at=datetime(2026, 2, 1, tzinfo=UTC),
                 measured_at=datetime(2026, 2, 1, tzinfo=UTC)),
            _run("b1", "build", None, kind="benchmark", measured_at=None),
        ],
        None,
    )
    assert [w["run_id"] for w in status["workloads"]] == ["r1", "b1"]
    assert status["runs_stored"] == {"benchmark": 1, "profile": 2}


def test_a_partial_run_is_newest_only_without_a_complete_one() -> None:
    # Ranking skips partial runs, so status names the complete run it ranks.
    status = evidence_status(
        [
            _run("p2", "build", None, mapping_partial=True,
                 measured_at=datetime(2026, 3, 1, tzinfo=UTC)),
            _run("r1", "build", None, measured_at=datetime(2026, 2, 1, tzinfo=UTC)),
            _run("p1", "load", None, mapping_partial=True),
        ],
        None,
    )
    assert [w["run_id"] for w in status["workloads"]] == ["r1", "p1"]
    assert status["workloads"][1]["mapping_partial"] is True
    assert status["runs_stored"] == {"profile": 3}
