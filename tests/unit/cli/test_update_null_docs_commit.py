"""A null pointer in ``state.json`` must not strand the store.

``get_head_commit`` returns ``None`` whenever ``git rev-parse HEAD`` fails, and
``update`` wrote that straight onto both pointers. The next run then read the
null as its base and refused with "No previous sync found" against a store that
had indexed fine. #1507 guarded the same write in ``generate``; ``update`` kept
it.

The null is also unrecoverable once written, because both readers test for an
absent key rather than a falsy value: the base-ref lookup used ``dict.get``'s
default, which only applies when the key is missing, and the migration that
backfills the docs pointer guarded on ``not in state``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.cli.commands.update_cmd import persistence as update_persistence


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.t")
    _git(path, "config", "user.name", "t")
    (path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "first")
    return path


def test_a_null_docs_commit_is_backfilled_from_the_sync_pointer() -> None:
    """The real migration repairs a null, not only a missing key.

    This is the path that should have healed a store on its next update and
    instead left it stuck: ``"last_docs_commit" not in state`` is False when
    the key is present and null, so the repair never ran.
    """
    state = {"last_sync_commit": "4b13948", "last_docs_commit": None}
    new_state: dict = {}

    update_persistence.backfill_docs_pointer(new_state, state)

    assert new_state["last_docs_commit"] == "4b13948"


def test_a_real_docs_commit_is_left_alone() -> None:
    state = {"last_sync_commit": "4b13948", "last_docs_commit": "09b9737"}
    new_state: dict = {}

    update_persistence.backfill_docs_pointer(new_state, state)

    assert "last_docs_commit" not in new_state


def test_an_unindexed_store_gets_no_pointer() -> None:
    new_state: dict = {}

    update_persistence.backfill_docs_pointer(new_state, {})

    assert new_state == {}


def test_update_recovers_a_store_whose_docs_pointer_is_null(repo: Path, monkeypatch) -> None:
    """End to end: a store carrying an explicit null still knows its base.

    HEAD has to be ahead of the pointer, or ``update`` short-circuits on
    "Already up to date" long before it reads a base ref.
    """
    from click.testing import CliRunner

    from repowise.cli.helpers import save_state
    from repowise.cli.main import cli

    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    (repo / ".repowise").mkdir()
    save_state(
        repo,
        {
            "last_sync_commit": _git(repo, "rev-parse", "HEAD"),
            "last_docs_commit": None,
            "docs_mode": "llm",
            "store_format_version": 2,
        },
    )
    (repo / "b.py").write_text("y = 2", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "second")

    result = CliRunner().invoke(cli, ["update", "--no-workspace", "--docs", str(repo)])

    assert "No previous sync found" not in (result.output or "")
