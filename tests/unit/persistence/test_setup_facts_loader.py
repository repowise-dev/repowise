"""``load_setup_facts`` over a real index and checkout: each fact from its own read."""

from __future__ import annotations

import datetime
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from repowise.core.persistence import create_engine, create_session_factory, get_session
from repowise.core.persistence.crud import upsert_repository
from repowise.core.persistence.crud.analysis.actions import load_actions_view
from repowise.core.persistence.crud.analysis.setup_facts import (
    dismiss_setup,
    load_setup_facts,
    with_current_call,
)
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import GitCommit

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "evidence"


@pytest.fixture
async def indexed(tmp_path: Path):
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".repowise").mkdir()
    engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
    await init_db(engine)
    factory = create_session_factory(engine)
    async with get_session(factory) as session:
        repo = await upsert_repository(session, name="repo", local_path=str(root), url="")
        later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)
        session.add_all(
            GitCommit(repository_id=repo.id, sha=f"c{i}", author_name="Ada",
                      author_email="ada@x.io", committed_at=later, subject="feat")
            for i in range(3)
        )  # fmt: skip
        await session.commit()
        repo_id = repo.id
    try:
        yield root, factory, repo_id
    finally:
        await engine.dispose()


async def test_facts_come_from_the_index_and_the_checkout(indexed) -> None:
    root, factory, repo_id = indexed
    (root / "coverage.xml").write_text("<coverage/>", encoding="utf-8")
    bench = root / ".benchmarks" / "Linux-CPython-3.13-64bit"
    bench.mkdir(parents=True)
    shutil.copy(_FIXTURES / "pytest_benchmark.json", bench / "0001_base.json")

    async with get_session(factory) as session:
        facts = await load_setup_facts(session, repo_id, root, rewrite_hook=False)
    assert facts.sync_hook is False
    assert facts.coverage_ingested is False and facts.runtime_stored is False
    assert [r.path for r in facts.coverage_reports] == ["coverage.xml"]
    assert [(f.path, f.kind) for f in facts.evidence_files] == [
        (".benchmarks/Linux-CPython-3.13-64bit/0001_base.json", "pytest-benchmark")
    ]
    assert facts.synced_commits == 3
    assert facts.agent_calls == 0 and facts.first_call_at is None
    assert facts.always_load is False and facts.rewrite_hook is False
    assert not facts.unavailable


async def test_the_view_records_the_answer_and_a_dismissal_holds(indexed) -> None:
    root, factory, repo_id = indexed
    async with get_session(factory) as session:
        facts = with_current_call(await load_setup_facts(session, repo_id, root))
        view = await load_actions_view(session, repo_id, setup=facts)
        assert view["setup"]["offer"] == "finish_setup"
        assert (root / ".repowise" / "setup.json").exists()

        await dismiss_setup(session, repo_id)
        await session.commit()
        view = await load_actions_view(session, repo_id, setup=facts)
    assert view["setup"]["dismissed"] is True and view["setup"]["offer"] is None
    assert view["setup"]["open"] >= 1


async def test_the_offer_is_made_at_most_once_per_repo(indexed) -> None:
    from repowise.core.setup_state import offered_steps, read_setup_state

    root, factory, repo_id = indexed
    async with get_session(factory) as session:
        facts = with_current_call(await load_setup_facts(session, repo_id, root))
        offered = await load_actions_view(session, repo_id, setup=facts)
        assert offered["setup"]["offer"] == "finish_setup"
        assert offered_steps(root) == offered["setup"]["open"]

        # Every step done: zero left, recorded as completed.
        done = replace(facts, sync_hook=True, background_records=0)
        view = await load_actions_view(session, repo_id, setup=done)
        assert view["setup"]["open"] == 0 and view["setup"]["completed"] is True
        assert read_setup_state(root)["completed"] is True

        # A row unlocked later appears, but the offer and the lines stay gone.
        facts = with_current_call(await load_setup_facts(session, repo_id, root))
        assert facts.completed is True
        later = replace(facts, sync_hook=True, background_records=0, claude_code_calls=25)
        view = await load_actions_view(session, repo_id, setup=later)
    assert view["setup"]["open"] >= 1 and view["setup"]["offer"] is None
    assert "always_load_suggested" in {a["rule"] for a in view["horizons"]["week"]["actions"]}
    assert offered_steps(root) == 0
    assert read_setup_state(root)["completed"] is True
