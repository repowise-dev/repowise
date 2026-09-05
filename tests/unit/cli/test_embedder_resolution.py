"""Embedder resolution across the keyless-init upgrade path.

The path these cover is the one the keyless default exists to serve: index for
free, decide it is useful, add a key, upgrade. Every writer used to read the
pinned embedder from ``config.yaml`` while every reader resolved one from the
environment, so the upgrade produced a fully written wiki that semantic search
could not read, and nothing in the output said so.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from repowise.cli import providers
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.core.upgrade import UpgradeActionKind, UpgradeTier, assess
from repowise.core.upgrade.version import STORE_FORMAT_VERSION

lancedb = pytest.importorskip("lancedb")


def _write_config(repo_path: Path, **keys: object) -> None:
    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir(parents=True, exist_ok=True)
    (repowise_dir / "config.yaml").write_text(yaml.safe_dump(keys), encoding="utf-8")


def _read_config(repo_path: Path) -> dict:
    return yaml.safe_load((repo_path / ".repowise" / "config.yaml").read_text(encoding="utf-8"))


class _WideEmbedder:
    """Stand-in for a hosted embedder, without the SDK or the key."""

    dimensions = 1536

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1535 + [1.0] for _ in texts]


async def _seed_store(repo_path: Path, embedder: object) -> None:
    """Write one real row so the table exists at *embedder*'s width."""
    store = providers.build_vector_store(repo_path, embedder)
    assert store is not None
    await store.embed_and_upsert("file_page:a.py", "hello", {"title": "a"})
    await store.close()


# --- the write guard: a mock must never drop a real table -------------------


async def test_mock_store_is_refused_over_a_real_width_table(tmp_path: Path) -> None:
    """The whole-store-wipe hazard, hoisted into the one shared constructor.

    Writing 8-wide vectors into a 1536-wide table makes LanceDB drop the table,
    taking every page *and* decision embedding with it. Silently.
    """
    await _seed_store(tmp_path, _WideEmbedder())
    assert providers.existing_vector_dim(tmp_path / ".repowise" / "lancedb") == 1536

    assert providers.build_vector_store(tmp_path, MockEmbedder()) is None

    # And the real table is still there, unharmed.
    assert providers.existing_vector_dim(tmp_path / ".repowise" / "lancedb") == 1536


async def test_mock_store_is_allowed_over_a_mock_table(tmp_path: Path) -> None:
    """Matching widths are not a conflict — the keyless path must still work."""
    await _seed_store(tmp_path, MockEmbedder())
    assert providers.build_vector_store(tmp_path, MockEmbedder()) is not None


def test_mock_store_is_allowed_when_no_table_exists(tmp_path: Path) -> None:
    """A first keyless init has nothing to clobber."""
    assert providers.build_vector_store(tmp_path, MockEmbedder()) is not None


async def test_real_embedder_still_rebuilds_a_differing_table(tmp_path: Path) -> None:
    """Only the mock is refused; a real re-embed is the intended rebuild."""
    await _seed_store(tmp_path, MockEmbedder())
    assert providers.build_vector_store(tmp_path, _WideEmbedder()) is not None


def test_existing_vector_dim_is_none_when_unreadable(tmp_path: Path) -> None:
    """Unknown never means "different" — the guard stays quiet, not guessy."""
    assert providers.existing_vector_dim(tmp_path / "nope") is None
    (tmp_path / "empty").mkdir()
    assert providers.existing_vector_dim(tmp_path / "empty") is None


# --- readers resolve from the pin, not the environment ----------------------


def test_reader_prefers_the_pinned_embedder_over_a_detected_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A keyless repo must not be queried with whatever key is exported.

    The pin is what wrote the table, so it is what can read it. Resolving from
    the environment sends 1536-wide query vectors at an 8-wide table.
    """
    _write_config(tmp_path, embedder="mock")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    assert providers.resolve_embedder_for_repo(tmp_path) == "mock"


def test_an_explicit_env_override_still_beats_the_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``serve`` honours ``REPOWISE_EMBEDDER`` over the pin, so this must too.

    Otherwise the same repo in the same shell resolves one embedder under
    ``serve`` and a different one under ``search``.
    """
    _write_config(tmp_path, embedder="mock")
    monkeypatch.setenv("REPOWISE_EMBEDDER", "openai")

    assert providers.resolve_embedder_for_repo(tmp_path) == "openai"


