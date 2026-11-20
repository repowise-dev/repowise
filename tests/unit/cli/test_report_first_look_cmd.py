"""``repowise report repo``: html, md and json from one stored report, and --since-last."""

from __future__ import annotations

import json
import re
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

    return CliRunner().invoke(cli, ["report", "repo", *args])


def test_html_is_written_beside_the_stored_report(indexed: Path) -> None:
    result = _run(str(indexed))
    assert result.exit_code == 0, result.output
    reports = indexed / ".repowise" / "reports"
    html = (reports / "repo-report.html").read_text(encoding="utf-8")
    assert "<h1>shop</h1>" in html and "Fix with your agent" in html
    # Built on first use, so the JSON, Markdown and the row exist too.
    assert (reports / "first-look.json").exists() and (reports / "repo-report.md").exists()
    assert SECRET not in html


def test_json_for_agents_and_markdown_to_stdout(indexed: Path, tmp_path: Path) -> None:
    result = _run(str(indexed), "--format", "json")
    assert result.exit_code == 0, result.output
    # The runner mixes in stderr's one "writing it from the index" line.
    doc = json.loads(result.stdout[result.stdout.index("{") :])
    assert doc["prose"]["writer"] == "template"
    # Each item with the fields an agent acts on, and no prompt anywhere.
    assert not {"prompts", "report_prompt"} & set(doc)
    items = [i for s in doc["sections"] for i in s["items"]]
    for item in items:
        assert {"id", "section", "rule", "status", "status_reason", "change", "locations",
                "effort", "confidence"} <= set(item)
        assert "prompt" not in item
        assert all(set(loc) == {"path", "line", "symbol"} for loc in item["locations"])
    assert {i["status"] for i in items} == {"open"} and {i["change"] for i in items} == {None}
    assert doc["baseline"]["stamp"]["commit"] == "c" * 40
    assert doc["prose_source"] == {"writer": "template", "written_at": None, "cost_usd": None}
    assert SECRET not in result.stdout

    out = tmp_path / "fl.md"
    md = _run(str(indexed), "--format", "md", "--out", str(out))
    assert md.exit_code == 0, md.output
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Repo report: shop")
    # The top reads on its own: the headline, the counts, and where to start, with places.
    top = text.split("\n## 1. ", 1)[0]
    assert "Health is" in top and "Found: 2 live secrets" in top
    assert "## Do these first" in top and "(`scripts/tool.py:9` and 1 more)" in top
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


def test_prompt_prints_the_whole_report_prompt_for_the_open_items(indexed: Path) -> None:
    first = _run(str(indexed), "--format", "json").stdout  # writes the report first
    doc = json.loads(first[first.index("{") :])
    prompt = _run(str(indexed), "--prompt")
    assert prompt.exit_code == 0, prompt.output
    assert "repowise mark <id> done" in prompt.stdout
    open_ids = [i["id"] for s in doc["sections"] for i in s["items"] if i["status"] == "open"]
    assert open_ids and all(i in prompt.stdout for i in open_ids)
    assert SECRET not in prompt.stdout


def test_bare_report_is_the_repo_report_and_subcommands_still_resolve(
    indexed: Path, monkeypatch
) -> None:
    from repowise.cli.main import cli

    runner = CliRunner()
    monkeypatch.chdir(indexed)
    bare = runner.invoke(cli, ["report", "--format", "md"])
    assert bare.exit_code == 0, bare.output
    repo = runner.invoke(cli, ["report", "repo", "--format", "md"])
    # The first run also says on stderr that it is writing the report.
    assert bare.stdout[bare.stdout.index("# Repo report: shop") :] == repo.stdout
    for name in ("assessment", "inventory", "changes", "estate", "repo", "first-look"):
        shown = runner.invoke(cli, ["report", name, "--help"])
        assert shown.exit_code == 0 and f"report {name} [OPTIONS]" in shown.output
    # A repo report option before another subcommand would be dropped: refused.
    mixed = runner.invoke(cli, ["report", "--format", "json", "estate"])
    assert mixed.exit_code == 2 and "Put --format after the subcommand" in mixed.output


def test_first_look_is_an_alias_of_report_repo(indexed: Path) -> None:
    from repowise.cli.main import cli

    runner = CliRunner()
    old = runner.invoke(cli, ["report", "first-look", str(indexed), "--format", "md"])
    new = runner.invoke(cli, ["report", "repo", str(indexed), "--format", "md"])
    assert old.exit_code == 0, old.output
    assert old.stdout[old.stdout.index("# Repo report") :] == new.stdout
    listed = runner.invoke(cli, ["report", "--help"]).output
    assert re.search(r"first-look\s+Alias for `repowise report repo`\.", listed)


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
    run = SimpleNamespace(
        repo_path=indexed,
        effective_index_only=True,
        provider=None,
        no_provider=True,
        cost_tracker=None,
        opts=SimpleNamespace(dry_run=False, index_only=False),
    )
    # A person at a terminal gets the path; an agent the one instruction.
    monkeypatch.setattr(_finish.sys.stdin, "isatty", lambda: True)
    first_look = _finish._write_first_look(run, steps)
    assert first_look.line() == "Repo report  .repowise/reports/repo-report.html  (template, free)"
    assert not first_look.agent and first_look.open_items > 0
    monkeypatch.setattr(_finish.sys.stdin, "isatty", lambda: False)
    piped = _finish._write_first_look(run, steps)
    assert piped.line() == (
        "Repo report: tell the user what it found (repowise report --format md); "
        "they decide what to fix next."
    )
    assert piped.summary()["open_items"] == first_look.open_items
    stored = indexed / ".repowise" / "reports" / "first-look.json"
    written = stored.read_text(encoding="utf-8")
    assert (indexed / ".repowise" / "reports" / "repo-report.html").exists()

    async def broken(*args, **kwargs):
        raise RuntimeError("store is gone")

    monkeypatch.setattr(write, "write_first_look", broken)
    # A later init keeps the stamped report and still names it.
    assert _finish._write_first_look(run, steps).line() == piped.line()
    assert stored.read_text(encoding="utf-8") == written
    stored.unlink()
    assert _finish._write_first_look(run, steps) is None


