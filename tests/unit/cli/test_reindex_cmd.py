"""Tests for the reindex CLI command internals."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import pytest

from repowise.cli.commands import reindex_cmd


class _DummyEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _EmptyResult:
    def scalars(self) -> _EmptyResult:
        return self

    def all(self) -> list[Any]:
        return []


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, _stmt: object) -> _EmptyResult:
        return _EmptyResult()


def _sessionmaker(*_args: object, **_kwargs: object):
    return _Session


async def test_reindex_uses_shared_database_engine(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"
    created: dict[str, object] = {}

    def fake_create_engine(url: str):
        engine = _DummyEngine()
        created["url"] = url
        created["engine"] = engine
        return engine

    async def fake_init_db(engine: object) -> None:
        created["init_engine"] = engine

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)
    monkeypatch.setattr(
        "repowise.core.persistence.database.create_engine",
        fake_create_engine,
    )
    monkeypatch.setattr("repowise.core.persistence.database.init_db", fake_init_db)
    monkeypatch.setattr("sqlalchemy.ext.asyncio.async_sessionmaker", _sessionmaker)

    await reindex_cmd._reindex(tmp_path, "openai", batch_size=20)

    assert created["url"] == db_url
    assert created["init_engine"] is created["engine"]
    assert created["engine"].disposed is True


async def test_reindex_recovers_pending_rows_before_loading_the_index(tmp_path, monkeypatch):
    pytest.importorskip("lancedb")
    import json
    from uuid import uuid4

    from repowise.core.persistence.database import create_engine, create_session_factory, init_db
    from repowise.core.persistence.models import Repository
    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    repo_id = "reindex-recovery-fixture"
    repowise_dir = tmp_path / ".repowise"
    repowise_dir.mkdir()
    engine = create_engine(f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}")
    try:
        await init_db(engine)
        async with create_session_factory(engine)() as session:
            session.add(Repository(id=repo_id, name="fixture", local_path=str(tmp_path)))
            await session.commit()
    finally:
        await engine.dispose()

    store = LanceDBVectorStore(str(repowise_dir / "lancedb"), MockEmbedder())
    key = "decision:restored"
    await store.upsert_vectors([(key, [0.25] * 8, {"title": "Retained"})])
    before = await store.snapshot_vectors({key})
    store.decision_vector_journal(repo_id).write_text(
        json.dumps({"revision": uuid4().hex, "previous": None, "rows": before}),
        encoding="utf-8",
    )
    await store.delete(key)
    await store.close()

    monkeypatch.setattr(reindex_cmd, "_embedder_for", lambda *_: ("mock", MockEmbedder()))
    await reindex_cmd._reindex(tmp_path, "mock", batch_size=20)

    assert await store.snapshot_vectors({key}) == before
    assert not store.decision_rekey_pending()
    await store.close()


async def test_reindex_aborts_when_every_item_failed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A reindex that indexed nothing but failed on every item must not exit 0.

    An automated pipeline (or an agent) treats a zero exit as a successful
    build, so an empty vector index after a total embedder failure would be
    mistaken for a healthy reindex.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"

    def fake_create_engine(url: str):
        return _DummyEngine()

    async def fake_init_db(engine: object) -> None:
        return None

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)
    monkeypatch.setattr(
        "repowise.core.persistence.database.create_engine",
        fake_create_engine,
    )
    monkeypatch.setattr("repowise.core.persistence.database.init_db", fake_init_db)
    monkeypatch.setattr("sqlalchemy.ext.asyncio.async_sessionmaker", _sessionmaker)

    # No pages in the DB → nothing to index, nothing failed → no abort.
    await reindex_cmd._reindex(tmp_path, "openai", batch_size=20)

    # Force the failure path: every item fails to embed. Patch the vector
    # store's embed_batch to raise, and give the store a page to process.
    class _FailingStore:
        batch_calls = 0
        single_calls = 0

        async def embed_batch(self, items: list[Any]) -> None:
            self.batch_calls += 1
            raise RuntimeError("embedder down")

        async def embed_and_upsert(self, *args: Any, **kwargs: Any) -> None:
            self.single_calls += 1
            raise RuntimeError("embedder down")

        async def close(self) -> None:
            return None

    class _Page:
        id = "p1"
        title = "Page"
        page_type = "file_page"
        target_path = "src/main.py"
        summary = ""
        content = "body"
        digest = ""
        metadata_json = "{}"
        decision = "a decision"

    class _FailingSession:
        async def __aenter__(self) -> _FailingSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def execute(self, _stmt: object) -> _EmptyResult:
            return _EmptyResult()

    class _FailingResult(_EmptyResult):
        def all(self) -> list[Any]:
            return [_Page()]

    class _FailingSession2:
        async def __aenter__(self) -> _FailingSession2:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def execute(self, _stmt: object) -> _FailingResult:
            return _FailingResult()

    def _failing_sessionmaker(*_args: object, **_kwargs: object):
        return _FailingSession2

    monkeypatch.setattr("sqlalchemy.ext.asyncio.async_sessionmaker", _failing_sessionmaker)
    store = _FailingStore()
    monkeypatch.setattr(
        "repowise.core.persistence.vector_store.LanceDBVectorStore",
        lambda *a, **k: store,
    )

    with pytest.raises(click.ClickException, match="failed in two consecutive batches") as exc_info:
        await reindex_cmd._reindex(tmp_path, "openai", batch_size=20)
    assert store.batch_calls == 2
    assert store.single_calls == 0
    # Two failed batches are as often a setting as an outage (issue #5738).
    assert "outage" not in exc_info.value.message
    assert "Last error: embedder down" in exc_info.value.message


def test_batch_recovery_keeps_a_refused_dimension_setting_terminal() -> None:
    """Control: a refused width stays terminal for the whole batch (issue #5738).

    httpx keeps the status on ``.response``, which ``_is_input_specific_error``
    does not read, so this held before the refusal became a ``ValueError`` too.
    """
    import httpx

    request = httpx.Request("POST", "http://localhost:11434/api/embed")
    refusal = httpx.HTTPStatusError(
        "400", request=request, response=httpx.Response(400, request=request)
    )
    exc = ValueError("Ollama refused dimensions=3072 ...")
    exc.__cause__ = refusal
    items = [(f"p{i}", "text", {}) for i in range(4)]

    successful, retry_items, terminal = reindex_cmd._batch_recovery_plan(exc, items)

    assert successful == 0
    assert retry_items == []
    assert [item for item, _stage, _exc in terminal] == items


def test_batch_recovery_retries_only_failed_input_chunk() -> None:
    from repowise.core.persistence.vector_store import BatchChunkFailure, BatchEmbeddingError

    class _BadRequest(Exception):
        status_code = 400

    items = [(f"p{i}", "text", {}) for i in range(32)]
    cause = _BadRequest("one input is invalid")
    exc = BatchEmbeddingError(
        failures=[BatchChunkFailure(tuple(items[16:]), "embedding", cause)],
        total_items=len(items),
    )

    successful, retry_items, terminal = reindex_cmd._batch_recovery_plan(exc, items)

    assert successful == 16
    assert retry_items == items[16:]
    assert terminal == []


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_batch_recovery_does_not_fan_out_provider_failure(status_code: int) -> None:
    from repowise.core.persistence.vector_store import BatchChunkFailure, BatchEmbeddingError

    class _ProviderError(Exception):
        pass

    items = [(f"p{i}", "text", {}) for i in range(32)]
    cause = _ProviderError("provider unavailable")
    cause.status_code = status_code  # type: ignore[attr-defined]
    exc = BatchEmbeddingError(
        failures=[BatchChunkFailure(tuple(items[:16]), "embedding", cause)],
        total_items=len(items),
    )

    successful, retry_items, terminal = reindex_cmd._batch_recovery_plan(exc, items)

    assert successful == 16
    assert retry_items == []
    assert [item[0][0] for item in terminal] == [f"p{i}" for i in range(16)]


def test_batch_recovery_never_reembeds_persistence_failure() -> None:
    from repowise.core.persistence.vector_store import BatchChunkFailure, BatchEmbeddingError

    items = [(f"p{i}", "text", {}) for i in range(16)]
    cause = RuntimeError("lancedb write failed")
    exc = BatchEmbeddingError(
        failures=[BatchChunkFailure(tuple(items), "persistence", cause)],
        total_items=len(items),
    )

    successful, retry_items, terminal = reindex_cmd._batch_recovery_plan(exc, items)

    assert successful == 0
    assert retry_items == []
    assert len(terminal) == len(items)
    assert {stage for _item, stage, _exc in terminal} == {"persistence"}


async def test_single_recovery_adaptively_truncates_confirmed_oversized_input() -> None:
    from repowise.core.persistence.vector_store._base import EMBED_TEXT_MAX_CHARS

    class _TooLong(Exception):
        status_code = 400

    class _Store:
        def __init__(self) -> None:
            self.text_lengths: list[int] = []

        async def embed_and_upsert(self, _page_id: str, text: str, _metadata: dict) -> None:
            self.text_lengths.append(len(text))
            if len(text) > EMBED_TEXT_MAX_CHARS // 2:
                raise _TooLong("maximum input length is 8192 tokens")

    store = _Store()
    await reindex_cmd._embed_one_with_input_recovery(
        store,
        ("oversized", "x" * (EMBED_TEXT_MAX_CHARS * 3), {}),
    )

    assert store.text_lengths == [EMBED_TEXT_MAX_CHARS, EMBED_TEXT_MAX_CHARS // 2]


async def test_single_recovery_does_not_retry_other_bad_requests() -> None:
    class _BadRequest(Exception):
        status_code = 400

    class _Store:
        calls = 0

        async def embed_and_upsert(self, _page_id: str, _text: str, _metadata: dict) -> None:
            self.calls += 1
            raise _BadRequest("invalid dimensions")

    store = _Store()
    with pytest.raises(_BadRequest, match="invalid dimensions"):
        await reindex_cmd._embed_one_with_input_recovery(store, ("bad", "text", {}))
    assert store.calls == 1


async def test_reindex_auto_honours_the_repo_pinned_embedder(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """`--embedder auto` must read config.yaml's pin, not just the environment.

    The pin is what wrote the vector table, so it is what must rewrite it. When
    `auto` resolved from env vars alone, a repo pinned to one embedder in a
    shell exporting another provider's key had its table rewritten by that other
    provider, leaving every reader — which does honour the pin — querying
    vectors of the wrong width.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"
    (tmp_path / ".repowise").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "embedder: ollama\n", encoding="utf-8"
    )

    built: dict[str, str] = {}

    def fake_build_embedder(name: str, _repo_path: object, **_kw: object):
        built["name"] = name
        raise click.Abort()

    # REPOWISE_EMBEDDER legitimately outranks the pin, so a test about pin
    # precedence has to clear it or it is asserting the wrong rule. It is also
    # genuinely leaked into this process by
    # test_embedder_resolution.py::test_the_reader_prefers_the_repo_pin, whose
    # `monkeypatch.delenv(..., raising=False)` records nothing when the variable
    # is already absent — so the value the production code then sets has no
    # saved state to restore and survives into later tests.
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    # An unrelated provider key exported in the environment. Before the fix this
    # is what `auto` resolved to, silently overriding the repo's own pin.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder", fake_build_embedder
    )

    with pytest.raises(click.Abort):
        await reindex_cmd._reindex(tmp_path, "auto", batch_size=20)

    assert built["name"] == "ollama"


