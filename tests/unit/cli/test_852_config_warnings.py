"""Issue #852: swallowed configuration errors surface as CLI warnings.

Each test poisons one swallow site from the #852 audit and asserts the
warning reaches default (non-verbose) CLI output: the embedder mock fallback
(init/update), the decision-provider skip (init --index-only), the
REPOWISE_FULL_RESCORE_INTERVAL_DAYS env parse (update), and the semantic
search fallback (search).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli

# ---------------------------------------------------------------------------
# Helpers (mirror test_update_e2e.py)
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    return result.stdout.strip()


def _make_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.py").write_text("def alpha():\n    return 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _index_full(repo: Path) -> None:
    """Real index-only init: full pipeline + persistence."""
    from repowise.core.pipeline.full_index import index_repo_full

    asyncio.run(index_repo_full(repo))


# ---------------------------------------------------------------------------
# Update path
# ---------------------------------------------------------------------------


def test_update_warns_embedder_degradation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A requested ollama embedder with a malformed timeout degrades to mock;
    the update must surface it in the degraded panel (R3/R4)."""
    repo = _make_git_repo(tmp_path)
    _index_full(repo)

    base = _git(repo, "rev-parse", "HEAD")
    from repowise.cli.helpers import save_state

    # docs_mode "llm" routes the update through the full path, which builds
    # the decision vector store (the surface #852 wants covered); the mock
    # provider keeps the run free.
    save_state(repo, {"last_sync_commit": base, "docs_mode": "llm"})

    (repo / "c.py").write_text("def gamma():\n    return 3\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add c.py")

    # The update reads the embedder from the config pin, not the env.
    (repo / ".repowise" / "config.yaml").write_text("embedder: ollama\n")
    monkeypatch.setenv("REPOWISE_EMBEDDER", "ollama")
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    monkeypatch.setenv("OLLAMA_EMBEDDING_TIMEOUT", "abc")

    result = CliRunner().invoke(
        cli, ["update", str(repo), "--no-workspace", "--provider", "mock"]
    )

    assert result.exit_code == 0, result.output
    assert "degraded step(s)" in result.output
    assert "Embedder: ollama" in result.output
    assert "OLLAMA_EMBEDDING_TIMEOUT" in result.output
    assert "[yellow]Warning:[/yellow]" not in result.output


def test_update_warns_vector_store_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed vector-store build degrades visibly (plan U2 scenario 2)."""
    repo = _make_git_repo(tmp_path)
    _index_full(repo)

    base = _git(repo, "rev-parse", "HEAD")
    from repowise.cli.helpers import save_state

    save_state(repo, {"last_sync_commit": base, "docs_mode": "llm"})

    (repo / "c.py").write_text("def gamma():\n    return 3\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add c.py")

    import repowise.cli.providers as providers_mod

    def _boom(*_args, **_kwargs):
        raise RuntimeError("store exploded")

    monkeypatch.setattr(providers_mod, "build_vector_store", _boom)

    result = CliRunner().invoke(
        cli, ["update", str(repo), "--no-workspace", "--provider", "mock"]
    )

    assert result.exit_code == 0, result.output
    assert "degraded step(s)" in result.output
    assert "Decision vector store" in result.output


def test_full_rescore_interval_warns_on_invalid_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """REPOWISE_FULL_RESCORE_INTERVAL_DAYS=abc warns and falls back (R5)."""
    from repowise.cli.commands.update_cmd.persistence import (
        _FULL_RESCORE_INTERVAL_DAYS,
        _full_rescore_interval_days,
    )

    monkeypatch.setenv("REPOWISE_FULL_RESCORE_INTERVAL_DAYS", "abc")
    assert _full_rescore_interval_days() == _FULL_RESCORE_INTERVAL_DAYS
    out = capsys.readouterr().out
    assert "REPOWISE_FULL_RESCORE_INTERVAL_DAYS" in out
    assert "abc" in out


def test_full_rescore_interval_valid_env_stays_quiet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A valid interval emits no warning (R8)."""
    from repowise.cli.commands.update_cmd.persistence import _full_rescore_interval_days

    monkeypatch.setenv("REPOWISE_FULL_RESCORE_INTERVAL_DAYS", "3.5")
    assert _full_rescore_interval_days() == 3.5
    assert "Warning" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Init path
# ---------------------------------------------------------------------------


