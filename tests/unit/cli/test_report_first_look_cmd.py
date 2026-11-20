"""``repowise report first-look``: html, md and json from one stored report, and --since-last."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import delete, update

from tests.unit.persistence.test_first_look_collect import SECRET
from tests.unit.persistence.test_first_look_collect import _seed as _seed_rows


async def _index(root: Path) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
    )
    from repowise.core.persistence.models import Repository

    (root / ".repowise").mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+aiosqlite:///{(root / '.repowise' / 'wiki.db').as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            rid = await _seed_rows(session)
            repo = await session.get(Repository, rid)
            repo.name, repo.local_path = "shop", str(root)
            await session.commit()
    finally:
        await engine.dispose()


async def _fix_the_secret(root: Path) -> None:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.models import Repository, SecurityFinding

    engine = create_engine(f"sqlite+aiosqlite:///{(root / '.repowise' / 'wiki.db').as_posix()}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            await session.execute(delete(SecurityFinding))
            repo = (await session.execute(Repository.__table__.select())).first()
            (await session.get(Repository, repo.id)).head_commit = "d" * 40
            await session.commit()
    finally:
        await engine.dispose()


async def _move_the_secret(root: Path) -> None:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.models import SecurityFinding

    engine = create_engine(f"sqlite+aiosqlite:///{(root / '.repowise' / 'wiki.db').as_posix()}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            await session.execute(
                update(SecurityFinding)
                .where(SecurityFinding.file_path == "src/settings.py")
                .values(line_number=7, snippet=f'PASSWORD = "{SECRET}2"')
            )
            await session.commit()
    finally:
        await engine.dispose()


@pytest.fixture
def indexed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from repowise.cli.helpers import run_async

    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    root = (tmp_path / "shop").resolve()
    run_async(_index(root))
    return root


def _run(*args: str):
    from repowise.cli.main import cli

    return CliRunner().invoke(cli, ["report", "first-look", *args])


def test_html_is_written_beside_the_stored_report(indexed: Path) -> None:
    result = _run(str(indexed))
    assert result.exit_code == 0, result.output
    reports = indexed / ".repowise" / "reports"
    html = (reports / "first-look.html").read_text(encoding="utf-8")
    assert "<h1>shop</h1>" in html and "Fix with your agent" in html
    # Built on first use, so the JSON, Markdown and the row exist too.
    assert (reports / "first-look.json").exists() and (reports / "first-look.md").exists()
    assert SECRET not in html


def test_json_for_agents_and_markdown_to_stdout(indexed: Path, tmp_path: Path) -> None:
    result = _run(str(indexed), "--format", "json")
    assert result.exit_code == 0, result.output
    # The runner mixes in stderr's one "writing it from the index" line.
    doc = json.loads(result.stdout[result.stdout.index("{") :])
    assert doc["prose"]["writer"] == "template"
    # Each item with its id and status, and no per-item prompt.
    assert "prompts" not in doc
    items = [i for s in doc["sections"] for i in s["items"]]
    assert {i["status"] for i in items} == {"open"} and {i["change"] for i in items} == {None}
    assert doc["baseline"]["stamp"]["commit"] == "c" * 40
    assert all(i["id"] in doc["report_prompt"] for i in items[:3])
    assert SECRET not in result.stdout

    out = tmp_path / "fl.md"
    md = _run(str(indexed), "--format", "md", "--out", str(out))
    assert md.exit_code == 0, md.output
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# First look: shop")
    assert SECRET not in text


def test_since_last_lists_what_was_fixed(indexed: Path) -> None:
    from repowise.cli.helpers import run_async

    assert _run(str(indexed), "--format", "json").exit_code == 0  # writes the report
    run_async(_fix_the_secret(indexed))

    text = _run(str(indexed), "--since-last")
    assert text.exit_code == 0, text.output
    assert text.stdout.startswith(
        "Written at cccccccc. The index is now at dddddddd. 1 fixed, 0 changed, 0 new."
    )
    assert "Security:" in text.stdout and "scripts/tool.py:9" in text.stdout

    data = json.loads(_run(str(indexed), "--since-last", "--format", "json").stdout)
    assert data["behind"] is True and len(data["fixed"]) == 1


def test_since_last_shows_a_changed_item_as_it_is_now(indexed: Path) -> None:
    from repowise.cli.helpers import run_async

    assert _run(str(indexed), "--format", "json").exit_code == 0  # writes the report
    run_async(_move_the_secret(indexed))

    text = _run(str(indexed), "--since-last")
    assert text.exit_code == 0, text.output
    assert "0 fixed, 1 changed, 0 new." in text.stdout
    assert "src/settings.py:7" in text.stdout.split("Changed:", 1)[1]

    data = json.loads(_run(str(indexed), "--since-last", "--format", "json").stdout)
    moved = {loc["path"]: loc["line"] for loc in data["changed_items"][0]["locations"]}
    assert moved["src/settings.py"] == 7


def test_without_an_index_it_says_so(tmp_path: Path) -> None:
    result = _run(str(tmp_path))
    assert result.exit_code != 0
    assert "repowise init" in result.output


def test_init_writes_the_report_and_never_fails_on_it(indexed: Path, monkeypatch) -> None:
    import contextlib
    from types import SimpleNamespace

    from repowise.cli.commands.init_cmd import _finish
    from repowise.core.report.first_look import write

    steps = SimpleNamespace(step=lambda name: contextlib.nullcontext())
    run = SimpleNamespace(repo_path=indexed, effective_index_only=True, provider=None)
    first_look = _finish._write_first_look(run, steps)
    assert first_look.line() == "First look  .repowise/reports/first-look.html  (template, free)"
    stored = indexed / ".repowise" / "reports" / "first-look.json"
    written = stored.read_text(encoding="utf-8")
    assert (indexed / ".repowise" / "reports" / "first-look.html").exists()

    async def broken(*args, **kwargs):
        raise RuntimeError("store is gone")

    monkeypatch.setattr(write, "write_first_look", broken)
    # A later init keeps the stamped report and still names it.
    assert _finish._write_first_look(run, steps).line() == first_look.line()
    assert stored.read_text(encoding="utf-8") == written
    stored.unlink()
    assert _finish._write_first_look(run, steps) is None


def test_the_app_address_comes_from_a_live_serve_lock_only(tmp_path: Path) -> None:
    import os

    from repowise.cli.commands.serve_cmd._lock import live_web_url

    lock = tmp_path / ".repowise" / "serve.lock.json"
    lock.parent.mkdir()
    assert live_web_url(tmp_path) is None
    lock.write_text(json.dumps({"pid": os.getpid(), "host": "0.0.0.0", "ui_port": 3100}))
    assert live_web_url(tmp_path) == "http://localhost:3100"
    lock.write_text(json.dumps({"pid": os.getpid(), "ui_port": None}))
    assert live_web_url(tmp_path) is None  # API only: no web page to link
    lock.write_text("{not json")
    assert live_web_url(tmp_path) is None