_EMBEDDER_KEYS = (
    "REPOWISE_EMBEDDER",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "OLLAMA_EMBEDDING_MODEL",
    "EDENAI_API_KEY",
)


def _pin_mock(tmp_path: Path, monkeypatch) -> str:
    """A repo a keyless init left pinned to mock, in a shell with no embedder."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"
    (tmp_path / ".repowise").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".repowise" / "config.yaml").write_text("embedder: mock\n", encoding="utf-8")
    for var in _EMBEDDER_KEYS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "repowise.core.providers.embedding.resolve.global_config_path",
        lambda: tmp_path / "no-global.yaml",
    )
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)
    return db_url


async def test_auto_replaces_a_mock_pin_with_a_detected_embedder(
    monkeypatch, tmp_path: Path
) -> None:
    """A keyless init pins mock on its way out; that is no choice to keep.

    Keeping it meant ``repowise reindex`` never added semantic search, while
    every surface said it would.
    """
    _pin_mock(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    built: dict[str, str] = {}

    def fake_build_embedder(name: str, _repo_path: object, **_kw: object):
        built["name"] = name
        raise click.Abort()

    monkeypatch.setattr("repowise.cli.providers.embedders.build_embedder", fake_build_embedder)

    with pytest.raises(click.Abort):
        await reindex_cmd._reindex(tmp_path, "auto", batch_size=20)

    assert built["name"] == "openai"


async def test_auto_on_a_mock_pin_with_no_embedder_prints_the_command(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """Rebuilding test vectors adds nothing: stop, exit 1, name the way on."""
    _pin_mock(tmp_path, monkeypatch)

    def fail_build(*_args: object):
        raise AssertionError("no embedder should be built")

    monkeypatch.setattr("repowise.cli.providers.embedders.build_embedder", fail_build)

    with pytest.raises(click.exceptions.Exit) as exc:
        await reindex_cmd._reindex(tmp_path, "auto", batch_size=20)

    assert exc.value.exit_code == 1
    said = " ".join(capsys.readouterr().out.split())
    assert "semantic search stays off" in said
    assert "repowise reindex --embedder ollama" in said
    assert "GEMINI_API_KEY" in said
    assert not (tmp_path / ".repowise" / "lancedb").exists()


async def test_an_explicit_mock_still_means_mock(monkeypatch, tmp_path: Path) -> None:
    """``--embedder mock`` is a request for test vectors, honoured as asked."""
    _pin_mock(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(
        "repowise.core.persistence.database.create_engine", lambda _url: _DummyEngine()
    )

    async def fake_init_db(_engine: object) -> None:
        return None

    monkeypatch.setattr("repowise.core.persistence.database.init_db", fake_init_db)
    monkeypatch.setattr("sqlalchemy.ext.asyncio.async_sessionmaker", _sessionmaker)

    # No Abort and no Exit: reaches the normal empty-database path and returns.
    await reindex_cmd._reindex(tmp_path, "mock", batch_size=20)


async def test_an_explicit_mock_never_replaces_a_semantic_store(
    monkeypatch, tmp_path: Path
) -> None:
    """``--embedder mock`` over a store a real embedder built refuses before writing.

    8-wide test vectors in a 1536-wide table make LanceDB drop the table, so a
    reindex with the mock wiped every vector a paid embedder had written. It
    now goes through the same guard as every other store path, and names the
    one step that replaces the store on purpose.
    """
    from repowise.cli.providers.vector_store import existing_vector_dim
    from repowise.core.persistence.vector_store.lancedb_store import record_vector_dim

    db_url = await _seed_pages(tmp_path, {"a.py": "a"})
    lance_dir = tmp_path / ".repowise" / "lancedb"
    (lance_dir / "wiki_pages.lance").mkdir(parents=True)
    record_vector_dim(lance_dir, "wiki_pages", 1536)
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)

    def no_store(*_a: object, **_k: object) -> None:
        raise AssertionError("the semantic store was opened for mock writes")

    monkeypatch.setattr("repowise.core.persistence.vector_store.LanceDBVectorStore", no_store)

    with pytest.raises(click.ClickException) as refused:
        await reindex_cmd._reindex(tmp_path, "mock", batch_size=20)

    said = refused.value.message
    assert "1536-wide" in said
    assert str(lance_dir) in said
    assert "delete that folder" in said
    assert existing_vector_dim(lance_dir) == 1536


async def test_an_explicit_mock_rebuilds_a_mock_store(monkeypatch, tmp_path: Path) -> None:
    """A store already at the mock's width holds nothing to lose: mock still writes."""
    from repowise.core.persistence.vector_store.lancedb_store import record_vector_dim
    from repowise.core.providers.embedding.base import MockEmbedder

    db_url = await _seed_pages(tmp_path, {"a.py": "a"})
    lance_dir = tmp_path / ".repowise" / "lancedb"
    (lance_dir / "wiki_pages.lance").mkdir(parents=True)
    record_vector_dim(lance_dir, "wiki_pages", MockEmbedder.dimensions)

    class _RecordingStore:
        def __init__(self) -> None:
            self.ids: list[str] = []

        async def embed_batch(self, items: list[Any]) -> None:
            self.ids.extend(item[0] for item in items)

        async def close(self) -> None:
            return None

    store = _RecordingStore()
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)
    monkeypatch.setattr(
        "repowise.core.persistence.vector_store.LanceDBVectorStore", lambda *a, **k: store
    )

    await reindex_cmd._reindex(tmp_path, "mock", batch_size=20)

    assert store.ids == ["file_page:a.py"]


