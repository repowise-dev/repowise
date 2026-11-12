"""The doctor Settings rows: config.yaml and environment values read through the registry."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from repowise.cli.commands.doctor_cmd import _repair, doctor_command
from repowise.cli.commands.doctor_cmd._checks import integrations, providers
from repowise.cli.commands.doctor_cmd._checks.context import CheckContext
from repowise.cli.commands.doctor_cmd._checks.settings import settings_rows
from repowise.cli.commands.doctor_cmd._types import _check
from repowise.core.settings import all_settings


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    (root / ".repowise").mkdir(parents=True)
    for s in all_settings():
        if s.env is not None and not s.env.prefix:
            monkeypatch.delenv(s.env.name, raising=False)
    return root


def _write(repo: Path, text: str) -> None:
    (repo / ".repowise" / "config.yaml").write_text(text, encoding="utf-8")


def _rows(repo: Path) -> tuple[CheckContext, dict]:
    ctx = CheckContext(repo_path=repo, repowise_dir=repo / ".repowise")
    return ctx, {r.id: r for r in settings_rows(ctx)}


def test_a_default_repo_shows_one_passing_row(repo) -> None:
    ctx, rows = _rows(repo)
    assert list(rows) == ["settings"]
    assert (rows["settings"].level, rows["settings"].detail) == (
        "ok",
        "every setting at its default",
    )
    assert ctx.invalid_settings == []


def test_valid_values_are_counted_in_the_passing_row(repo) -> None:
    # ``sources.pr: true`` is the short form of ``sources.pr.enabled``, not a block in error.
    _write(repo, "commit_limit: 500\ndecisions:\n  mode: full\n  sources:\n    pr: true\n")
    _ctx, rows = _rows(repo)
    assert list(rows) == ["settings"]
    assert rows["settings"].detail == "2 set in .repowise/config.yaml, all valid"


def test_a_value_of_the_wrong_type_fails_with_the_key_value_form_and_fix(repo) -> None:
    _write(repo, 'commit_limit: "lots"\n')
    ctx, rows = _rows(repo)
    row = rows["setting-commit-limit"]
    assert (row.level, row.ok, row.fix) == ("fail", False, "repowise config reset commit_limit")
    assert row.detail.startswith('commit_limit: "lots" in .repowise/config.yaml is not valid')
    assert "Accepts a whole number, at least 1" in row.detail
    assert "repowise config set commit_limit <value>" in row.detail
    assert "settings" not in rows
    assert ctx.invalid_settings == ["commit_limit"]


@pytest.mark.parametrize(
    ("text", "row_id", "accepts"),
    [
        ("max_tokens: -3\n", "setting-max-tokens", "more than 0"),
        ("events:\n  retention_days: 0\n", "setting-events-retention-days", "more than 0"),
        (
            "decisions:\n  discovery:\n    max_sessions: 0\n",
            "setting-decisions-discovery-max-sessions",
            "at least 1 and at most 24",
        ),
    ],
    ids=["max_tokens", "events", "decisions"],
)
def test_a_value_out_of_range_fails(repo, text, row_id, accepts) -> None:
    _write(repo, text)
    _ctx, rows = _rows(repo)
    assert rows[row_id].level == "fail"
    assert accepts in rows[row_id].detail


def test_an_unknown_key_warns_with_the_close_setting(repo) -> None:
    _write(repo, "comit_limit: 50\n")
    _ctx, rows = _rows(repo)
    row = rows["setting-comit-limit"]
    assert (row.level, row.ok) == ("warn", True)
    assert "is not a setting repowise reads" in row.detail
    assert row.detail.endswith("did you mean commit_limit?")


def test_an_unknown_key_in_a_block_does_not_suggest_its_block(repo) -> None:
    _write(repo, "events:\n  kinds: {nope: true}\nretired_knob: 1\n")
    _ctx, rows = _rows(repo)
    assert "did you mean" not in rows["setting-events-kinds-nope"].detail
    assert "did you mean" not in rows["setting-retired-knob"].detail


def test_a_block_written_as_one_value_warns(repo) -> None:
    _write(repo, "events: true\n")
    _ctx, rows = _rows(repo)
    assert rows["setting-events"].level == "warn"
    assert "keep their defaults" in rows["setting-events"].detail


def test_an_unreadable_decisions_block_fails_once_without_a_reset(repo) -> None:
    _write(repo, "decisions: 3\n")
    ctx, rows = _rows(repo)
    assert [r.level for r in rows.values()] == ["fail"]
    assert "`decisions:` is not a mapping" in next(iter(rows.values())).detail
    assert ctx.invalid_settings == []


def test_an_invalid_environment_value_fails_and_is_never_reset(repo, monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_REASONING", "bogus")
    ctx, rows = _rows(repo)
    row = rows["setting-repowise-reasoning"]
    assert (row.level, row.fix) == ("fail", None)
    assert row.detail.startswith('REPOWISE_REASONING="bogus" in the environment is not valid')
    assert "Unset REPOWISE_REASONING" in row.detail
    assert ctx.invalid_settings == []


def test_an_environment_variable_overriding_the_file_is_information(repo, monkeypatch) -> None:
    _write(repo, "hooks:\n  read_skeleton: true\n")
    monkeypatch.setenv("REPOWISE_HOOK_READ_SKELETON", "0")
    _ctx, rows = _rows(repo)
    assert list(rows) == ["settings", "setting-repowise-hook-read-skeleton"]
    row = rows["setting-repowise-hook-read-skeleton"]
    assert row.level == "ok"
    assert row.detail == (
        "REPOWISE_HOOK_READ_SKELETON=false overrides hooks.read_skeleton: true "
        "in .repowise/config.yaml until it is unset"
    )


def test_a_file_value_that_wins_over_a_set_variable_is_information(repo, monkeypatch) -> None:
    _write(repo, "provider: anthropic\n")
    monkeypatch.setenv("REPOWISE_PROVIDER", "openai")
    _ctx, rows = _rows(repo)
    assert rows["setting-repowise-provider"].level == "ok"
    assert "wins over it" in rows["setting-repowise-provider"].detail


def test_an_agreeing_variable_adds_no_row(repo, monkeypatch) -> None:
    _write(repo, "provider: anthropic\n")
    monkeypatch.setenv("REPOWISE_PROVIDER", "anthropic")
    _ctx, rows = _rows(repo)
    assert list(rows) == ["settings"]


def test_a_config_that_does_not_parse_fails(repo) -> None:
    _write(repo, "commit_limit: [unclosed\n")
    _ctx, rows = _rows(repo)
    assert list(rows) == ["settings"]
    assert rows["settings"].level == "fail"


def test_repair_resets_invalid_file_values_and_keeps_the_rest(repo, monkeypatch) -> None:
    _write(repo, 'commit_limit: "lots"\nmax_tokens: -3\nfollow_renames: false\n')
    monkeypatch.setenv("REPOWISE_REASONING", "bogus")
    printed: list[str] = []
    monkeypatch.setattr(
        _repair.console, "print", lambda *a, **_k: printed.append(" ".join(map(str, a)))
    )
    ctx, _ = _rows(repo)
    assert sorted(ctx.invalid_settings) == ["commit_limit", "max_tokens"]
    _repair.run_repair(ctx, repo_ok=False)
    config = yaml.safe_load((repo / ".repowise" / "config.yaml").read_text(encoding="utf-8"))
    assert config == {"follow_renames": False}
    assert not any("Nothing" in line for line in printed)
    _ctx, rows = _rows(repo)
    assert "setting-commit-limit" not in rows and "setting-max-tokens" not in rows
    assert rows["setting-repowise-reasoning"].level == "fail"


def test_repair_leaves_settings_with_an_area_writer_alone(repo) -> None:
    # The decisions writer rewrites the whole block, which could drop valid neighbours.
    _write(repo, "decisions:\n  preset: nope\n  sources:\n    adr: false\n")
    ctx, rows = _rows(repo)
    assert rows["setting-decisions-preset"].fix == "repowise config reset decisions.preset"
    assert ctx.invalid_settings == []


def test_json_carries_the_settings_rows_in_the_doctor_shape(repo, monkeypatch) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    integrations._mcp_responds_check.cache_clear()
    monkeypatch.setattr(integrations, "_registered_mcp_entry", lambda: None)
    monkeypatch.setattr(
        providers,
        "_hosted_account_check",
        lambda: _check("Hosted account", True, "", check_id="hosted-account"),
    )
    _write(repo, 'commit_limit: "lots"\ncomit_limit: 5\n')
    result = CliRunner().invoke(doctor_command, [str(repo), "--no-workspace", "--format", "json"])
    integrations._mcp_responds_check.cache_clear()
    payload = json.loads(result.output[result.output.index("{") :])
    assert payload["ok"] is False
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["setting-commit-limit"] == {
        "name": "Settings",
        "ok": False,
        "detail": by_id["setting-commit-limit"]["detail"],
        "id": "setting-commit-limit",
        "level": "fail",
        "fix": "repowise config reset commit_limit",
    }
    assert by_id["setting-comit-limit"]["level"] == "warn"
    assert "settings" not in by_id
