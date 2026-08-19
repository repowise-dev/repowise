"""CLI coverage for change-risk exclusion rules."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.commands.risk_cmd import risk_command


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(["add", relative_path], repo)
    _git(["-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-m", message], repo)


def test_risk_uses_root_riskignore_and_repeatable_excludes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    _commit(
        repo,
        {
            "src/app.py": "value = 1\n",
            "tests/test_app.py": "def test_value():\n    assert True\n",
            "web/app.spec.ts": "it('works', () => {})\n",
        },
        "feat: app",
    )
    (repo / ".riskignore").write_text("tests/\n", encoding="utf-8")

    result = CliRunner().invoke(
        risk_command,
        [
            "HEAD",
            "--path",
            str(repo),
            "--baseline",
            "0",
            "-x",
            "*.spec.ts",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["features"]["nf"] == 1
    assert payload["features"]["la"] == 1
    assert payload["exclude_patterns"] == ["tests/", "*.spec.ts"]


def test_risk_help_describes_repeatable_exclude() -> None:
    result = CliRunner().invoke(risk_command, ["--help"])

    assert result.exit_code == 0
    assert "-x, --exclude PATTERN" in result.output
    assert "Repeatable" in result.output
