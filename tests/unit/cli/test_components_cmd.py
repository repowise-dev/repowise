"""``repowise components check`` and ``list`` on a repository with no index."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli

_CATALOG = """\
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: api
  annotations:
    repowise.dev/paths: src/api/**
    repowise.dev/criticality: high
    repowise.dev/tags: pci
spec: {type: service, lifecycle: production, owner: group:default/payments}
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for rel in ("src/api/routes.py", "src/db/models.py", "README.md"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def _invoke(args: list[str]) -> object:
    return CliRunner().invoke(cli, args, catch_exceptions=False)


def _write_catalog(repo: Path, text: str) -> None:
    (repo / ".repowise").mkdir(exist_ok=True)
    (repo / ".repowise" / "components.yaml").write_text(text, encoding="utf-8")


def test_check_with_no_catalog_passes(repo):
    result = _invoke(["components", "check", "--path", str(repo)])
    assert result.exit_code == 0
    assert "No catalog file found" in result.output


def test_check_passes_a_valid_catalog(repo):
    _write_catalog(repo, _CATALOG)
    result = _invoke(["components", "check", "--path", str(repo), "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["errors"] == 0 and data["components"] == 1
    assert data["sources"] == [".repowise/components.yaml"]


def test_check_exits_non_zero_on_errors(repo):
    _write_catalog(
        repo,
        _CATALOG.replace("src/api/**", "src/gone/**")
        + "---\napiVersion: backstage.io/v1alpha1\nkind: Widget\nmetadata: {name: w}\n",
    )
    result = _invoke(["components", "check", "--path", str(repo), "--format", "json"])
    assert result.exit_code == 1
    messages = [i["message"] for i in json.loads(result.output)["issues"]]
    assert "glob 'src/gone/**' matches no files" in messages
    assert any(m.startswith("unknown kind 'Widget'") for m in messages)

    table = _invoke(["components", "check", "--path", str(repo)])
    assert table.exit_code == 1
    assert "matches no files" in table.output


def test_list_without_an_index_uses_directories_and_the_catalog(repo):
    _write_catalog(repo, _CATALOG)
    result = _invoke(["components", "list", "--path", str(repo), "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    rows = {r["name"]: r for r in data["components"]}
    assert rows["api"] == {
        "name": "api",
        "source": "declared",
        "files": 1,
        "owner": "group:default/payments",
        "criticality": "high",
        "tags": ["pci"],
    }
    # Only the layout directory "src" divides the repo, so the derived component
    # is the whole repo, keeping the files the declared component did not claim.
    (whole,) = [r for r in data["components"] if r["source"] == "repo"]
    assert whole["files"] == 2 and "src" not in rows
    assert data["catalog_errors"] == 0


def test_ownership_without_index_checks_declared_facts(repo):
    _write_catalog(
        repo,
        _CATALOG
        + """---
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: ledger
  annotations:
    repowise.dev/paths: src/ledger/**
    repowise.dev/criticality: critical
spec: {type: service, lifecycle: production, owner: group:default/money}
""",
    )
    result = _invoke(["components", "ownership", "--path", str(repo), "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["indexed"] is False
    by_name = {c["name"]: c for c in data["components"]}
    # Activity is unknown without an index, so the owned api component has no finding.
    assert by_name["api"]["findings"] == []
    assert by_name["api"]["declared_teams"][0]["activity"] == "unknown"
    assert [f["kind"] for f in by_name["ledger"]["findings"]] == ["catalog_entry_no_code"]

    text = _invoke(["components", "ownership", "--path", str(repo)])
    assert "catalog_entry_no_code ledger" in text.output
    # The text form says what the check could see, so no findings never reads as all clear.
    assert "component(s) have a declared owner" in text.output


def test_ownership_reports_a_bad_teams_file_once_without_its_content(repo):
    _write_catalog(repo, _CATALOG)
    (repo / ".repowise" / "teams.yaml").write_text(
        'members: ["hidden@acme.com", [\n', encoding="utf-8"
    )
    result = _invoke(["components", "ownership", "--path", str(repo), "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data["directory_issues"]) == 1
    assert all("directory_issues" not in c for c in data["components"])
    assert "hidden" not in result.output
