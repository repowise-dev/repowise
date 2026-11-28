"""`decision show` and `changes show` print the tracker issues stored for their commits."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.commands.changes_cmd import changes_group
from repowise.cli.main import cli
from tests.unit.cli.test_changes_cmd import HEAD
from tests.unit.cli.test_changes_cmd import _repo as _changes_repo
from tests.unit.cli.test_decision_cmd import _REPO_ID, _create_empty_index, _seed_wiki_db

_SHA = "d" * 40


def _store_link(root: Path, repo_id: str, sha: str, key: str, **over: str) -> None:
    row = {"type": "bug", "status": "Done", "title": "Checkout fails", "url": ""} | over
    with sqlite3.connect(root / ".repowise" / "wiki.db") as db:
        db.execute(
            "INSERT INTO issue_links (repository_id, commit_sha, issue_key, source, issue_type,"
            " status, title, url, basis, fetched_at)"
            " VALUES (?, ?, ?, 't', ?, ?, ?, ?, '', '2026-11-01 00:00:00')",
            (repo_id, sha, key, row["type"], row["status"], row["title"], row["url"]),
        )


def _decision_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _create_empty_index(root)
    _seed_wiki_db(
        root,
        [{"id": "a" * 32, "title": "Prefer SQLite locally", "evidence_commits": [_SHA[:8]]}],
    )
    return root


def _show(root: Path, *extra: str) -> str:
    result = CliRunner().invoke(cli, ["decision", "show", "aaaaaaaa", str(root), *extra])
    assert result.exit_code == 0, result.output
    return result.output


def test_decision_show_is_unchanged_without_a_link(tmp_path: Path) -> None:
    root = _decision_repo(tmp_path)
    table, payload = _show(root), _show(root, "--format", "json")
    _store_link(root, _REPO_ID, "e" * 40, "ENG-1")

    assert _show(root) == table
    assert _show(root, "--format", "json") == payload
    assert "linked_issues" not in payload


def test_decision_show_lists_the_issue_its_evidence_commit_links_to(tmp_path: Path) -> None:
    root = _decision_repo(tmp_path)
    _store_link(root, _REPO_ID, _SHA, "ENG-42", url="https://tracker.example/ENG-42")

    assert "ENG-42 (bug, Done): Checkout fails  https://tracker.example/ENG-42" in _show(root)
    record = json.loads(_show(root, "--format", "json"))["decision"]
    assert record["linked_issues"] == [
        {
            "key": "ENG-42",
            "type": "bug",
            "status": "Done",
            "title": "Checkout fails",
            "url": "https://tracker.example/ENG-42",
        }
    ]


def _repo_id(root: Path) -> str:
    with sqlite3.connect(root / ".repowise" / "wiki.db") as db:
        return db.execute("SELECT id FROM repositories").fetchone()[0]


def _change(root: Path, fmt: str) -> str:
    args = ["show", "pr-5", "--path", str(root), "--no-evidence", "--format", fmt]
    result = CliRunner().invoke(changes_group, args)
    assert result.exit_code == 0, result.output
    return result.output


def test_changes_show_is_unchanged_without_a_link(tmp_path: Path) -> None:
    root = _changes_repo(tmp_path)
    table, record = _change(root, "table"), _change(root, "json")
    _store_link(root, _repo_id(root), "e" * 40, "ENG-1")

    assert _change(root, "table") == table
    assert _change(root, "json") == record


def test_changes_show_quotes_the_issue_on_its_commit(tmp_path: Path) -> None:
    root = _changes_repo(tmp_path)
    _store_link(root, _repo_id(root), HEAD, "ENG-42")

    assert "linked issues: ENG-42 (bug, Done): Checkout fails" in _change(root, "table")
    record = json.loads(_change(root, "json"))
    assert record["metadata"]["linked_issues"] == ["ENG-42 (bug, Done): Checkout fails"]