@pytest.mark.parametrize(
    ("dry_run", "index_only", "no_provider", "effective_index_only", "passed"),
    [
        (False, False, False, False, True),
        (False, True, False, True, False),  # --no-prose
        (False, False, True, True, False),  # keyless, whatever the environment holds
        (False, False, False, True, False),  # the cost was declined
        (True, False, False, False, False),
    ],
    ids=["model-run", "no-prose", "keyless", "declined", "dry-run"],
)
def test_init_passes_a_provider_only_on_a_model_run(
    dry_run, index_only, no_provider, effective_index_only, passed
) -> None:
    from types import SimpleNamespace

    from repowise.cli.commands.init_cmd._finish import first_look_provider

    provider = object()
    run = SimpleNamespace(
        provider=provider,
        no_provider=no_provider,
        effective_index_only=effective_index_only,
        opts=SimpleNamespace(dry_run=dry_run, index_only=index_only),
    )
    assert (first_look_provider(run) is provider) is passed


def test_init_plan_line_only_when_the_call_will_run(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from repowise.cli.commands.report_first_look_cmd import first_look_plan_line

    provider = SimpleNamespace(provider_name="anthropic", model_name="claude-haiku-5-5")
    line = first_look_plan_line(tmp_path, provider)
    assert line == "Repo report about $0.003 with claude-haiku-5-5 (one model call)."
    config = tmp_path / ".repowise" / "config.yaml"
    config.parent.mkdir()
    config.write_text("report:\n  first_look:\n    llm: false\n", encoding="utf-8")
    assert first_look_plan_line(tmp_path, provider) is None
    config.write_text("", encoding="utf-8")
    (tmp_path / ".repowise" / "reports").mkdir()
    (tmp_path / ".repowise" / "reports" / "first-look.json").write_text("{}", encoding="utf-8")
    assert first_look_plan_line(tmp_path, provider) == line  # unreadable: init writes anew
    from repowise.core.report.first_look.store import write_report_file
    from tests.unit.core.test_first_look import _item, _report

    write_report_file(tmp_path, _report(_item()))
    assert first_look_plan_line(tmp_path, provider) is None  # a stamped report is kept


def test_llm_flag_prints_the_cost_and_writes_with_the_model(indexed: Path, monkeypatch) -> None:
    from repowise.cli import helpers
    from tests.unit.persistence.test_first_look_write import EchoProvider

    provider = EchoProvider()
    monkeypatch.setattr(helpers, "resolve_provider", lambda *a, **k: provider)
    from repowise.cli.main import cli

    declined = CliRunner().invoke(
        cli, ["report", "repo", str(indexed), "--llm", "--format", "json"], input="n\n"
    )
    assert declined.exit_code == 0, declined.output
    assert "about $" in declined.output and provider.calls == 0

    result = _run(str(indexed), "--llm", "--yes", "--format", "json")
    assert result.exit_code == 0, result.output
    assert provider.calls == 1
    stored = json.loads((indexed / ".repowise" / "reports" / "first-look.json").read_text())
    assert stored["stamp"]["generator"] == "claude-haiku-5-5"
    assert stored["prose"]["writer"] == "claude-haiku-5-5"


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


def test_reads_and_update_never_call_the_model(indexed: Path, monkeypatch) -> None:
    """The model writes the wording at init or on --llm only: every read of the
    report, and `repowise update`, reuse the stored wording or the template."""
    from repowise.cli import helpers
    from repowise.cli.helpers import repo_index_session, run_async
    from repowise.core.report.first_look import llm
    from repowise.core.report.first_look.living import build_living
    from tests.unit.persistence.test_first_look_write import EchoProvider

    provider = EchoProvider()
    monkeypatch.setattr(helpers, "resolve_provider", lambda *a, **k: provider)
    assert _run(str(indexed), "--llm", "--yes", "--format", "json").exit_code == 0
    assert provider.calls == 1  # the explicit request

    async def refused(*args, **kwargs):
        raise AssertionError("a read called the model")

    monkeypatch.setattr(llm, "rewrite_prose", refused)
    for args in (["--format", "json"], ["--format", "md"], ["--prompt"], ["--since-last"], []):
        result = _run(str(indexed), *args)
        assert result.exit_code == 0, result.output

    async def living():
        async with repo_index_session(indexed) as (session, repo_id):
            return await build_living(session, repo_id, indexed, store_missing=True)

    report = run_async(living()).to_json()
    assert provider.calls == 1
    # The stored model wording is reused, and says whose it is.
    assert report["prose_source"]["writer"] == "claude-haiku-5-5"
    assert report["prose_source"]["written_at"] is not None

    # `repowise update` has no path to the report writer or the model call.
    update = Path(helpers.__file__).parent / "commands" / "update_cmd"
    sources = [p.read_text(encoding="utf-8") for p in sorted(update.glob("*.py"))]
    assert sources and not any("report.first_look" in text for text in sources)