async def _seed_pages(
    tmp_path: Path, pages: dict[str, str], *, tombstoned: tuple[str, ...] = ()
) -> str:
    """Create the wiki database with one page per ``path: title``; return its URL."""
    from sqlalchemy import text

    from repowise.core.persistence.crud import upsert_page, upsert_repository
    from repowise.core.persistence.database import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
    )

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"
    engine = create_engine(db_url)
    await init_db(engine)
    async with get_session(create_session_factory(engine)) as session:
        repo = await upsert_repository(session, name="r", local_path=str(tmp_path))
        for path, title in pages.items():
            await upsert_page(
                session,
                page_id=f"file_page:{path}",
                repository_id=repo.id,
                page_type="file_page",
                title=title,
                content="a body long enough to be worth embedding " * 4,
                target_path=path,
                source_hash="h",
                model_name="mock",
                provider_name="mock",
                input_tokens=1,
                output_tokens=1,
            )
        for path in tombstoned:
            await session.execute(
                text("UPDATE wiki_pages SET freshness_status = 'tombstone' WHERE id = :id"),
                {"id": f"file_page:{path}"},
            )
        await session.commit()
    await engine.dispose()
    return db_url


async def test_reindex_skips_tombstoned_pages(monkeypatch, tmp_path: Path) -> None:
    """A tombstone is out of both indexes by design: update removes its vector
    and doctor reports a leftover one as an orphan that ``--repair`` deletes.
    Embedding it here meant reindex and ``doctor --repair`` undid each other on
    every run."""
    db_url = await _seed_pages(
        tmp_path, {"live.py": "live", "gone.py": "gone"}, tombstoned=("gone.py",)
    )

    class _RecordingStore:
        def __init__(self) -> None:
            self.ids: list[str] = []

        async def embed_batch(self, items: list[Any]) -> None:
            self.ids.extend(item[0] for item in items)

        async def close(self) -> None:
            return None

    store = _RecordingStore()
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)
    monkeypatch.setattr(
        "repowise.core.persistence.vector_store.LanceDBVectorStore",
        lambda *a, **k: store,
    )

    await reindex_cmd._reindex(tmp_path, "mock", batch_size=20)

    assert store.ids == ["file_page:live.py"]


