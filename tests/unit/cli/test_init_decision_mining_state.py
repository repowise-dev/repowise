"""Init records the decision mining it ran in the per-source ledger (#6031).

Before, only ``repowise update`` wrote ``decision_mining``, so ``decision
status`` showed every source as never run after a fresh ``init``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from repowise.cli.commands.init_cmd._finish import record_index_decision_mining
from repowise.core.analysis.decisions import mining_state

HEAD = "a" * 40


def _status(state: dict, source: str) -> dict:
    return mining_state.status_fields(mining_state.status_entries(state).get(source))


def test_every_source_init_ran_has_a_last_run_at_the_init_head(tmp_path: Path) -> None:
    state: dict = {}

    record_index_decision_mining(state, tmp_path, SimpleNamespace(failures={}), HEAD)

    for source in ("adr", "inline_marker"):
        status = _status(state, source)
        assert status["last_run"] is not None, source
        assert status["last_run_status"] == "ok"
        assert status["last_run_commit"] == HEAD
        assert status["last_success_commit"] == HEAD


def test_a_source_that_failed_in_init_is_recorded_as_failed(tmp_path: Path) -> None:
    state: dict = {}
    report = SimpleNamespace(failures={"adr": "could not read docs/adr"})

    record_index_decision_mining(state, tmp_path, report, HEAD)

    adr = _status(state, "adr")
    assert adr["last_run_status"] == "failed"
    assert adr["last_failure"] == "could not read docs/adr"
    assert adr["last_success_commit"] is None
    assert _status(state, "inline_marker")["last_run_status"] == "ok"


def test_a_skipped_decision_phase_records_nothing(tmp_path: Path) -> None:
    state: dict = {"last_sync_commit": HEAD}

    record_index_decision_mining(state, tmp_path, None, HEAD)

    assert mining_state.DECISION_MINING_STATE_KEY not in state


def test_init_keeps_the_entries_of_sources_it_did_not_run(tmp_path: Path) -> None:
    state: dict = {}
    mining_state.record_success(state, "session_mining", "b" * 40)

    record_index_decision_mining(state, tmp_path, SimpleNamespace(failures={}), HEAD)

    assert _status(state, "session_mining")["last_success_commit"] == "b" * 40


def test_record_run_splits_failures_from_successes() -> None:
    state: dict = {}

    mining_state.record_run(state, ("adr", "pr"), {"pr": "rate limited"}, HEAD)

    assert _status(state, "adr")["last_run_status"] == "ok"
    assert _status(state, "pr")["last_run_status"] == "failed"
    assert _status(state, "pr")["last_failure"] == "rate limited"


def test_the_init_state_carries_the_ledger(tmp_path: Path, monkeypatch) -> None:
    """``_base_state`` (what init writes to state.json) records the decision phase."""
    import subprocess

    from repowise.cli.commands.init_cmd import _finish
    from repowise.cli.commands.init_cmd._options import InitOptions, InitRun

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-qm",
            "one",
        ],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    fields = {name: None for name in InitOptions.__dataclass_fields__}
    fields.update(run_mode="standard", index_only=True, include_submodules=False)
    run = InitRun(opts=InitOptions(**fields), repo_path=tmp_path, start=0.0, is_interactive=False)
    run.result = SimpleNamespace(
        decision_report=SimpleNamespace(failures={}),
        generated_pages=[],
        parsed_files=[],
        knowledge_graph_result=None,
    )
    monkeypatch.setattr(_finish, "decision_llm_client", lambda run: None)

    state = _finish._base_state(run)

    assert _status(state, "adr")["last_run_commit"] == head
    assert _status(state, "inline_marker")["last_run"] is not None