def test_init_header_warns_embedder_degradation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init with a requested ollama embedder + malformed timeout warns in the
    header while the run proceeds unchanged (R3)."""
    repo = _make_git_repo(tmp_path)

    monkeypatch.setenv("REPOWISE_EMBEDDER", "ollama")
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    monkeypatch.setenv("OLLAMA_EMBEDDING_TIMEOUT", "abc")

    result = CliRunner().invoke(cli, ["init", str(repo), "--provider", "mock", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Embedder: ollama" in result.output
    assert "Warning" in result.output
    assert "OLLAMA_EMBEDDING_TIMEOUT" in result.output


def test_init_auto_detected_embedder_degradation_stays_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An embedder auto-detected from an API key that the run would downgrade
    to mock by policy must not read as a failure warning (R8)."""
    repo = _make_git_repo(tmp_path)

    monkeypatch.setenv("GEMINI_API_KEY", "sk-test")
    monkeypatch.setenv("REPOWISE_EMBEDDING_DIMS", "abc")

    result = CliRunner().invoke(cli, ["init", str(repo), "--provider", "mock", "--yes"])

    assert result.exit_code == 0, result.output
    assert "embedder unavailable" not in result.output


def test_init_index_only_warns_named_provider_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init --index-only with a named provider that fails warns instead of
    silently skipping decision extraction (R6)."""
    repo = _make_git_repo(tmp_path)

    import repowise.cli.commands.init_cmd.command as init_cmd

    def _boom(*_args, **_kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(init_cmd, "resolve_provider", _boom)

    result = CliRunner().invoke(cli, ["init", str(repo), "--index-only", "--provider", "ollama"])

    assert result.exit_code == 0, result.output
    assert "Decision extraction unavailable" in result.output
    assert "provider exploded" in result.output


def test_init_index_only_keyless_stays_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A keyless non-tty index-only run is the intended no-provider flow and
    must not warn (R8: additive warnings only)."""
    repo = _make_git_repo(tmp_path)
    result = CliRunner().invoke(cli, ["init", str(repo), "--index-only", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Decision extraction unavailable" not in result.output


def test_init_index_only_keyless_provider_failure_stays_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even when provider resolution fails, a keyless run must not be told
    decision extraction failed — it never intended to run it."""
    repo = _make_git_repo(tmp_path)

    import repowise.cli.commands.init_cmd.command as init_cmd

    def _boom(*_args, **_kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(init_cmd, "resolve_provider", _boom)

    result = CliRunner().invoke(cli, ["init", str(repo), "--index-only", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Decision extraction unavailable" not in result.output


# ---------------------------------------------------------------------------
# Search path
# ---------------------------------------------------------------------------


def test_search_semantic_warns_and_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing semantic search warns before the FTS fallback (R7)."""
    from repowise.core.pipeline.full_index import index_repo_full

    repo = _make_git_repo(tmp_path)
    asyncio.run(index_repo_full(repo))
    # A mock-embedder index writes no LanceDB dir; create one so the semantic
    # path enters the try block, then make the store search fail.
    (repo / ".repowise" / "lancedb").mkdir()

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("vector store on fire")

    monkeypatch.setattr(
        "repowise.core.persistence.vector_store.LanceDBVectorStore.search", _boom
    )

    result = CliRunner().invoke(cli, ["search", "alpha", str(repo), "--mode", "semantic"])

    assert result.exit_code == 0, result.output
    assert "Semantic search unavailable" in result.output
    assert "full-text" in result.output


def test_search_warns_on_degraded_embedder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pinned embedder that degrades to mock warns in search, even when the
    store query itself would succeed (the #852 headline case)."""
    from repowise.core.pipeline.full_index import index_repo_full

    repo = _make_git_repo(tmp_path)
    asyncio.run(index_repo_full(repo))
    (repo / ".repowise" / "lancedb").mkdir()

    (repo / ".repowise" / "config.yaml").write_text("embedder: ollama\n")
    monkeypatch.setenv("REPOWISE_EMBEDDER", "ollama")
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    monkeypatch.setenv("OLLAMA_EMBEDDING_TIMEOUT", "abc")

    result = CliRunner().invoke(cli, ["search", "alpha", str(repo), "--mode", "semantic"])

    assert result.exit_code == 0, result.output
    assert "ollama embedder unavailable" in result.output
    assert "OLLAMA_EMBEDDING_TIMEOUT" in result.output


def test_search_semantic_empty_results_do_not_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty-but-real semantic result must not warn or fall back to FTS."""
    from repowise.core.pipeline.full_index import index_repo_full

    repo = _make_git_repo(tmp_path)
    asyncio.run(index_repo_full(repo))
    (repo / ".repowise" / "lancedb").mkdir()

    async def _empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        "repowise.core.persistence.vector_store.LanceDBVectorStore.search", _empty
    )

    result = CliRunner().invoke(cli, ["search", "alpha", str(repo), "--mode", "semantic"])

    assert result.exit_code == 0, result.output
    assert "Warning" not in result.output
    assert "No results found." in result.output