# --- what a reindex leaves behind when it changes the embedder (#5673) -------


async def _switch_embedder(
    monkeypatch,
    tmp_path: Path,
    to: str,
    *,
    config: str,
    pages: dict[str, str] | None = None,
    pending_edit: str = "",
    fail: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    """Reindex a stamped repo with *to*; return its config, state and config changes.

    *pending_edit* is appended to config.yaml after the stamp: a hand edit no
    update has seen yet.
    """
    from repowise.cli.commands.update_cmd.config_changes import classify_config_changes
    from repowise.cli.helpers import load_config, load_state, save_state
    from repowise.cli.providers import semantic_search_status
    from repowise.core.repo_config import config_dependency_fingerprints, config_fingerprint

    (tmp_path / ".repowise").mkdir()
    # What a keyless run leaves in place of a store.
    (tmp_path / ".repowise" / "lancedb.keyless").touch()
    (tmp_path / ".repowise" / "config.yaml").write_text(config, encoding="utf-8")
    old = load_config(tmp_path)["embedder"]
    save_state(
        tmp_path,
        {
            "config_fingerprint": config_fingerprint(tmp_path),
            "config_dependency_fingerprints": config_dependency_fingerprints(tmp_path),
            "index_scope": {
                "provider": {"embedder": old},
                "search": {"semantic": semantic_search_status(old, 0)},
            },
        },
    )
    with (tmp_path / ".repowise" / "config.yaml").open("a", encoding="utf-8") as fh:
        fh.write(pending_edit)
    db_url = await _seed_pages(tmp_path, pages or {"a.py": "a.py"})

    class _Store:
        async def embed_batch(self, items: list[Any]) -> None:
            if fail:
                raise RuntimeError("embedder down")

        async def close(self) -> None:
            return None

    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)
    monkeypatch.setattr(
        "repowise.core.persistence.vector_store.LanceDBVectorStore", lambda *a, **k: _Store()
    )
    if to != "mock":
        monkeypatch.setattr(
            "repowise.cli.providers.embedders.build_embedder", lambda _n, _p=None, **_kw: object()
        )

    await reindex_cmd._reindex(tmp_path, to, batch_size=20)

    cfg, state = load_config(tmp_path), load_state(tmp_path)
    return cfg, state, classify_config_changes(tmp_path, state, cfg)


