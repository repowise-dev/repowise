"""`repowise serve` must not pin one database for the rest of a pytest session.

``serve_cmd`` assigns ``REPOWISE_DB_URL`` straight onto ``os.environ`` when a
``.repowise/`` sits beside the cwd. That is right for a real ``serve``: it is
one process that serves one store and exits. Inside pytest it is not. Many
commands share a process, ``resolve_db_url`` consults the environment *before*
the ``repo_path`` it is handed, and a raw assignment is not undone by anything.

A suite run therefore wrote hundreds of `repo` rows into the developer's own
``.repowise/wiki.db``, each pointing at a temp fixture directory, and every
later test that indexed a ``tmp_path`` repo silently measured that database
instead of its own.
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from repowise.cli.commands import serve_cmd

SENTINEL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
def stub_serve(monkeypatch) -> None:
    """Everything `serve` would really do, except touching the database."""
    monkeypatch.setattr("uvicorn.run", lambda _app, **_kwargs: None)
    monkeypatch.setattr(serve_cmd, "_load_local_provider_config", lambda: None)
    monkeypatch.setattr(serve_cmd, "_setup_embedder", lambda: None)
    monkeypatch.setattr(serve_cmd, "_serve_lock_path", lambda: None)
    monkeypatch.setattr(serve_cmd, "_find_free_port", lambda _host, port, _label: port)
    monkeypatch.delenv("REPOWISE_API_KEY", raising=False)
    monkeypatch.setenv("REPOWISE_HOST", "127.0.0.1")


def test_serve_leaves_a_db_url_the_caller_chose_alone(
    stub_serve: None, monkeypatch, tmp_path
) -> None:
    """Auto-detect fills a gap; it must never overrule an explicit choice.

    This is also the shape the fix in ``test_serve_host_env`` relies on: set
    the variable and the branch is skipped, so ``monkeypatch`` owns the
    cleanup rather than the command leaking a raw assignment.
    """
    (tmp_path / ".repowise").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPOWISE_DB_URL", SENTINEL)

    result = CliRunner().invoke(serve_cmd.serve_command, ["--no-ui"])

    assert result.exit_code == 0, result.output
    assert os.environ["REPOWISE_DB_URL"] == SENTINEL


def test_serve_adopts_the_local_store_when_nothing_is_set(
    stub_serve: None, monkeypatch, tmp_path
) -> None:
    """The behaviour that makes the guard necessary, pinned so it stays known.

    Asserting the cleanup from inside this test would prove nothing: the
    assignment is still live here by design. The test that follows is where it
    has to be gone.
    """
    (tmp_path / ".repowise").mkdir()
    monkeypatch.chdir(tmp_path)
    # Deliberately NOT monkeypatch.delenv. monkeypatch records whatever it
    # touches and restores it at teardown, which would clean up the leak for
    # us and make this test prove nothing. The real bug happened precisely
    # because no monkeypatch ever recorded this variable, so the command's raw
    # assignment had nothing to undo it. Reproduce that: clear it unrecorded
    # and leave the restoring to the guard under test.
    os.environ.pop("REPOWISE_DB_URL", None)

    result = CliRunner().invoke(serve_cmd.serve_command, ["--no-ui"])

    assert result.exit_code == 0, result.output
    assert tmp_path.name in os.environ["REPOWISE_DB_URL"]


def test_the_previous_test_did_not_leak_its_db_url() -> None:
    """The regression itself, and the reason this is a second test function.

    The bug was invisible within the test that caused it and only ever showed
    up in whatever ran next, so a second assertion in the test above could not
    have caught it. Ordering is the assertion: pytest runs a module's tests in
    definition order, and no random-ordering plugin is installed here.

    What restores the variable is the autouse guard in ``tests/conftest.py``.
    """
    assert "REPOWISE_DB_URL" not in os.environ
