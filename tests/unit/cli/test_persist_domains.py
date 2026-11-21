"""Fault injection at work and context-exit commits of init's retry domains."""

from __future__ import annotations

import asyncio
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from repowise.cli.commands.init_cmd import persistence
from repowise.core import persistence as db
from repowise.core import pipeline
from repowise.core.pipeline import persist, resume


class ConnectionDoesNotExistError(Exception):
    pass


class _Session:
    def __init__(self, harness):
        self.harness = harness
        self.domain = None
        self.attempt = 0

    async def __aenter__(self):
        self.harness.sessions.append(self)
        return self

    async def __aexit__(self, *args):
        self.harness.events.append(("close", self.domain, self.attempt))

    async def commit(self):
        self.harness.events.append(("commit", self.domain, self.attempt))
        self.harness.fail(self, "commit")
        self.harness.committed.append(self.domain)

    async def rollback(self):
        self.harness.events.append(("rollback", self.domain, self.attempt))

    async def execute(self, *_args, **_kwargs):
        # The search cleanup-debt seed reads tombstoned page ids once per store.
        class _Rows:
            def scalars(self):
                return []

        return _Rows()


class _Harness:
    def __init__(self, monkeypatch, *, index_done=True, domain=None, stage=None, errors=()):
        self.sessions = []
        self.events = []
        self.calls = Counter()
        self.committed = []
        self.domain = domain
        self.stage = stage
        self.errors = iter(errors)
        self.engine = SimpleNamespace(dispose=AsyncMock())
        self.fts = SimpleNamespace(
            ensure_index=AsyncMock(), delete_many=AsyncMock(), index_pages=AsyncMock()
        )
        self.store = SimpleNamespace(delete_many=AsyncMock(side_effect=self.delete))
        self.result = SimpleNamespace(
            repo_name="r",
            index_persisted_incrementally=index_done,
            generated_pages=[SimpleNamespace(metadata={})],
            parsed_files=[],
            health_report=None,
            vector_store=self.store,
            tech_stack={"language": "Python"},
        )
        self.progress = SimpleNamespace(
            on_phase_done=lambda phase: self.events.append(("done", phase))
        )
        monkeypatch.setattr(persistence, "_RETRY_BACKOFF_S", (0,))
        monkeypatch.setattr(
            persistence,
            "open_repo_db",
            AsyncMock(return_value=(self.engine, lambda: _Session(self), "initial")),
        )
        monkeypatch.setattr(db, "FullTextSearch", lambda engine: self.fts)
        monkeypatch.setattr(db, "refresh_planner_stats", AsyncMock())
        monkeypatch.setattr(db, "upsert_repository", self.repository)
        monkeypatch.setattr(pipeline, "persist_analysis", self.analysis)
        monkeypatch.setattr(pipeline, "persist_generation", self.generation)
        monkeypatch.setattr(pipeline, "sweep_stale_generated_pages", self.sweep)
        monkeypatch.setattr(persist, "sweep_store_hygiene", AsyncMock(return_value=[]))
        monkeypatch.setattr(persistence, "apply_index_persistence", AsyncMock())
        monkeypatch.setattr(pipeline, "persist_pipeline_result", self.full)
        monkeypatch.setattr(pipeline, "tombstone_absent_file_pages", self.tombstones)
        monkeypatch.setattr(db.crud, "upsert_generation_job", self.job)
        self.ledger = SimpleNamespace(mark_completed=AsyncMock())
        monkeypatch.setattr(resume, "ResumeLedger", lambda *args: self.ledger)
        from repowise.core.pipeline import commit_health

        monkeypatch.setattr(commit_health, "refresh_commit_health_after_update", AsyncMock())
        self.publish = AsyncMock()
        monkeypatch.setattr(persistence, "publish_index_persistence", self.publish)

    def fail(self, session, stage):
        if session.domain == self.domain and stage == self.stage:
            error = next(self.errors, None)
            if error is not None:
                raise error

    def start(self, session, domain):
        assert session.domain is None, "domains must not share sessions"
        session.domain = domain
        self.calls[domain] += 1
        session.attempt = self.calls[domain]
        self.events.append(("work", domain, session.attempt))
        self.fail(session, "work")

    async def repository(self, session, **kwargs):
        self.start(session, "repository")
        return SimpleNamespace(id=f"repo-{session.attempt}", settings_json='{"kept": true}')

    async def analysis(self, result, session, repo_id):
        assert repo_id == f"repo-{self.calls['repository']}"
        self.start(session, "analysis")

    async def generation(self, result, session, repo_id):
        self.start(session, "generation")

    async def sweep(self, session, *args):
        return [f"swept-{session.attempt}"]

    async def full(self, result, session, repo_id, **kwargs):
        self.start(session, "generation")
        return [f"swept-{session.attempt}"]

    async def tombstones(self, session, *args):
        self.start(session, "prune")
        return [f"tombstone-{session.attempt}"]

    async def job(self, session, **kwargs):
        self.start(session, "job")
        return SimpleNamespace()

    async def delete(self, ids):
        self.events.append(("delete", tuple(ids)))
        # A delete failure must replay all work in that domain too.
        self.fail(self.sessions[-1], "delete")