def test_semantic_search_reads_through_the_repo_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The call site, not just the helper.

    ``search`` used to resolve from the environment; a helper test alone would
    keep passing if that were reverted. Since ``search`` collapsed onto the
    ``search_codebase`` tool, the call site is the bridge that builds the store
    for every adapter command, so this now covers all of them at once.
    """
    import asyncio

    from repowise.cli import tool_bridge

    _write_config(tmp_path, embedder="mock")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    (tmp_path / ".repowise" / "lancedb").mkdir(parents=True, exist_ok=True)

    seen: list[str] = []

    def _record(name: str, _repo_path=None):
        seen.append(name)
        return MockEmbedder()

    monkeypatch.setattr("repowise.cli.providers.embedders.build_embedder", _record)

    asyncio.run(tool_bridge._open_vector_store(tmp_path))

    assert seen == ["mock"], seen


def test_serve_seeds_a_mock_pin_into_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pin is a width, not a preference to be talked out of.

    Skipping "mock" here sent a keyless repo down the detection path, which
    picks up any exported key and then queries an 8-wide table with 1536-wide
    vectors.
    """
    from repowise.cli.commands import serve_cmd

    _write_config(tmp_path, embedder="mock")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)

    serve_cmd._load_local_provider_config()

    assert os.environ.get("REPOWISE_EMBEDDER") == "mock"


def test_reader_falls_back_to_the_environment_when_nothing_is_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    assert providers.resolve_embedder_for_repo(tmp_path) == "openai"


# --- reindex records what it built the table with ---------------------------


async def test_reindex_persists_its_resolved_embedder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without this, the next routine ``update`` wipes the store.

    ``reindex`` builds a real 1536-wide table; ``update`` then reads
    ``embedder: mock`` from config, builds a mock store, and the first write
    hits the dimension mismatch and drops the table. The reindex is undone by
    an update the user did not connect to it.
    """
    from repowise.cli.commands import reindex_cmd

    _write_config(tmp_path, embedder="mock", language="en")

    class _Page:
        id = "file_page:a.py"
        title = "a"
        content = "hello"
        page_type = "file_page"
        target_path = "a.py"
        # Non-nullable on the real model, so a stand-in that omits it is a
        # stand-in for a page that cannot exist.
        summary = "what a.py does"

    class _Result:
        def __init__(self, rows: list) -> None:
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class _Session:
        _calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, _stmt):
            type(self)._calls += 1
            return _Result([_Page()] if type(self)._calls == 1 else [])

    class _Engine:
        async def dispose(self):
            return None

    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _p: "sqlite+aiosqlite://")
    monkeypatch.setattr("repowise.core.persistence.database.create_engine", lambda _u: _Engine())
    monkeypatch.setattr("repowise.core.persistence.database.init_db", lambda _e: _noop_coro())
    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.async_sessionmaker", lambda *a, **k: lambda: _Session()
    )
    # _reindex imports build_embedder from this module inside the function, so
    # the module attribute is the one that takes effect.
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder", lambda _n, _p=None: _WideEmbedder()
    )

    await reindex_cmd._reindex(tmp_path, "openai", batch_size=8)

    assert _read_config(tmp_path)["embedder"] == "openai"
    # The unrelated key survives the merge.
    assert _read_config(tmp_path)["language"] == "en"


async def test_reindex_does_not_pin_an_embedder_that_wrote_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pin describes the table, so a run with no pages describes nothing.

    Writing it anyway points later writers at a width the table does not have.
    """
    from repowise.cli.commands import reindex_cmd

    _write_config(tmp_path, embedder="mock")

    class _Empty:
        def scalars(self):
            return self

        def all(self):
            return []

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, _stmt):
            return _Empty()

    class _Engine:
        async def dispose(self):
            return None

    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _p: "sqlite+aiosqlite://")
    monkeypatch.setattr("repowise.core.persistence.database.create_engine", lambda _u: _Engine())
    monkeypatch.setattr("repowise.core.persistence.database.init_db", lambda _e: _noop_coro())
    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.async_sessionmaker", lambda *a, **k: lambda: _Session()
    )
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder", lambda _n, _p=None: _WideEmbedder()
    )

    await reindex_cmd._reindex(tmp_path, "openai", batch_size=8)

    assert _read_config(tmp_path)["embedder"] == "mock"


