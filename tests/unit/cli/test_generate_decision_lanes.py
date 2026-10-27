"""``generate`` and ``update --full`` price, run and settle the decision lanes a keyless init skipped."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from repowise.cli.commands import decision_lanes as cli_lanes
from repowise.cli.commands import status_cmd, upgrade_flow
from repowise.cli.commands.generate_cmd import command as generate_command
from repowise.cli.helpers import load_state, save_state
from repowise.core.cost_estimator import PageTypePlan
from repowise.core.decision_lanes import (
    PENDING_DECISION_LANES_KEY,
    DecisionLaneOutcome,
    DecisionLanePlan,
)

LANES = ("pr", "git_archaeology", "comment")
MOCK = SimpleNamespace(provider_name="mock", model_name="mock")


def _capture(monkeypatch: pytest.MonkeyPatch, *modules) -> io.StringIO:
    buf = io.StringIO()
    console = Console(file=buf, width=200, force_terminal=False)
    for module in modules:
        monkeypatch.setattr(module, "console", console)
    return buf


def _gate(**kwargs) -> bool:
    gate = generate_command._make_gate(kwargs["dry_run"])
    return gate(
        [PageTypePlan(page_type="module_page", count=3, level=0)],
        kwargs["provider"],
        Path("."),
        yes=True,
        dry_run=kwargs["dry_run"],
        decisions=DecisionLanePlan(LANES, calls=4),
    )


def test_keyless_dry_run_names_the_decision_lanes_on_their_own_line(monkeypatch) -> None:
    buf = _capture(monkeypatch, generate_command, cli_lanes)
    assert _gate(provider=None, dry_run=True) is False
    out = buf.getvalue()
    assert "Writing 3 pages" in out
    # 4 calls at the decision-call heuristic of 7,500 tokens each.
    assert "Decisions from PRs, history and comments: about 30,000 tokens (4 model calls)." in out
    assert "there is no price" in out
    assert "Dry run, nothing generated." in out


def test_priced_dry_run_includes_the_decision_lanes(monkeypatch) -> None:
    buf = _capture(monkeypatch, generate_command, cli_lanes)
    assert _gate(provider=MOCK, dry_run=True) is False
    out = buf.getvalue()
    assert "Decisions from PRs, history and comments: about 30,000 tokens (4 model calls), $0.00." in out
    assert "Estimated" in out


def test_generate_state_write_clears_the_lanes_that_ran(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()
    state = {PENDING_DECISION_LANES_KEY: list(LANES)}
    outcome = SimpleNamespace(
        total_pages=1,
        remaining_template_pages=0,
        decision_lanes=DecisionLaneOutcome(found=2, still_pending=("comment",)),
    )
    monkeypatch.setattr(generate_command, "get_head_commit", lambda _p: None)
    generate_command._write_state(tmp_path, state, MOCK, outcome)
    assert load_state(tmp_path)[PENDING_DECISION_LANES_KEY] == ["comment"]

    outcome.decision_lanes = DecisionLaneOutcome(found=0, still_pending=())
    generate_command._write_state(tmp_path, state, MOCK, outcome)
    assert PENDING_DECISION_LANES_KEY not in load_state(tmp_path)


def test_update_full_settles_the_lanes_its_run_reports(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    (repo / ".repowise").mkdir()
    save_state(repo, {"run_mode": "standard", "docs_mode": "deterministic",
                      PENDING_DECISION_LANES_KEY: list(LANES)})
    monkeypatch.setattr(upgrade_flow, "resolve_provider", lambda *a, **k: MOCK)
    monkeypatch.setattr(upgrade_flow, "try_acquire_update_lock", lambda *a, **k: None)
    monkeypatch.setattr(upgrade_flow, "release_update_lock", lambda *a, **k: None)

    async def succeed(*_a, **_k):
        return [], 0, {
            "git_history_coverage": None,
            "file_pages": {"configured_cap": None},
            "search": {"full_text": "available", "semantic": "unavailable", "next_command": None},
            "embedding_error": None,
            "decision_lanes_pending": (),
        }

    monkeypatch.setattr(upgrade_flow, "_run_upgrade", succeed)
    upgrade_flow.upgrade_to_full(
        repo, provider_name=None, model=None, reasoning=None, concurrency=1, yes=True
    )
    assert PENDING_DECISION_LANES_KEY not in load_state(repo)


def _status(monkeypatch, tmp_path: Path, state: dict, *args: str):
    from click.testing import CliRunner

    (tmp_path / ".repowise").mkdir(exist_ok=True)
    buf = _capture(monkeypatch, status_cmd)
    monkeypatch.setattr(status_cmd, "load_state", lambda _p: state)
    monkeypatch.setattr(status_cmd, "load_config", lambda _p: {})
    monkeypatch.setattr(status_cmd, "db_configured", lambda: False)
    result = CliRunner().invoke(
        status_cmd.status_command, [str(tmp_path), "--no-workspace", *args]
    )
    assert result.exit_code == 0, result.output
    return buf.getvalue(), result.output


def test_status_names_pending_lanes_and_the_command_that_runs_them(monkeypatch, tmp_path) -> None:
    out, _ = _status(monkeypatch, tmp_path, {PENDING_DECISION_LANES_KEY: list(LANES)})
    assert "PRs, history and comments not read yet (needs a model): repowise generate" in out


def test_status_names_the_fast_to_full_command_for_lanes_on_an_index_only_repo(
    monkeypatch, tmp_path
) -> None:
    # `generate` refuses a repo with no documentation.
    state = {PENDING_DECISION_LANES_KEY: list(LANES), "docs_mode": "none", "run_mode": "fast"}
    out, _ = _status(monkeypatch, tmp_path, state)
    assert "(needs a model): repowise update --full" in out
    assert "repowise generate" not in out


def test_status_is_quiet_once_the_lanes_ran(monkeypatch, tmp_path) -> None:
    out, _ = _status(monkeypatch, tmp_path, {"docs_mode": "llm"})
    assert "not read yet" not in out


def test_status_json_carries_the_pending_lanes(monkeypatch, tmp_path) -> None:
    import json

    _, raw = _status(
        monkeypatch, tmp_path, {PENDING_DECISION_LANES_KEY: ["pr"]}, "--format", "json"
    )
    assert json.loads(raw)["decision_lanes_pending"] == ["pr"]


def test_status_json_names_generate_for_a_repo_built_from_code(monkeypatch, tmp_path) -> None:
    import json

    _, raw = _status(monkeypatch, tmp_path, {"docs_mode": "deterministic"}, "--format", "json")
    payload = json.loads(raw)
    # `upgrade_command` stays the Index only signal; the model step is named per mode.
    assert payload["upgrade_command"] is None
    assert payload["model_command"] == "repowise generate"


def test_keyless_init_records_the_lanes_a_model_init_does_not(tmp_path: Path) -> None:
    from repowise.cli.commands.init_cmd._ingest import decision_llm_client

    opts = SimpleNamespace(dry_run=False, index_only=True)
    assert decision_llm_client(SimpleNamespace(opts=opts, provider=MOCK, decision_provider=None)) is None
    opts = SimpleNamespace(dry_run=False, index_only=False)
    assert decision_llm_client(SimpleNamespace(opts=opts, provider=MOCK, decision_provider=None)) is MOCK
    opts = SimpleNamespace(dry_run=True, index_only=False)
    assert decision_llm_client(SimpleNamespace(opts=opts, provider=MOCK, decision_provider=None)) is None


async def test_a_lane_run_that_raises_keeps_every_lane_pending(monkeypatch) -> None:
    """Pages written before the lanes keep their state write when the lanes fail."""
    buf = _capture(monkeypatch, cli_lanes)

    async def boom(*_a, **_k):
        raise RuntimeError("store locked")

    monkeypatch.setattr(cli_lanes, "run_decision_lanes", boom)
    plan = DecisionLanePlan(LANES, calls=2)
    outcome = await cli_lanes.run_and_report_lanes(
        None, "repo-1", Path("."), plan, provider=SimpleNamespace(), inputs=None
    )
    assert outcome.still_pending == LANES
    assert "store locked" in buf.getvalue()
