"""Index plugins share the write transaction and publish after it commits."""

from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.persistence import database, upsert_repository
from repowise.core.persistence.crud import upsert_git_metadata_bulk
from repowise.core.pipeline.index_persistence import IndexWrite, index_persistence


@pytest.fixture
async def writer(tmp_path, monkeypatch):
    monkeypatch.setattr(index_persistence, "_impls", {})
    (tmp_path / ".repowise").mkdir()
    for name in ("new.py", "locked.py"):
        (tmp_path / name).write_text("def branch(value):\n    return value\n")
    engine = database.create_engine(f"sqlite+aiosqlite:///{tmp_path}/.repowise/wiki.db")
    await database.init_db(engine)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE plugin_records (name TEXT)"))
    sf = database.create_session_factory(engine)
    async with database.get_session(sf) as session:
        repo = await upsert_repository(session, name=tmp_path.name, local_path=str(tmp_path))
        await upsert_git_metadata_bulk(
            session, repo.id, [{"file_path": path} for path in ("old.py", "gone.py")]
        )
    result = SimpleNamespace(
        repo_name=tmp_path.name,
        repo_path=str(tmp_path),
        parsed_files=[],
        file_infos=[SimpleNamespace(path="locked.py")],
        graph_builder=GraphBuilder(tmp_path),
        git_metadata_list=[],
        git_summary=None,
        dead_code_report=None,
        decision_report=None,
        generated_pages=[],
        execution_flow_report=None,
    )
    yield SimpleNamespace(sf=sf, engine=engine, repo_id=repo.id, root=tmp_path, result=result)
    await engine.dispose()


@pytest.mark.parametrize("path", ["init", "full", "incremental", "full_update", "rescore"])
@pytest.mark.parametrize("outcome", ["ok", "apply", "publish"])
async def test_writers_apply_in_order_and_publish_after_commit(writer, monkeypatch, path, outcome):
    from repowise.cli.commands.init_cmd.persistence import persist_result
    from repowise.cli.commands.update_cmd.full_persist import FullUpdatePersist, write_full_update
    from repowise.cli.commands.update_cmd.persistence import _rescore_health_from_db
    from repowise.core.ingestion.change_detector import FileDiff
    from repowise.core.pipeline.full_index import index_repo_full
    from repowise.core.pipeline.incremental import persist_incremental_index

    calls = []
    error = RuntimeError(outcome)
    result = writer.result
    parsed_files = result.parsed_files
    walked = frozenset({"locked.py"})
    diff = {"changed_paths": ("new.py",), "deleted_paths": ("gone.py", "old.py")}
    expected = {
        "init": IndexWrite("full", [], traversed_paths=walked),
        "full": IndexWrite("full", [], traversed_paths=walked),
        "incremental": IndexWrite(
            "incremental", [], **diff, degraded=("Symbol persist: earlier failure",)
        ),
        "full_update": IndexWrite(
            "incremental", [], **diff, traversed_paths=walked, degraded=("earlier failure",)
        ),
        "rescore": IndexWrite("incremental", [], deleted_paths=("gone.py",)),
    }[path]

    async def first(session, repo_id, write):
        assert repo_id == writer.repo_id
        assert write == expected
        assert write.parsed_files is parsed_files
        assert session.in_transaction()
        calls.append("first")
        await session.execute(text("INSERT INTO plugin_records VALUES ('first')"))

    async def second(session, repo_id, write):
        assert await session.scalar(text("SELECT name FROM plugin_records")) == "first"
        calls.append("second")
        if outcome == "apply":
            raise error

    async def publish(repo_id, root):
        assert (repo_id, root) == (writer.repo_id, writer.root)
        async with writer.engine.connect() as conn:
            assert await conn.scalar(text("SELECT name FROM plugin_records")) == "first"
        calls.append("publish")
        if outcome == "publish":
            raise error

    index_persistence.register("first", SimpleNamespace(apply=first, publish=publish))
    index_persistence.register("second", SimpleNamespace(apply=second, publish=publish))
    builder = result.graph_builder
    builder.traversed_file_paths = {"locked.py"}
    if path == "incremental":
        monkeypatch.setattr(
            "repowise.core.pipeline.persist.persist_incremental_symbols",
            AsyncMock(side_effect=RuntimeError("earlier failure")),
        )
    diffs = [FileDiff("new.py", "renamed", "old.py", None, None, None)]
    with pytest.raises(RuntimeError) if outcome != "ok" else nullcontext() as raised:
        if path == "init":
            await persist_result(result, writer.root)
        elif path == "full":
            monkeypatch.setattr(
                "repowise.core.pipeline.run_pipeline", AsyncMock(return_value=result)
            )
            await index_repo_full(writer.root)
        elif path == "incremental":
            await persist_incremental_index(
                writer.root,
                builder,
                {},
                None,
                None,
                ["new.py"],
                parsed_files=parsed_files,
                file_diffs=diffs,
            )
        elif path == "rescore":
            await _rescore_health_from_db(writer.root, builder, parsed_files, ["gone.py"])
        else:
            await write_full_update(
                FullUpdatePersist(
                    repo_path=writer.root,
                    repo_name=writer.root.name,
                    graph_builder=builder,
                    git_meta_map={},
                    parsed_files=parsed_files,
                    changed_paths=["new.py"],
                    file_diffs=diffs,
                    degraded=["earlier failure"],
                    reconcile_full_scope=True,
                )
            )
    if outcome != "ok":
        assert raised.value is error
    async with writer.engine.connect() as conn:
        assert await conn.scalar(text("SELECT COUNT(*) FROM plugin_records")) == (
            0 if outcome == "apply" else 1
        )
        if path == "rescore":
            assert await conn.scalar(
                text("SELECT COUNT(*) FROM git_metadata WHERE file_path = 'gone.py'")
            ) == (1 if outcome == "apply" else 0)
    assert calls == ["first", "second"] + ["publish"] * (
        0 if outcome == "apply" else 1 if outcome == "publish" else 2
    )