async def test_a_clean_embedder_switch_leaves_nothing_for_update_to_rebuild(
    monkeypatch, tmp_path: Path
) -> None:
    """The reindex just did the embed; the next update must not do it again."""
    cfg, state, changes = await _switch_embedder(
        monkeypatch, tmp_path, "openai", config="embedder: mock\nembedding_model: mock-model\n"
    )

    assert cfg["embedder"] == "openai"
    # A model pinned for the old embedder would now be read as the new one's.
    assert "embedding_model" not in cfg
    assert state["index_scope"]["provider"]["embedder"] == "openai"
    assert not changes.changed
    assert not changes.rebuild_required


async def test_a_clean_switch_clears_the_keyless_marker(monkeypatch, tmp_path: Path) -> None:
    """Once the wiki is re-embedded, ``update`` stops proposing the keyless backfill."""
    await _switch_embedder(monkeypatch, tmp_path, "openai", config="embedder: mock\n")

    assert not (tmp_path / ".repowise" / "lancedb.keyless").exists()


async def test_a_failed_switch_keeps_the_keyless_marker(monkeypatch, tmp_path: Path) -> None:
    """A run that embedded nothing leaves the marker, so the next ``update`` retries."""
    with pytest.raises(click.Abort):
        await _switch_embedder(
            monkeypatch, tmp_path, "openai", config="embedder: mock\n", fail=True
        )

    assert (tmp_path / ".repowise" / "lancedb.keyless").is_file()


