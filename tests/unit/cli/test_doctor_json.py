"""Tests for ``repowise doctor --format json``."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.commands.doctor_cmd import DoctorCheck, _check, doctor_command


def _git_init(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(p), capture_output=True)


class TestCheckCarriesOk:
    def test_check_returns_named_tuple_with_ok(self) -> None:
        c = _check("Some check", True, "all good")
        assert isinstance(c, DoctorCheck)
        assert c.name == "Some check"
        assert c.ok is True
        assert c.detail == "all good"

    def test_check_failure(self) -> None:
        c = _check("Some check", False, "broken")
        assert c.ok is False


class TestDoctorFormatJson:
    def test_json_output_on_unindexed_repo(self, tmp_path: Path) -> None:
        _git_init(tmp_path)

        result = CliRunner().invoke(
            doctor_command, [str(tmp_path), "--no-workspace", "--format", "json"]
        )

        # Doctor should still report cleanly even though checks fail.
        assert result.exit_code == 1, result.output
        payload = json.loads(result.output[result.output.index("{") :])
        assert payload["ok"] is False
        names = {c["name"] for c in payload["checks"]}
        assert ".repowise/ directory" in names
        failing = [c for c in payload["checks"] if c["name"] == ".repowise/ directory"]
        assert failing[0]["ok"] is False
        # Detail strings must be plain text: no Rich markup leaking through.
        for c in payload["checks"]:
            assert "[green]" not in c["detail"]
            assert "[red]" not in c["detail"]

    def test_repair_with_json_is_a_usage_error(self, tmp_path: Path) -> None:
        _git_init(tmp_path)

        result = CliRunner().invoke(
            doctor_command,
            [str(tmp_path), "--no-workspace", "--format", "json", "--repair"],
        )

        assert result.exit_code != 0
        assert "--repair" in result.output
        assert "json" in result.output.lower()