async def _noop_coro() -> None:
    return None


# --- "mock" in config is not a choice anyone made ---------------------------


def test_persisted_mock_does_not_count_as_a_requested_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second ``init`` must not silently bill a hosted embedder.

    ``embedder: mock`` is what a keyless run writes on its way out. Treating it
    as an explicit request makes the re-init resolve a paid embedder and embed
    the whole wiki through it, on a run whose own header says no model, no cost.
    """
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)

    for pinned in (None, "", "  ", "mock"):
        assert providers.embedder_was_requested(None, pinned) is False


def test_a_real_pin_does_count_as_a_requested_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The re-init case the mock carve-out must not break.

    Dropping a real pin to the mock would re-embed the store at a different
    width, which the LanceDB writer resolves by dropping the table.
    """
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)

    for pinned in ("openai", "gemini", "ollama"):
        assert providers.embedder_was_requested(None, pinned) is True
    # An explicit flag or env var is a request regardless of the pin.
    assert providers.embedder_was_requested("openai", "mock") is True
    monkeypatch.setenv("REPOWISE_EMBEDDER", "gemini")
    assert providers.embedder_was_requested(None, "mock") is True


def test_pin_predicate_ignores_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The workspace phase folds in the pin only, never the env, a second time.

    Its caller has already weighed the flag and ``REPOWISE_EMBEDDER`` and passed
    down a verdict. Re-reading them there would let a stray env var override a
    caller that decided "not requested", and put a hosted embedder on the bill
    for a run advertised as free.
    """
    monkeypatch.setenv("REPOWISE_EMBEDDER", "openai")

    assert providers.pin_names_an_embedder("mock") is False
    assert providers.pin_names_an_embedder(None) is False
    assert providers.pin_names_an_embedder("gemini") is True


# --- the upgrade layer detects a real embedder change -----------------------


def test_width_mismatch_triggers_an_auto_reembed() -> None:
    """The check that actually fires.

    The model-name comparison it sits beside reads ``REPOWISE_EMBEDDING_MODEL``
    on both sides, which almost nobody sets, so both are ``None`` and it
    no-ops. Width is not opt-in.
    """
    verdict = assess(
        {"store_format_version": STORE_FORMAT_VERSION},
        stored_vector_dim=8,
        current_vector_dim=1536,
    )
    assert verdict.tier == UpgradeTier.AUTO
    assert UpgradeActionKind.REEMBED_VECTORS in [a.kind for a in verdict.actions]


def test_matching_width_is_a_noop() -> None:
    verdict = assess(
        {"store_format_version": STORE_FORMAT_VERSION},
        stored_vector_dim=1536,
        current_vector_dim=1536,
    )
    assert verdict.is_noop


def test_unknown_width_never_triggers() -> None:
    """Either side unknown means "cannot tell", which must not mean "changed"."""
    for stored, current in ((8, None), (None, 1536), (None, None)):
        verdict = assess(
            {"store_format_version": STORE_FORMAT_VERSION},
            stored_vector_dim=stored,
            current_vector_dim=current,
        )
        assert verdict.is_noop


def _patch_stored_dim(monkeypatch: pytest.MonkeyPatch, dim: int | None) -> None:
    # _vector_dims imports this locally, so the module attribute is the one
    # that takes effect.
    monkeypatch.setattr("repowise.cli.providers.vector_store.existing_vector_dim", lambda _d: dim)


def test_mock_width_with_a_real_pin_proposes_a_re_embed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The leftover-keyless-vectors case, which is the one worth acting on."""
    from repowise.cli.upgrade import _vector_dims

    _write_config(tmp_path, embedder="openai")
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    _patch_stored_dim(monkeypatch, 8)
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder", lambda _n, _p=None: _WideEmbedder()
    )

    assert _vector_dims(tmp_path) == (8, 1536)


