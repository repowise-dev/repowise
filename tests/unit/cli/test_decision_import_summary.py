"""``decision import``'s text summary names the entries the file re-accepted.

An edit to an accepted decision's wording is re-accepted on import; without a
``reaffirmed`` count and line the run reads as a no-op.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.core.analysis.decisions.manifest import (
    ManifestDecision,
    load_manifest,
    write_manifest,
)

from .test_decision_cmd import _seed_wiki_db

DECISION = "a" * 32


def _import(repo: Path, *args: str) -> str:
    result = CliRunner().invoke(cli, ["decision", "import", str(repo), *args])
    assert result.exit_code == 0, result.output
    return result.output


def _seed_manifest(tmp_path: Path) -> tuple[Path, ManifestDecision]:
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
    return repo, entry


def _export(repo: Path) -> ManifestDecision:
    result = CliRunner().invoke(cli, ["decision", "export", str(repo)])
    assert result.exit_code == 0, result.output
    return load_manifest(repo)[0][0]


@pytest.mark.parametrize("with_token", [False, True], ids=["legacy", "matching-token"])
def test_an_edited_entry_is_listed_as_reaffirmed(tmp_path: Path, with_token: bool) -> None:
    repo, entry = _seed_manifest(tmp_path)
    if with_token:
        entry = _export(repo)
        assert entry.base_token

    write_manifest(repo, [replace(entry, reason="a colleague corrected this")])
    payload = json.loads(_import(repo, "--dry-run", "--format", "json"))
    text = _import(repo)

    assert payload["reaffirmed"] == [DECISION]
    assert "reaffirmed 1" in text
    assert f"reaffirmed {DECISION[:8]}" in text
    # Only a legacy entry, with no token to compare, is flagged as unchecked.
    assert payload["unchecked"] == ([] if with_token else [DECISION])
    assert ("not checked for staleness" in text) is (not with_token)


def test_a_stale_entry_is_listed_as_skipped_until_forced(tmp_path: Path) -> None:
    repo, _ = _seed_manifest(tmp_path)
    exported = _export(repo)
    write_manifest(repo, [replace(exported, reason="A newer accepted reason")])
    assert "reaffirmed 1" in _import(repo)
    write_manifest(repo, [replace(exported, title="An edit based on the old export")])

    payload = json.loads(_import(repo, "--dry-run", "--format", "json"))
    text = _import(repo)

    assert payload["skipped"] == [
        {"id": DECISION, "reason": "changed in the store since this file was exported"}
    ]
    assert payload["dry_run"] is True
    assert "skipped 1" in text
    assert f"skipped {DECISION[:8]}" in text
    assert "changed in the store since this file was exported" in " ".join(text.split())

    preview = json.loads(_import(repo, "--force", "--dry-run", "--format", "json"))
    assert preview["reaffirmed"] == [DECISION]
    assert not preview["skipped"]
    assert "skipped 1" in _import(repo)
    assert "reaffirmed 1" in _import(repo, "--force")
    assert "unchanged 1" in _import(repo)
    accepted = _export(repo)
    assert accepted.title == "An edit based on the old export"
    assert accepted.reason == exported.reason