@pytest.mark.parametrize(
    "index_done,domain",
    [
        (index_done, domain)
        for index_done in (True, False)
        for domain in ("repository", "analysis", "generation", "prune", "job")
        if index_done or domain != "analysis"
    ],
)
@pytest.mark.parametrize("stage", ["work", "commit"])
async def test_only_failed_domain_replays(monkeypatch, tmp_path, index_done, domain, stage):
    h = _Harness(
        monkeypatch,
        index_done=index_done,
        domain=domain,
        stage=stage,
        errors=[ConnectionDoesNotExistError("drop")],
    )
    await persistence.persist_result(h.result, tmp_path, h.progress)
    domains = ["repository", *(["analysis"] if index_done else []), "generation", "prune", "job"]
    assert h.calls == Counter({d: 2 if d == domain else 1 for d in domains})
    assert h.committed == domains
    assert len(h.sessions) == len(domains) + 1
    assert len({id(s) for s in h.sessions}) == len(h.sessions)
    generation_attempt = h.calls["generation"]
    prune_attempt = h.calls["prune"]
    h.fts.delete_many.assert_awaited_once_with(
        [f"swept-{generation_attempt}", f"tombstone-{prune_attempt}"]
    )
    h.publish.assert_awaited_once_with(f"repo-{h.calls['repository']}", tmp_path)
    # Successful outputs are consumed only after every domain committed and closed.
    assert h.events[-2:] == [("close", "job", h.calls["job"]), ("done", "persist")]
    for d, prefix in [("generation", "swept"), ("prune", "tombstone")]:
        for attempt in range(1, h.calls[d] + 1):
            if ("commit", d, attempt) in h.events:
                assert h.events.index(("delete", (f"{prefix}-{attempt}",))) < h.events.index(
                    ("commit", d, attempt)
                )
    if stage == "commit" and domain in ("generation", "prune"):
        assert h.store.delete_many.await_count == 3
    h.engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("domain", ["generation", "prune"])
async def test_delete_failure_replays_domain(monkeypatch, tmp_path, domain):
    h = _Harness(monkeypatch, domain=domain, stage="delete", errors=[ConnectionDoesNotExistError()])
    await persistence.persist_result(h.result, tmp_path)
    assert h.calls[domain] == 2
    assert h.calls["repository"] == h.calls["analysis"] == 1
    assert h.store.delete_many.await_count == 3
    h.fts.delete_many.assert_awaited_once_with(
        [f"swept-{h.calls['generation']}", f"tombstone-{h.calls['prune']}"]
    )


@pytest.mark.parametrize("stage", ["work", "commit"])
@pytest.mark.parametrize("error", [ValueError("bug"), asyncio.CancelledError("cancel")])
async def test_nonconnection_and_cancellation_do_not_retry(monkeypatch, tmp_path, stage, error):
    # Even an explicitly chained driver drop must not make cancellation retryable.
    error.__cause__ = ConnectionDoesNotExistError()
    if isinstance(error, ValueError):
        error.__cause__ = None
    h = _Harness(monkeypatch, domain="generation", stage=stage, errors=[error])
    with pytest.raises(type(error)):
        await persistence.persist_result(h.result, tmp_path, h.progress)
    assert h.calls["generation"] == 1
    assert h.calls["job"] == 0
    h.fts.delete_many.assert_not_awaited()
    h.ledger.mark_completed.assert_not_awaited()
    h.publish.assert_not_awaited()
    h.engine.dispose.assert_awaited_once()
    assert not any(e[0] == "done" for e in h.events)