def test_two_real_widths_are_never_compared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-embed loop this must not have.

    ``dimensions`` is a lookup against a short hardcoded table, so an Ollama
    model outside that table reports 768 when it really produces 1024. Acting
    on that writes the true width back, so the next run sees the same
    disagreement, forever — a full-wiki embedding bill on every ``update``.
    Stored widths that are not the mock's are therefore never compared.
    """
    from repowise.cli.upgrade import _vector_dims

    _write_config(tmp_path, embedder="ollama")
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    _patch_stored_dim(monkeypatch, 1024)

    class _MisreportingEmbedder:
        dimensions = 768  # the hardcoded guess, not what it actually emits

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 1024 for _ in texts]

    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder", lambda _n, _p=None: _MisreportingEmbedder()
    )

    assert _vector_dims(tmp_path) == (None, None)


def test_mock_pin_never_proposes_re_embedding_a_real_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-embedding a real store down to 8-dim hashes is pure loss."""
    from repowise.cli.upgrade import _vector_dims

    _write_config(tmp_path, embedder="mock")
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    _patch_stored_dim(monkeypatch, 1536)

    assert _vector_dims(tmp_path) == (None, None)


def test_a_mock_pin_never_reads_the_stored_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The keyless default must not open LanceDB to learn nothing.

    Reading the stored width costs an ``import lancedb`` (1.7s measured, versus
    0.09s for the connect and schema read it is there for), and ``assess_store``
    runs on every ``update`` including the no-op path a post-commit hook fires
    on. A mock pin can never produce a verdict, so the read is pure waste and
    the guards are ordered to skip it. Asserting the call count rather than the
    timing: this is the invariant, the seconds are its consequence.
    """
    from repowise.cli.upgrade import _vector_dims

    _write_config(tmp_path, embedder="mock")
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)

    calls: list[Path] = []

    def _spy(lance_dir):
        calls.append(lance_dir)
        return 8

    monkeypatch.setattr("repowise.cli.providers.vector_store.existing_vector_dim", _spy)

    assert _vector_dims(tmp_path) == (None, None)
    assert calls == []


def test_a_real_pin_still_reads_the_stored_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The saving must not have been bought by skipping the check that matters."""
    from repowise.cli.upgrade import _vector_dims

    _write_config(tmp_path, embedder="openai")
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)

    calls: list[Path] = []

    def _spy(lance_dir):
        calls.append(lance_dir)
        return 8

    monkeypatch.setattr("repowise.cli.providers.vector_store.existing_vector_dim", _spy)
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder", lambda _n, _p=None: _WideEmbedder()
    )

    assert _vector_dims(tmp_path) == (8, 1536)
    assert len(calls) == 1


