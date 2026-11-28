"""``init`` and ``update`` with ``contracts.in_repo`` off (the default) and on."""

from __future__ import annotations

import shutil
import subprocess

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.core.workspace import repo_contracts


def _git(cwd, *args) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@e.x", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def git_repo(tmp_path, sample_repo_path, monkeypatch):
    dest = tmp_path / "gitrepo"
    shutil.copytree(sample_repo_path, dest)
    db_path = dest / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    _git(dest, "init", "-q")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-qm", "init")
    return dest


def _index(repo) -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["init", str(repo), "--index-only"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    (repo / "utils" / "extra.py").write_text("def extra():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "more")
    r = runner.invoke(cli, ["update", str(repo), "--index-only"], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def test_off_init_and_update_never_extract(git_repo, monkeypatch) -> None:
    async def _never(*_a, **_k):
        raise AssertionError("extraction ran with contracts.in_repo off")

    monkeypatch.setattr(repo_contracts, "run_repo_contract_extraction", _never)
    _index(git_repo)

    assert not repo_contracts.repo_contracts_path(git_repo).exists()


def test_on_init_and_update_write_the_repo_contracts(git_repo) -> None:
    (git_repo / ".repowise" / "config.yaml").write_text("contracts:\n  in_repo: true\n")
    _index(git_repo)

    path = repo_contracts.repo_contracts_path(git_repo)
    assert path.is_file()
    assert "gitrepo" in path.read_text(encoding="utf-8")