@pytest.mark.parametrize("fail", [False, True])
async def test_checkpoint_applies_in_the_index_transaction(writer, fail):
    from repowise.core.pipeline.resume import ResumeController

    writes = []
    publish = AsyncMock()

    async def apply(session, repo_id, write):
        writes.append(write)
        assert repo_id == writer.repo_id
        assert session.in_transaction()
        await session.execute(text("INSERT INTO plugin_records VALUES ('checkpoint')"))
        if fail:
            raise RuntimeError("apply failed")

    index_persistence.register("test", SimpleNamespace(apply=apply, publish=publish))
    controller = ResumeController(writer.sf, writer.repo_id, resume=True)
    await controller.checkpoint_index(
        parsed_files=[],
        graph_builder=GraphBuilder(writer.root),
        git_metadata_list=[],
    )
    assert writes == [IndexWrite("checkpoint", [])]
    publish.assert_not_awaited()
    assert controller.index_persisted is (not fail)
    async with writer.engine.connect() as conn:
        assert await conn.scalar(text("SELECT COUNT(*) FROM plugin_records")) == (0 if fail else 1)


async def test_init_applies_again_after_checkpointed_analysis(writer):
    from repowise.cli.commands.init_cmd.persistence import persist_result
    from repowise.core.analysis.health.models import HealthReport
    from repowise.core.pipeline.resume import ResumeController

    result = writer.result
    fi = next(fi for fi in FileTraverser(writer.root).traverse() if fi.path == "new.py")
    parsed = ASTParser().parse_file(fi, (writer.root / "new.py").read_bytes())
    result.parsed_files = [parsed]
    result.file_infos = [fi]
    observed = []
    publish = AsyncMock()

    async def apply(session, repo_id, write):
        assert write.traversed_paths is None
        complexity = await session.scalar(text("SELECT complexity_estimate FROM wiki_symbols"))
        observed.append((write.mode, complexity))

    index_persistence.register("test", SimpleNamespace(apply=apply, publish=publish))
    controller = ResumeController(writer.sf, writer.repo_id, resume=True)
    await controller.checkpoint_index(
        parsed_files=result.parsed_files,
        graph_builder=result.graph_builder,
        git_metadata_list=[],
    )
    assert controller.index_persisted
    parsed.symbols[0].complexity_estimate = 4
    result.health_report = HealthReport(repo_id=writer.repo_id, analyzed_at=datetime.now(UTC))
    result.index_persisted_incrementally = True
    await persist_result(result, writer.root)

    assert observed == [("checkpoint", 1), ("full", 4)]
    publish.assert_awaited_once_with(writer.repo_id, writer.root)
