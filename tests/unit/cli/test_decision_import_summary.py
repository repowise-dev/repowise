"""``decision import``'s text summary names the entries the file re-accepted.

An edit to an accepted decision's wording is re-accepted on import; without a
``reaffirmed`` count and line the run reads as a no-op.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.core.analysis.decisions.manifest import ManifestDecision, write_manifest

from .test_decision_cmd import _seed_wiki_db

DECISION = "a" * 32


def _import(repo: Path, *args: str) -> str:
    result = CliRunner().invoke(cli, ["decision", "import", str(repo), *args])
    assert result.exit_code == 0, result.output
    return result.output


def test_an_edited_entry_is_listed_as_reaffirmed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_wiki_db(repo, [])
    entry = ManifestDecision(
        id=DECISION,
        title="Use X",
        decision="use X",
        reason="because Y",
        scope=["src/app.py"],
        accepted_at="2026-09-01T00:00:00+00:00",
        accepted_by="tester",
    )
    write_manifest(repo, [entry])
    assert "created 1" in _import(repo)

    write_manifest(repo, [replace(entry, reason="a colleague corrected this")])
    payload = json.loads(_import(repo, "--dry-run", "--format", "json"))
    text = _import(repo)

    assert payload["reaffirmed"] == [DECISION]
    assert "reaffirmed 1" in text
    assert f"reaffirmed {DECISION[:8]}" in text
