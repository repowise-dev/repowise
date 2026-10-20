"""``repowise health --score`` scores files from disk, with or without an index."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from repowise.cli.commands.health_cmd import command as health_module
from repowise.cli.main import cli


def _complex(name: str, branches: int) -> str:
    lines = [f"def {name}(value):"]
    for i in range(branches):
        lines.append(f"    if value % {i + 2} == 0 and value > {i - 1}:")
        lines.append(f"        value = value + {i}")
    lines.append("    return value")
    return "\n".join(lines) + "\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _invoke(repo: Path, *args: str):
    with patch.object(health_module, "resolve_command_target") as resolve:
        resolve.return_value = SimpleNamespace(
            is_workspace=False, repo_path=repo, notice=lambda *a, **k: None
        )
        return CliRunner().invoke(cli, ["health", *args])


def test_score_compares_the_file_on_disk_with_a_revision(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "checkout", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    app = tmp_path / "app.py"
    app.write_text(_complex("tangle", 26), encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    app.write_text(_complex("tangle", 1), encoding="utf-8")

    result = _invoke(tmp_path, "--score", str(app), "--against", "HEAD", "--format", "json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout[result.stdout.index("{") :])
    assert payload["history_source"] == "none"
    row = payload["files"][0]
    assert row["path"] == "app.py"
    assert row["head_score"] > row["base_score"]
    assert payload["score_change"]["delta"] > 0


def test_against_needs_score(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "--against", "HEAD")
    assert result.exit_code != 0
    assert "--against needs --score" in result.output


def test_score_refuses_a_view_flag_and_a_path_outside_the_repo(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    repo = tmp_path / "repo"
    clash = _invoke(repo, "--score", str(repo / "a.py"), "--trend")
    assert clash.exit_code != 0
    assert "--score does not combine with --trend" in clash.output
    outside = _invoke(repo, "--score", str(tmp_path / "elsewhere.py"))
    assert outside.exit_code != 0
    assert "outside" in outside.output


def test_score_counts_stored_dry_violation_and_reports_scope(tmp_path: Path) -> None:
    from repowise.core.analysis.change_health.index_inputs import IndexInputs

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "checkout", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    a = tmp_path / "a.py"
    a.write_text(_complex("tangle", 20), encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")

    stored = SimpleNamespace(
        biomarker_type="dry_violation",
        severity="medium",
        function_name=None,
        line_start=1,
        line_end=15,
        details_json=json.dumps({"worst_clone_partner": "b.py", "worst_clone_lines": 15}),
        health_impact=0.5,
        reason="clone",
    )
    mock_inputs = IndexInputs(
        git_meta={"a.py": {"file_path": "a.py"}, "b.py": {"file_path": "b.py"}},
        dry_findings={"a.py": [stored]},
    )

    with patch("repowise.cli.commands.health_cmd.live_score._index_inputs", return_value=mock_inputs):
        # Table output says clones outside evaluated paths are not counted
        human = _invoke(tmp_path, "--score", str(a))
        assert human.exit_code == 0
        assert "Cross-file clones outside the evaluated paths are not counted" in human.output

        # JSON output reports cross_file_clones scope and counts the stored dry_violation
        res = _invoke(tmp_path, "--score", str(a), "--format", "json")
        assert res.exit_code == 0
        payload = json.loads(res.stdout[res.stdout.index("{") :])
        assert payload["cross_file_clones"] == "passed_paths_only"
        row = payload["files"][0]
        assert row["path"] == "a.py"
        assert row["head_score"] is not None