async def test_a_partial_embedder_switch_still_asks_update_to_rebuild(
    monkeypatch, tmp_path: Path
) -> None:
    """Pages failed, so the embed is not done: the fingerprints stay and update rebuilds."""
    cfg, state, changes = await _switch_embedder(
        monkeypatch,
        tmp_path,
        "openai",
        config="embedder: mock\nembedding_model: mock-model\n",
        pages={"a.py": "a.py", "b.py": ""},  # a blank title is counted as a failure
    )

    assert cfg["embedder"] == "openai"
    assert "embedding_model" not in cfg
    assert state["index_scope"]["provider"]["embedder"] == "openai"
    assert changes.generation_changed
    assert changes.rebuild_required


async def test_a_clean_embedder_switch_keeps_a_pending_config_edit_for_update(
    monkeypatch, tmp_path: Path
) -> None:
    """Only a fingerprint that still matched moves: update has not seen this edit."""
    _cfg, _state, changes = await _switch_embedder(
        monkeypatch,
        tmp_path,
        "openai",
        config="embedder: mock\n",
        pending_edit="exclude_patterns: [vendor]\n",
    )

    assert changes.traversal_changed
    assert changes.rebuild_required


async def test_a_switch_names_the_model_pin_it_removes(monkeypatch, tmp_path: Path) -> None:
    """The embed did not read a pin bound to the old embedder; say it is gone."""
    warnings: list[str] = []
    monkeypatch.setattr(reindex_cmd, "warn", warnings.append)
    cfg, _state, _changes = await _switch_embedder(
        monkeypatch,
        tmp_path,
        "openai",
        config="embedder: mock\nembedding_model: text-embedding-3-large\n",
    )

    assert "embedding_model" not in cfg
    assert any("Removed embedding_model: text-embedding-3-large" in w for w in warnings)


