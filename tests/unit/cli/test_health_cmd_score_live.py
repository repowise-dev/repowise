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
    _git(tmp_path, "init", "-q", "-b", "main")
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
    from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION

    assert payload["analyzer_version"] == HEALTH_ANALYZER_VERSION
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
