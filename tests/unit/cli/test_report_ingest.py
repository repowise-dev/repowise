"""The ingest steps report commands share: open the index, say what did not map."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from repowise.cli.commands import _report_ingest
from repowise.core.analysis.health.coverage import live_head_commit


@pytest.fixture
def printed(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    out = io.StringIO()
    monkeypatch.setattr(_report_ingest, "console", Console(file=out, width=200))
    return out


class _Repo:
    id = "repo-1"
    head_commit = "b" * 40


def _repo_lookup(monkeypatch: pytest.MonkeyPatch, row) -> None:
    async def lookup(session, local_path):
        return row

    monkeypatch.setattr("repowise.core.persistence.crud.get_repository_by_path", lookup)


async def _keys(session, repo_id):
    return {"src/a.py"}


async def _no_keys(session, repo_id):
    return set()


async def test_open_ingest_target_returns_the_repo_keys_and_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, printed: io.StringIO
) -> None:
    _repo_lookup(monkeypatch, _Repo())
    target = await _report_ingest.open_ingest_target(
        None, tmp_path, what="evidence", load_keys=_keys
    )
    assert target is not None
    assert target.file_keys == {"src/a.py"}
    # No git checkout here, so the indexed commit stands in for HEAD.
    assert target.head_sha == _Repo.head_commit
    assert printed.getvalue() == ""


async def test_open_ingest_target_says_why_there_is_nothing_to_ingest_into(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, printed: io.StringIO
) -> None:
    _repo_lookup(monkeypatch, None)
    assert await _report_ingest.open_ingest_target(None, tmp_path, what="evidence") is None
    assert "before adding evidence" in printed.getvalue()

    _repo_lookup(monkeypatch, _Repo())
    assert (
        await _report_ingest.open_ingest_target(
            None, tmp_path, what="evidence", load_keys=_no_keys
        )
        is None
    )
    assert "No indexed files found" in printed.getvalue()


def test_live_head_commit_prefers_the_checkout(tmp_path: Path) -> None:
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    (tmp_path / "a.py").write_text("x = 1\n")
    repo.index.add(["a.py"])
    head = repo.index.commit("init").hexsha
    repo.close()
    assert live_head_commit(tmp_path, _Repo()) == head
    assert live_head_commit(tmp_path / "missing", None) is None


def test_unmapped_and_partial_messages(printed: io.StringIO) -> None:
    _report_ingest.report_unmapped([], "frames")
    assert printed.getvalue() == ""
    _report_ingest.report_unmapped([f"p{i}" for i in range(7)], "frames")
    _report_ingest.refuse_partial("report", "set coverage.strip_prefix: build")
    _report_ingest.report_stored_partial("evidence")
    text = printed.getvalue()
    assert "7 frames did not map to the repo tree (e.g. p0, p1, p2, p3, p4)." in text
    assert "More than half the report did not map" in text
    assert "set coverage.strip_prefix: build and re-run" in text
    assert "the stored evidence is flagged partial" in text


def test_messages_escape_what_they_quote(printed: io.StringIO) -> None:
    _report_ingest.refuse_partial("report", "set coverage.path_prefix: [web]")
    _report_ingest.report_unmapped(["build/[gen]/a.py"], "report file(s)")
    text = printed.getvalue()
    assert "path_prefix: [web] and re-run" in text
    assert "(e.g. build/[gen]/a.py)" in text


def test_messages_go_to_the_console_the_caller_names(printed: io.StringIO) -> None:
    other = io.StringIO()
    _report_ingest.report_unmapped(["a.py"], "entries", out=Console(file=other, width=200))
    assert printed.getvalue() == ""
    assert "1 entries did not map" in other.getvalue()