async def test_auto_reembed_refuses_to_run_with_a_mock_embedder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other auto-action can reach the executor on a mock-pinned repo.

    Letting it through would rewrite every vector in the wiki as an 8-dim hash.
    """
    from repowise.cli import upgrade as upgrade_mod

    _write_config(tmp_path, embedder="mock")
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)

    called = False

    async def _boom(*_a: object, **_k: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("repowise.cli.commands.reindex_cmd._reindex", _boom)

    await upgrade_mod._CliUpgradeContext(tmp_path).reembed_vectors()

    assert called is False


# --- doctor --fix referenced a column that does not exist ------------------


def test_page_has_no_page_id_attribute() -> None:
    """Why ``doctor --fix``'s repair never ran.

    Both its FTS and vector branches filtered on ``Page.page_id``. The natural
    primary key is spelled ``id``; ``page_id`` on the model is a *different*
    table's foreign key. So the repair raised AttributeError every time, and
    the vector branch's ``except`` reported it as "Vector repair skipped".
    """
    from repowise.core.persistence.models import Page

    assert hasattr(Page, "id")
    assert not hasattr(Page, "page_id")


def test_duplicate_reembed_actions_run_once() -> None:
    """Two checks can both conclude "re-embed"; the API bill should not double."""
    import asyncio

    from repowise.core.upgrade import UpgradeAction, UpgradeVerdict, apply_auto

    calls = 0

    class _Ctx:
        async def reembed_vectors(self) -> None:
            nonlocal calls
            calls += 1

        def drop_parse_cache(self) -> None:
            return None

    verdict = UpgradeVerdict(
        tier=UpgradeTier.AUTO,
        from_store_version=STORE_FORMAT_VERSION,
        to_store_version=STORE_FORMAT_VERSION,
        written_by=None,
        actions=(
            UpgradeAction(kind=UpgradeActionKind.REEMBED_VECTORS, reason="model changed"),
            UpgradeAction(kind=UpgradeActionKind.REEMBED_VECTORS, reason="width changed"),
        ),
        user_notice=None,
        reindex_command=None,
    )
    asyncio.run(apply_auto(verdict, _Ctx()))
    assert calls == 1


# --- build_embedder must say when it degrades -----------------------------
#
# The fallback to keyless embeddings used to be a bare ``except`` that returned
# a KeylessEmbedder with nothing printed anywhere. ``--embedder ollama`` against
# a stopped Ollama therefore produced a repo that indexed and searched without
# complaint and had no semantic retrieval at all, while ``doctor`` reported
# healthy. The server path already recorded this as ``degraded: True``; the CLI
# path recorded nothing at all.


def _capture_console(monkeypatch) -> list[str]:
    """Collect what build_embedder prints, without a real terminal.

    It warns on **stderr**: the caller's stdout may be a JSON document (see
    ``test_output_agent_readiness.py``), and a warning printed in front of it
    breaks every parser reading it.
    """
    from repowise.cli import helpers

    printed: list[str] = []
    monkeypatch.setattr(
        helpers.err_console, "print", lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    )
    return printed


def test_build_embedder_reports_a_failed_real_backend(monkeypatch):
    from repowise.core.providers.embedding.base import KeylessEmbedder

    def _boom(name: str, **kwargs: object):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("repowise.core.providers.embedding.registry.get_embedder", _boom)
    printed = _capture_console(monkeypatch)

    embedder = providers.build_embedder("ollama")

    # Still falls back rather than crashing the run: full-text search is
    # unaffected and a hard failure here would break indexing outright.
    assert isinstance(embedder, KeylessEmbedder)
    said = " ".join(printed)
    # The three things a user needs: which backend, why, and what it costs them.
    assert "ollama" in said
    assert "connection refused" in said
    assert "semantic search" in said.lower()


def test_build_embedder_is_silent_for_the_keyless_default(monkeypatch):
    """``mock`` is what a no-key run resolves to. Reaching it is not a failure
    and must not print a warning, or every keyless run cries wolf."""
    from repowise.core.providers.embedding.base import KeylessEmbedder

    printed = _capture_console(monkeypatch)
    embedder = providers.build_embedder("mock")

    assert isinstance(embedder, KeylessEmbedder)
    assert printed == []


# --- the global config's ``embedder`` name, which had no reader -------------


def _clear_embedder_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "REPOWISE_EMBEDDER",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "OLLAMA_EMBEDDING_MODEL",
        "EDENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def _write_global_config(home: Path, **keys: object) -> None:
    global_dir = home / ".repowise"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "config.yaml").write_text(yaml.safe_dump(keys), encoding="utf-8")


def test_global_config_embedder_is_used_when_nothing_is_exported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect: a configured backend resolved to ``mock``, then pinned it."""
    from repowise.cli.providers.embedders import resolve_embedder

    _clear_embedder_env(monkeypatch)
    _write_global_config(tmp_path, embedder="openai", embedder_api_key="sk-test")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

    assert resolve_embedder(None) == "openai"


