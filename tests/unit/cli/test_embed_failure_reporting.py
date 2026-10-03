"""A failed embed must not be reported as a healthy semantic index.

With a broken LanceDB install, ``init`` printed "Embedding failed", exited 0,
stamped ``search.semantic = available`` from the embedder's name alone, and
closed with a note that had nothing to do with what happened. These tests pin
the count that replaced the guess, the exit status, the stamped scope, the
closing card, and the ``reindex`` that clears it.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner
from rich.console import Console

from repowise.cli.providers import embed_failure_message, semantic_search_status
from repowise.core.generation.models import GeneratedPage


@pytest.mark.parametrize(
    ("embedder", "failed", "expected"),
    [
        ("openai", 0, "available"),
        ("openai", 2, "unavailable"),
        ("mock", 0, "unavailable"),
        (None, 0, "unavailable"),
    ],
)
def test_semantic_status_reads_what_the_run_did(embedder, failed, expected):
    assert semantic_search_status(embedder, failed) == expected


def test_only_a_real_embedder_that_failed_is_an_error():
    assert embed_failure_message("openai", 0) is None
    # The mock's vectors were never semantic; losing them changes nothing.
    assert embed_failure_message("mock", 5) is None
    message = embed_failure_message("ollama", 5)
    assert message is not None
    assert "5 page(s)" in message
    assert "repowise reindex" in message
    assert "pip install --force-reinstall lancedb" in message


def test_the_full_init_scope_marks_a_failed_embed_unavailable():
    from repowise.cli.commands.init_cmd.persistence import _stamp_full_init_scope

    def _stamp(failed: int) -> dict:
        state: dict = {}
        result = SimpleNamespace(
            health_report=object(),
            generation_scope={"effective_cap": 1, "eligible": 1, "generated": 1, "omitted": 0},
            traversal_stats=None,
            embed_failed_pages=failed,
        )
        _stamp_full_init_scope(
            state,
            result,
            SimpleNamespace(provider_name="openai", model_name="m"),
            resolved_commit_limit=10,
            max_file_pages=None,
            embedder_name_resolved="openai",
        )
        return state["index_scope"]["search"]

    assert _stamp(0) == {
        "full_text": "available",
        "semantic": "available",
        "next_command": None,
    }
    assert _stamp(3)["semantic"] == "unavailable"
    assert _stamp(3)["next_command"] == "repowise reindex"


def test_run_repo_generation_records_the_failed_count(tmp_path, monkeypatch):
    async def _fake_generation(**kwargs):
        kwargs["stats_out"]["embed_failed_pages"] = 4
        return []

    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd._generation_persist.run_generation_with_persistence",
        _fake_generation,
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation._enrich_knowledge_graph", lambda **kw: None
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.generation.console.print", lambda *a, **k: None
    )
    from repowise.cli.commands.init_cmd.generation import run_repo_generation

    result = SimpleNamespace(
        repo_name="r",
        parsed_files=[],
        source_map={},
        graph_builder=MagicMock(),
        repo_structure=MagicMock(),
        git_meta_map={},
        vector_store=object(),
    )
    run_repo_generation(
        repo_path=tmp_path,
        result=result,
        provider=SimpleNamespace(provider_name="template", model_name="template"),
        gen_config=SimpleNamespace(max_concurrency=1, deterministic=True),
        concurrency=1,
        embedder_name_resolved="mock",
        resume=False,
        verbose=False,
    )
    assert result.embed_failed_pages == 4


# ---------------------------------------------------------------------------
# Closing card
# ---------------------------------------------------------------------------


def _page() -> GeneratedPage:
    return GeneratedPage(
        page_id="file_page:a.py",
        page_type="file_page",
        title="a.py",
        content="content",
        source_hash="h",
        model_name="template",
        provider_name="template",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=2,
        target_path="a.py",
        created_at="2026-10-03T00:00:00Z",
        updated_at="2026-10-03T00:00:00Z",
    )


def _closing_card(tmp_path: Path, monkeypatch, *, semantic: str, failed: int) -> str:
    from repowise.cli.commands.init_cmd.reporting import show_completion

    (tmp_path / ".repowise").mkdir(exist_ok=True)
    (tmp_path / ".repowise" / "state.json").write_text(
        json.dumps({"index_scope": {"search": {"full_text": "available", "semantic": semantic}}}),
        encoding="utf-8",
    )
    buf = io.StringIO()
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.reporting.console",
        Console(file=buf, width=200, force_terminal=False),
    )
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd.reporting.build_completion_panel",
        lambda title, metrics, next_steps=None: "PANEL",
    )
    graph = MagicMock()
    graph.number_of_nodes.return_value = 1
    graph.number_of_edges.return_value = 0
    show_completion(
        repo_path=tmp_path,
        result=SimpleNamespace(
            graph_builder=MagicMock(graph=lambda: graph),
            dead_code_report=None,
            decision_report=None,
            git_summary=None,
            git_meta_map={},
            repo_structure=SimpleNamespace(root_language_distribution={"Python": 1.0}),
            generated_pages=[_page()],
            failed_page_ids=[],
            file_count=1,
            symbol_count=1,
            embed_failed_pages=failed,
        ),
        start=0.0,
        effective_index_only=True,
        run_mode="standard",
        provider=None,
    )
    return " ".join(buf.getvalue().split())


def test_card_names_a_failed_embed(tmp_path, monkeypatch):
    out = _closing_card(tmp_path, monkeypatch, semantic="unavailable", failed=3)
    assert "Semantic search is unavailable: embedding failed for 3 page(s)" in out
    assert "needs an embedder" not in out


def test_card_does_not_ask_for_an_embedder_the_run_used(tmp_path, monkeypatch):
    out = _closing_card(tmp_path, monkeypatch, semantic="available", failed=0)
    assert "and semantic search work now" in out
    assert "needs an embedder" not in out


def test_card_keeps_the_keyless_note(tmp_path, monkeypatch):
    out = _closing_card(tmp_path, monkeypatch, semantic="unavailable", failed=0)
    assert "semantic search needs an embedder" in out
    assert "Semantic search is unavailable" not in out


# ---------------------------------------------------------------------------
# Exit status
# ---------------------------------------------------------------------------


def _broken_store(monkeypatch):
    from repowise.core.persistence.vector_store import LanceDBVectorStore

    async def _boom(self, items):
        raise RuntimeError("LanceDB is missing or broken (AttributeError: connect_async)")

    monkeypatch.setattr(LanceDBVectorStore, "embed_batch", _boom)


def test_init_with_a_real_embedder_exits_nonzero_when_embedding_fails(tmp_path, monkeypatch):
    from repowise.cli.main import cli

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    _broken_store(monkeypatch)
    (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli, ["init", str(tmp_path), "--index-only", "--embedder", "ollama", "--yes"]
    )

    assert result.exit_code == 1, result.output
    assert "Embedding failed for" in result.output
    assert "repowise reindex" in result.output
    state = json.loads((tmp_path / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state["index_scope"]["search"]["semantic"] == "unavailable"
    assert state["index_scope"]["search"]["full_text"] == "available"


def test_keyless_init_still_exits_zero_when_the_store_fails(tmp_path, monkeypatch):
    from repowise.cli.main import cli

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    _broken_store(monkeypatch)
    (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")

    result = CliRunner().invoke(cli, ["init", str(tmp_path), "--index-only", "--yes"])

    assert result.exit_code == 0, result.output


def test_update_marks_semantic_unavailable_and_fails(tmp_path):
    from repowise.cli.commands.update_cmd.command import _fail_on_embed_failure
    from repowise.cli.helpers import save_state

    (tmp_path / ".repowise").mkdir()
    save_state(tmp_path, {"index_scope": {"search": {"semantic": "available"}}})
    emitter = MagicMock()

    with pytest.raises(click.ClickException, match="Embedding failed for 2 page"):
        _fail_on_embed_failure(tmp_path, "openai", 2, emitter)

    state = json.loads((tmp_path / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state["index_scope"]["search"]["semantic"] == "unavailable"
    assert state["index_scope"]["search"]["next_command"] == "repowise reindex"
    emitter.error.assert_called_once()


def test_update_with_the_mock_embedder_is_not_failed(tmp_path):
    from repowise.cli.commands.update_cmd.command import _fail_on_embed_failure

    _fail_on_embed_failure(tmp_path, "mock", 2, None)
    assert not (tmp_path / ".repowise" / "state.json").exists()


def test_the_deterministic_update_render_records_a_failed_embed(tmp_path, monkeypatch):
    """The index-only update passed no ``on_warning``, so nothing was recorded."""
    from repowise.cli.commands.update_cmd import deterministic

    class _Generator:
        embed_failed_pages = 0

        def __init__(self, *a, **k):
            pass

        async def generate_all(self, *a, **kwargs):
            kwargs["on_warning"]("Embedding failed for 2 page(s)")
            self.embed_failed_pages = 2
            return ["page"]

    monkeypatch.setattr("repowise.core.generation.PageGenerator", _Generator)
    monkeypatch.setattr(
        "repowise.core.generation.GenerationConfig.from_repo_config",
        lambda *a, **k: SimpleNamespace(language="en"),
    )
    monkeypatch.setattr("repowise.core.generation.ContextAssembler", lambda *a, **k: None)
    degraded: list[str] = []
    stats: dict[str, int] = {}

    pages = deterministic.regenerate_deterministic_page_ids(
        repo_path=tmp_path,
        parsed_files=[SimpleNamespace(file_info=SimpleNamespace(path="a.py"))],
        source_map={},
        graph_builder=None,
        repo_structure=None,
        git_meta_map={},
        page_ids={"file_page:a.py"},
        cfg={},
        concurrency=1,
        degraded=degraded,
        vector_store=object(),
        stats_out=stats,
    )

    assert pages == ["page"]
    assert degraded == ["Embedding failed for 2 page(s)"]
    assert stats == {"embed_failed_pages": 2}


def test_reindex_clears_the_unavailable_stamp(tmp_path, monkeypatch):
    """``init`` and ``update`` point at reindex; nothing else flipped it back."""
    from repowise.cli.commands import reindex_cmd
    from repowise.cli.helpers import save_state
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import upsert_page, upsert_repository
    from repowise.core.persistence.database import init_db
    from repowise.core.providers.embedding.base import MockEmbedder

    class _RealEmbedder:
        """Not the mock by type, so the reindex guard lets it through."""

        dimensions = 8

        async def embed(self, texts):
            return await MockEmbedder().embed(texts)

    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder",
        lambda name, _p=None: _RealEmbedder(),
    )
    repowise_dir = tmp_path / ".repowise"
    repowise_dir.mkdir()
    save_state(
        tmp_path,
        {"index_scope": {"search": {"semantic": "unavailable", "next_command": "repowise reindex"}}},
    )

    async def _seed():
        engine = create_engine(f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}")
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="r", local_path=str(tmp_path))
            await upsert_page(
                session,
                page_id="file_page:a.py",
                repository_id=repo.id,
                page_type="file_page",
                title="File: a.py",
                content="A module that parses configuration files and validates them. " * 5,
                summary="",
                target_path="a.py",
                source_hash="",
                model_name="m",
                provider_name="p",
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(_seed())
    monkeypatch.setattr(
        reindex_cmd, "get_db_url_for_repo", lambda p: f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}"
    )

    asyncio.run(reindex_cmd._reindex(tmp_path, "openai", 8))

    state = json.loads((repowise_dir / "state.json").read_text(encoding="utf-8"))
    assert state["index_scope"]["search"]["semantic"] == "available"
    assert state["index_scope"]["search"]["next_command"] is None


def test_workspace_init_persists_every_repo_then_exits_nonzero(tmp_path, monkeypatch):
    import git as gitpython

    from repowise.cli.main import cli

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    _broken_store(monkeypatch)
    for name in ("api", "web"):
        repo = tmp_path / name
        repo.mkdir()
        (repo / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        g = gitpython.Repo.init(repo)
        g.index.add(["app.py"])
        g.index.commit("init")

    result = CliRunner().invoke(
        cli,
        ["init", str(tmp_path), "--index-only", "--embedder", "ollama", "--yes", "--all"],
    )

    assert result.exit_code == 1, result.output
    assert "Embedding failed for" in " ".join(result.output.split())
    for name in ("api", "web"):
        state = json.loads((tmp_path / name / ".repowise" / "state.json").read_text("utf-8"))
        assert state["index_scope"]["search"]["semantic"] == "unavailable"
        assert state["index_scope"]["search"]["full_text"] == "available"


def _indexed_repo_with_broken_store(tmp_path, monkeypatch) -> Path:
    """An index-only repo pinned to a real embedder, with a store that fails.

    Init itself exits 1 here (its embed fails too); the stamp is reset to
    available so the update test proves the update is what marks it.
    """
    import subprocess

    from repowise.cli.helpers import load_state, save_state
    from repowise.cli.main import cli

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    _broken_store(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)

    git("init")
    git("config", "user.email", "t@t.test")
    git("config", "user.name", "T")
    (repo / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "initial")
    init = CliRunner().invoke(
        cli, ["init", str(repo), "--index-only", "--embedder", "ollama", "--yes", "--no-workspace"]
    )
    assert init.exit_code == 1, init.output
    state = load_state(repo)
    state["index_scope"]["search"]["semantic"] = "available"
    save_state(repo, state)

    (repo / "a.py").write_text("def alpha():\n    return 2\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "change")
    return repo


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="index-only render"),
        pytest.param(["--docs", "--provider", "mock"], id="model generation"),
    ],
)
def test_update_with_a_failing_store_exits_nonzero(tmp_path, monkeypatch, extra_args):
    from repowise.cli.main import cli

    repo = _indexed_repo_with_broken_store(tmp_path, monkeypatch)

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace", *extra_args])

    assert result.exit_code == 1, result.output
    assert "Embedding failed for" in " ".join(result.output.split())
    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state["index_scope"]["search"]["semantic"] == "unavailable"
    assert state["index_scope"]["search"]["next_command"] == "repowise reindex"
