"""A keyless init gives a FastAPI route file its api_contract page, and the
first update keeps it there instead of trading it for a file page."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.helpers import release_update_lock
from repowise.cli.main import cli

_INIT = (
    "init",
    "--embedder",
    "mock",
    "--no-editor-setup",
    "--no-hook",
    "--no-agents",
    "--no-codex",
    "--no-claude-md",
    "--no-cost-tracking",
    "--no-workspace",
    "--no-prose",
    "-y",
)

_ROUTER = "app/routers/items.py"
_FILES = {
    "app/__init__.py": "",
    "app/routers/__init__.py": "",
    "app/main.py": (
        "from fastapi import FastAPI\n\n"
        "from app.routers import items\n\n"
        "app = FastAPI()\n"
        'app.include_router(items.router, prefix="/items")\n'
    ),
    _ROUTER: (
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n\n"
        '@router.get("/")\n'
        "def list_items():\n"
        "    return []\n"
    ),
}


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Api Contract",
        "GIT_AUTHOR_EMAIL": "api-contract@example.invalid",
        "GIT_COMMITTER_NAME": "Api Contract",
        "GIT_COMMITTER_EMAIL": "api-contract@example.invalid",
    }
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _write_and_commit(repo: Path, files: dict[str, str], message: str) -> None:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _invoke(runner: CliRunner, repo: Path, *args: str) -> None:
    try:
        result = runner.invoke(cli, [*args, str(repo)], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        release_update_lock(repo)


def _pages(repo: Path) -> set[tuple[str, str]]:
    with closing(sqlite3.connect(repo / ".repowise" / "wiki.db")) as conn:
        rows = conn.execute(
            "SELECT page_type, target_path FROM wiki_pages "
            "WHERE page_type IN ('api_contract', 'file_page')"
        ).fetchall()
    return {(page_type, path) for page_type, path in rows}


def test_keyless_init_writes_the_route_file_api_contract_page(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write_and_commit(repo, _FILES, "seed")
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")
    runner = CliRunner()

    _invoke(runner, repo, *_INIT)
    expected = {("api_contract", _ROUTER), ("file_page", "app/main.py")}
    assert _pages(repo) == expected

    edited = (
        _FILES[_ROUTER] + '\n\n@router.post("/")\ndef create_item(name: str):\n    return name\n'
    )
    _write_and_commit(repo, {_ROUTER: edited}, "feat: create items")
    _invoke(runner, repo, "update", "--no-workspace", "--no-agents")
    assert _pages(repo) == expected