@pytest.mark.parametrize("stage", ["work", "commit"])
async def test_exhaustion_propagates_without_outputs(monkeypatch, tmp_path, stage):
    errors = [ConnectionDoesNotExistError("first"), ConnectionDoesNotExistError("last")]
    h = _Harness(monkeypatch, domain="prune", stage=stage, errors=errors)
    with pytest.raises(ConnectionDoesNotExistError, match="last"):
        await persistence.persist_result(h.result, tmp_path, h.progress)
    assert h.calls == Counter(repository=1, analysis=1, generation=1, prune=2)
    h.fts.delete_many.assert_not_awaited()
    h.ledger.mark_completed.assert_not_awaited()
    h.publish.assert_not_awaited()
    h.engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("index_done", [True, False])
async def test_ordinary_tombstone_work_error_is_best_effort(monkeypatch, tmp_path, index_done):
    h = _Harness(
        monkeypatch,
        index_done=index_done,
        domain="prune",
        stage="work",
        errors=[ValueError("stale page")],
    )
    await persistence.persist_result(h.result, tmp_path, h.progress)
    assert h.calls["prune"] == 1
    assert h.calls["job"] == 1
    h.fts.delete_many.assert_awaited_once_with(["swept-1"])
    h.store.delete_many.assert_awaited_once_with(["swept-1"])
    h.publish.assert_awaited_once()


async def test_runner_output_waits_for_context_exit(monkeypatch):
    committed = asyncio.Event()
    release = asyncio.Event()
    sessions = []

    class Session(_Session):
        async def __aenter__(self):
            sessions.append(self)
            return self

        async def commit(self):
            committed.set()
            await release.wait()

        async def __aexit__(self, *args):
            pass

    async def work(session):
        return ["id"]

    task = asyncio.create_task(persistence._persist_domain(lambda: Session(None), "test", work))
    await committed.wait()
    assert not task.done()
    release.set()
    assert await task == ["id"]
    assert len(sessions) == 1


@pytest.mark.parametrize("stage", ["work", "commit"])
async def test_real_sessions_rollback_failed_attempt(monkeypatch, tmp_path, stage):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from repowise.core.persistence import database

    engine = database.create_engine(f"sqlite+aiosqlite:///{tmp_path}/retry.db")
    sessions = []
    attempts = 0

    class FaultSession(AsyncSession):
        async def commit(self):
            if stage == "commit" and attempts == 1:
                raise ConnectionDoesNotExistError("commit dropped")
            await super().commit()

    # Deliberately enable real expiry: callers must not read ORM objects from a
    # prior domain, even if a supplied factory differs from the default.
    sf = async_sessionmaker(engine, class_=FaultSession, expire_on_commit=True)
    monkeypatch.setattr(persistence, "_RETRY_BACKOFF_S", (0,))

    async def work(session):
        nonlocal attempts
        attempts += 1
        sessions.append(session)
        await session.execute(text("INSERT INTO attempts VALUES (:n)"), {"n": attempts})
        if stage == "work" and attempts == 1:
            raise ConnectionDoesNotExistError("work dropped")
        return [f"id-{attempts}"]

    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE attempts (n INTEGER)"))
        assert await persistence._persist_domain(sf, "test", work) == ["id-2"]
        assert len(sessions) == 2
        assert sessions[0] is not sessions[1]
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT n FROM attempts"))).scalars().all() == [2]
        async with database.create_session_factory(engine)() as session:
            assert session.sync_session.expire_on_commit is False
    finally:
        await engine.dispose()


async def test_fts_setup_failure_still_disposes_engine(monkeypatch, tmp_path):
    h = _Harness(monkeypatch)
    h.fts.ensure_index.side_effect = ValueError("fts setup")
    with pytest.raises(ValueError, match="fts setup"):
        await persistence.persist_result(h.result, tmp_path)
    h.engine.dispose.assert_awaited_once()
