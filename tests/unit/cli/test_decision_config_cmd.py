"""``repowise decision config`` / ``source`` / ``llm`` contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from repowise.cli.main import cli


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / ".repowise").mkdir()
    # Keep provider availability deterministic: the rendered status must not
    # depend on which keys happen to be in the developer's environment.
    monkeypatch.setattr(
        "repowise.cli.commands.decision_config_cmd._provider_available", lambda _p: True
    )
    return tmp_path


def _run(*args) -> object:
    return CliRunner().invoke(cli, list(args))


def _config(repo: Path) -> dict:
    return yaml.safe_load((repo / ".repowise" / "config.yaml").read_text(encoding="utf-8"))


def _write(repo: Path, text: str) -> None:
    (repo / ".repowise" / "config.yaml").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# show / list
# ---------------------------------------------------------------------------


def test_show_json_lists_every_source(repo: Path):
    result = _run("decision", "config", "show", str(repo), "--format", "json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    keys = [s["key"] for s in payload["policy"]["sources"]]
    assert keys == [
        "inline_marker",
        "git_archaeology",
        "adr",
        "pr",
        "comment",
        "session",
        "session_discovery",
        "cli",
    ]
    assert payload["policy"]["preset"] == "default"


def test_show_table_renders_without_a_config(repo: Path):
    result = _run("decision", "config", "show", str(repo))

    assert result.exit_code == 0, result.output
    assert "Decision capture" in result.output
    assert "inline_marker" in result.output


def test_source_list_and_config_show_agree(repo: Path):
    a = _run("decision", "source", "list", str(repo), "--format", "json")
    b = _run("decision", "config", "show", str(repo), "--format", "json")

    assert json.loads(a.output) == json.loads(b.output)


def test_show_surfaces_a_legacy_key_and_an_unknown_source(repo: Path):
    _write(repo, "decisions:\n  session_mining: false\n  sources:\n    code_comment: false\n")

    result = _run("decision", "config", "show", str(repo), "--format", "json")

    payload = json.loads(result.output)
    assert payload["legacy_keys"] == ["session_mining"]
    assert any("code_comment" in w for w in payload["warnings"])
    session = next(s for s in payload["policy"]["sources"] if s["key"] == "session")
    assert session["enabled"] is False


def test_no_provider_reports_skipped_not_failed(repo: Path, monkeypatch):
    monkeypatch.setattr(
        "repowise.cli.commands.decision_config_cmd._provider_available", lambda _p: False
    )

    result = _run("decision", "config", "show", str(repo), "--format", "json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    by_key = {s["key"]: s for s in payload["policy"]["sources"]}
    assert payload["provider_available"] is False
    assert by_key["pr"]["status"] == "skipped_no_provider"
    assert by_key["adr"]["status"] == "deterministic_only"


# ---------------------------------------------------------------------------
# mutations
# ---------------------------------------------------------------------------


def test_llm_off_writes_the_master_switch(repo: Path):
    result = _run("decision", "llm", "--off", str(repo))

    assert result.exit_code == 0, result.output
    assert _config(repo)["decisions"]["llm"] is False


def test_llm_off_leaves_deterministic_sources_enabled(repo: Path):
    _run("decision", "llm", "--off", str(repo))

    payload = json.loads(
        _run("decision", "config", "show", str(repo), "--format", "json").output
    )
    by_key = {s["key"]: s for s in payload["policy"]["sources"]}
    assert by_key["adr"]["enabled"] is True
    assert by_key["adr"]["llm_enabled"] is False
    assert by_key["cli"]["status"] == "always_on"


def test_preset_writes_its_name(repo: Path):
    result = _run("decision", "config", "preset", "local_only", str(repo))

    assert result.exit_code == 0, result.output
    assert _config(repo)["decisions"]["preset"] == "local_only"


def test_an_edit_after_a_preset_drops_the_preset_name(repo: Path):
    _run("decision", "config", "preset", "balanced", str(repo))
    _run("decision", "source", "set", "comment", "--on", str(repo))

    assert "preset" not in _config(repo)["decisions"]
    payload = json.loads(
        _run("decision", "config", "show", str(repo), "--format", "json").output
    )
    assert payload["policy"]["preset"] == "custom"


def test_source_set_toggles_one_source(repo: Path):
    result = _run("decision", "source", "set", "pr", "--off", str(repo))

    assert result.exit_code == 0, result.output
    assert _config(repo)["decisions"]["sources"]["pr"] is False
    assert _config(repo)["decisions"]["sources"]["adr"] is True


def test_source_set_can_disable_only_the_model_stage(repo: Path):
    _run("decision", "source", "set", "adr", "--no-llm", str(repo))

    payload = json.loads(
        _run("decision", "config", "show", str(repo), "--format", "json").output
    )
    adr = next(s for s in payload["policy"]["sources"] if s["key"] == "adr")
    assert adr["enabled"] is True
    assert adr["llm_enabled"] is False
    assert adr["status"] == "deterministic_only"


def test_mutations_preserve_unrelated_config_keys(repo: Path):
    _write(repo, "provider: anthropic\nmodel: claude-x\n")

    _run("decision", "llm", "--off", str(repo))

    cfg = _config(repo)
    assert cfg["provider"] == "anthropic"
    assert cfg["model"] == "claude-x"


# ---------------------------------------------------------------------------
# dry run and errors
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(repo: Path):
    result = _run("decision", "config", "preset", "off", str(repo), "--dry-run")

    assert result.exit_code == 0, result.output
    assert not (repo / ".repowise" / "config.yaml").exists()


def test_dry_run_json_reports_the_changes(repo: Path):
    result = _run(
        "decision", "llm", "--off", str(repo), "--dry-run", "--format", "json"
    )

    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert {c["key"] for c in payload["changes"]} == {"llm"}


def test_dry_run_on_a_no_op_says_so(repo: Path):
    result = _run("decision", "llm", "--on", str(repo), "--dry-run")

    assert result.exit_code == 0, result.output
    assert "No change" in result.output


def test_source_set_without_a_switch_errors(repo: Path):
    result = _run("decision", "source", "set", "adr", str(repo))

    assert result.exit_code != 0
    assert "--on/--off" in result.output


def test_manual_entry_is_not_offered_as_a_switch(repo: Path):
    result = _run("decision", "source", "set", "cli", "--off", str(repo))

    assert result.exit_code != 0


def test_unknown_source_is_rejected(repo: Path):
    result = _run("decision", "source", "set", "code_comment", "--off", str(repo))

    assert result.exit_code != 0


def test_unknown_preset_is_rejected(repo: Path):
    result = _run("decision", "config", "preset", "aggressive", str(repo))

    assert result.exit_code != 0


def test_llm_with_neither_switch_errors(repo: Path):
    result = _run("decision", "llm", str(repo))

    assert result.exit_code != 0
    assert "--on" in result.output


def test_a_broken_config_is_a_clean_error_not_a_traceback(repo: Path):
    _write(repo, "decisions:\n\tsources: {}\n")

    result = _run("decision", "config", "show", str(repo))

    assert result.exit_code != 0
    assert "Could not parse" in result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# config discovery
# ---------------------------------------------------------------------------


def test_discovery_budget_writes_and_reports(repo: Path):
    result = _run(
        "decision", "config", "discovery", str(repo),
        "--max-sessions", "4", "--max-input-tokens", "9000",
        "--format", "json",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["policy"]["discovery"] == {
        "max_sessions": 4,
        "max_input_tokens": 9000,
    }
    assert _config(repo)["decisions"]["discovery"] == {
        "max_sessions": 4,
        "max_input_tokens": 9000,
    }


def test_discovery_budget_setting_one_field_keeps_the_other(repo: Path):
    _run("decision", "config", "discovery", str(repo), "--max-input-tokens", "9000")
    _run("decision", "config", "discovery", str(repo), "--max-sessions", "4")

    assert _config(repo)["decisions"]["discovery"] == {
        "max_sessions": 4,
        "max_input_tokens": 9000,
    }


def test_discovery_budget_with_no_flags_only_reports(repo: Path):
    result = _run("decision", "config", "discovery", str(repo), "--format", "json")

    assert result.exit_code == 0, result.output
    assert not (repo / ".repowise" / "config.yaml").exists()


def test_discovery_budget_rejects_an_out_of_range_value(repo: Path):
    result = _run("decision", "config", "discovery", str(repo), "--max-sessions", "0")

    assert result.exit_code != 0
    assert "between 1 and 24" in result.output


def test_discovery_budget_dry_run_writes_nothing(repo: Path):
    result = _run(
        "decision", "config", "discovery", str(repo), "--max-sessions", "4", "--dry-run"
    )

    assert result.exit_code == 0, result.output
    assert "discovery.max_sessions" in result.output
    assert not (repo / ".repowise" / "config.yaml").exists()


def test_discovery_budget_resolves_its_target_like_every_sibling(repo: Path, monkeypatch):
    """It must not bypass workspace/primary-repo redirection with its own resolve()."""
    seen: list[object] = []

    def _spy(path=None, **kw):
        seen.append(path)
        from repowise.cli.helpers import resolve_command_target

        return resolve_command_target(path=path, **kw)

    monkeypatch.setattr("repowise.cli.commands.decision_cmd.resolve_command_target", _spy)
    result = _run("decision", "config", "discovery", str(repo), "--max-sessions", "4")

    assert result.exit_code == 0, result.output
    assert seen == [str(repo)]


def test_switching_discovery_on_is_an_ordinary_source_toggle(repo: Path):
    result = _run("decision", "source", "set", "session_discovery", str(repo), "--on")

    assert result.exit_code == 0, result.output
    assert _config(repo)["decisions"]["sources"]["session_discovery"] is True