def test_a_pinned_mock_is_not_a_choice_anyone_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored ``mock`` is what a keyless run persisted, not a choice."""
    from repowise.cli.providers.keys import global_config_embedder

    _clear_embedder_env(monkeypatch)
    _write_global_config(tmp_path, embedder="mock")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

    assert global_config_embedder() is None


def test_the_environment_still_outranks_the_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exported key is an explicit override and nothing may outrank it."""
    from repowise.cli.providers.embedders import resolve_embedder

    _clear_embedder_env(monkeypatch)
    _write_global_config(tmp_path, embedder="openai", embedder_api_key="sk-test")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "gk-test")

    assert resolve_embedder(None) == "gemini"


def test_an_explicit_flag_still_outranks_the_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.cli.providers.embedders import resolve_embedder

    _clear_embedder_env(monkeypatch)
    _write_global_config(tmp_path, embedder="openai", embedder_api_key="sk-test")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

    assert resolve_embedder("ollama") == "ollama"


def test_no_global_config_still_resolves_keyless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The keyless default has to survive: it is the advertised no-key path."""
    from repowise.cli.providers.embedders import resolve_embedder

    _clear_embedder_env(monkeypatch)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

    assert resolve_embedder(None) == "mock"


@pytest.mark.parametrize(
    "body",
    [
        "embedder: [unclosed",  # throws inside safe_load
        "just a bare string",  # parses to str, then .get raises
        "- a",  # parses to list, same
    ],
)
def test_a_malformed_global_config_resolves_keyless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """A hand-edited config must not take the run down.

    The scalar and list cases parse *successfully*, so only an isinstance
    check catches them, and ``resolve_embedder`` is on every command's path.
    """
    from repowise.cli.providers.embedders import resolve_embedder
    from repowise.cli.providers.keys import global_config_embedder

    _clear_embedder_env(monkeypatch)
    global_dir = tmp_path / ".repowise"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "config.yaml").write_text(body, encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

    assert global_config_embedder() is None
    assert resolve_embedder(None) == "mock"


def test_a_global_backend_with_no_key_is_not_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``serve`` writes ``embedder`` unconditionally, the key only if given.

    By this tier the environment and the repo ``.env`` are known empty, so a
    keyed backend with no ``embedder_api_key`` can only fail to build, and the
    name would still be pinned against a store the fallback wrote at 8-wide.
    """
    from repowise.cli.providers.embedders import resolve_embedder

    _clear_embedder_env(monkeypatch)
    _write_global_config(tmp_path, embedder="openai")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

    assert resolve_embedder(None) == "mock"


def test_a_keyless_global_backend_is_still_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama needs no key, so the absent-key rule must not swallow it."""
    from repowise.cli.providers.embedders import resolve_embedder

    _clear_embedder_env(monkeypatch)
    _write_global_config(tmp_path, embedder="ollama")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

    assert resolve_embedder(None) == "ollama"


def test_the_repo_env_overlay_outranks_the_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The precedence edge this change actually introduces."""
    from repowise.cli.providers.embedders import resolve_embedder

    _clear_embedder_env(monkeypatch)
    _write_global_config(tmp_path, embedder="gemini", embedder_api_key="gk-test")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

    assert resolve_embedder(None, {"OPENAI_API_KEY": "sk-x"}) == "openai"


def test_repowise_embedder_outranks_the_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.cli.providers.embedders import resolve_embedder

    _clear_embedder_env(monkeypatch)
    _write_global_config(tmp_path, embedder="openai", embedder_api_key="sk-test")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setenv("REPOWISE_EMBEDDER", "edenai")

    assert resolve_embedder(None) == "edenai"


def test_a_repo_pin_outranks_the_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The width invariant: the pin wrote the table, so the pin queries it."""
    from repowise.cli.providers.embedders import resolve_embedder_for_repo

    _clear_embedder_env(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(repo, embedder="mock")
    _write_global_config(tmp_path, embedder="openai", embedder_api_key="sk-test")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

    assert resolve_embedder_for_repo(repo) == "mock"