async def test_a_switch_to_mock_is_named_in_the_scope(monkeypatch, tmp_path: Path) -> None:
    _cfg, state, _changes = await _switch_embedder(
        monkeypatch, tmp_path, "mock", config="embedder: openai\n"
    )

    assert state["index_scope"]["provider"]["embedder"] == "mock"
    # Test vectors: the scope must not go on calling semantic search available.
    assert state["index_scope"]["search"]["semantic"] == "unavailable"


async def test_a_reindex_with_the_same_embedder_keeps_its_model_pin(
    monkeypatch, tmp_path: Path
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(reindex_cmd, "warn", warnings.append)
    cfg, _state, changes = await _switch_embedder(
        monkeypatch, tmp_path, "openai", config="embedder: openai\nembedding_model: small\n"
    )

    assert cfg["embedding_model"] == "small"
    assert not warnings
    assert not changes.rebuild_required


async def test_a_broken_config_stops_the_reindex_before_it_embeds(
    monkeypatch, tmp_path: Path
) -> None:
    """The config cannot be written, so say which file to fix, and before the embed."""
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text("embedder: [mock\n", encoding="utf-8")
    db_url = await _seed_pages(tmp_path, {"a.py": "a.py"})
    embedded: list[Any] = []

    class _Store:
        async def embed_batch(self, items: list[Any]) -> None:
            embedded.extend(items)

        async def close(self) -> None:
            return None

    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)
    monkeypatch.setattr(
        "repowise.core.persistence.vector_store.LanceDBVectorStore", lambda *a, **k: _Store()
    )

    with pytest.raises(
        click.ClickException, match=r"(?s)config\.yaml.*Nothing was saved"
    ) as raised:
        await reindex_cmd._reindex(tmp_path, "mock", batch_size=20)

    assert getattr(raised.value, "reason", None) == "config_unreadable"
    assert not embedded


async def test_a_switch_pins_the_model_set_only_in_the_shell(monkeypatch, tmp_path: Path) -> None:
    """OLLAMA_EMBEDDING_MODEL in one shell: the MCP server must embed queries with it too."""
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
    cfg, _state, changes = await _switch_embedder(
        monkeypatch, tmp_path, "ollama", config="embedder: mock\n"
    )

    assert cfg["embedder"] == "ollama"
    assert cfg["embedding_model"] == "nomic-embed-text"
    assert not changes.rebuild_required


async def test_a_missing_ollama_model_stops_on_the_first_batch_with_the_pull_command(
    monkeypatch, tmp_path: Path
) -> None:
    from repowise.core.providers.embedding.ollama import OllamaModelMissingError

    db_url = await _seed_pages(tmp_path, {"a.py": "a.py", "b.py": "b.py", "c.py": "c.py"})
    missing = OllamaModelMissingError(
        "Ollama has no model 'embeddinggemma'. Run `ollama pull embeddinggemma`, "
        "then re-run this command."
    )

    class _Store:
        calls = 0

        async def embed_batch(self, items: list[Any]) -> None:
            _Store.calls += 1
            raise missing

        async def close(self) -> None:
            return None

    warnings: list[str] = []
    monkeypatch.setattr(reindex_cmd, "warn", warnings.append)
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)
    monkeypatch.setattr(
        "repowise.core.persistence.vector_store.LanceDBVectorStore", lambda *a, **k: _Store()
    )
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder", lambda _n, _p=None, **_kw: object()
    )

    with pytest.raises(click.ClickException) as exc_info:
        await reindex_cmd._reindex(tmp_path, "ollama", batch_size=1)

    assert _Store.calls == 1
    assert not warnings
    assert exc_info.value.message == str(missing)


async def test_an_env_model_that_replaces_a_pin_says_so(monkeypatch, tmp_path: Path) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(reindex_cmd, "warn", warnings.append)
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
    cfg, _state, _changes = await _switch_embedder(
        monkeypatch,
        tmp_path,
        "ollama",
        config="embedder: ollama\nembedding_model: embeddinggemma\n",
    )

    assert cfg["embedding_model"] == "nomic-embed-text"
    assert warnings == [
        "embedding_model in config.yaml changed from embeddinggemma to nomic-embed-text: "
        "this run embedded with the model the environment names."
    ]
