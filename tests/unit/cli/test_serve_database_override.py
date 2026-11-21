"""Local database discovery preserves an explicit database URL."""

from __future__ import annotations

import os

from repowise.cli.commands.serve_cmd.command import _resolve_db_url
from repowise.core.persistence.database import resolve_db_url


def test_local_directory_does_not_override_database_url(monkeypatch, tmp_path):
    (tmp_path / ".repowise").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.setenv("REPOWISE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    _resolve_db_url()

    assert "REPOWISE_DB_URL" not in os.environ
    assert resolve_db_url() == "sqlite+aiosqlite:///:memory:"


def test_nested_non_member_repo_serves_its_own_database(monkeypatch, tmp_path):
    """A repo cloned under a workspace it is not a member of serves its own index."""
    (tmp_path / ".repowise-workspace.yaml").write_text("version: 1\nrepos: []\n")
    clone = tmp_path / "test-repos" / "clone"
    (clone / ".git").mkdir(parents=True)
    (clone / ".repowise").mkdir()
    monkeypatch.chdir(clone)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)

    _resolve_db_url()

    assert os.environ.pop("REPOWISE_DB_URL").endswith("test-repos/clone/.repowise/wiki.db")
