"""``repowise update`` onboards a provider when a docs run needs one.

A repo can start life ``--index-only`` and later flip ``docs_enabled`` on (via
config or ``repowise update --docs``) without a provider/key ever being
configured. In an interactive terminal the docs run should prompt for the
provider + key exactly like ``init`` does, persist the choice, and continue,
rather than dying with "No provider configured". In a non-interactive run
(hook / CI / ``--progress json``) it must stay a clean, non-blocking failure.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import yaml
from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.cli.ui.provider_selection import ProviderSelection

# ---------------------------------------------------------------------------
# Helpers
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
    # Ignore the index dir so `git add .` never commits repowise's own
    # artifacts — commit hashes then track only the source changes below.
    (repo / ".gitignore").write_text(".repowise/\n")
    (repo / "a.py").write_text("def alpha():\n    return 1\n")
    (repo / "b.py").write_text("from a import alpha\n\n\ndef beta():\n    return alpha() + 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _index_full(repo: Path) -> None:
    """Real index-only init: full pipeline + persistence, no provider/key."""
    from repowise.core.pipeline.full_index import index_repo_full

    asyncio.run(index_repo_full(repo))


def _commit_change(repo: Path, name: str, body: str) -> str:
    (repo / name).write_text(body)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD")


def _config(repo: Path) -> dict:
    text = (repo / ".repowise" / "config.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def _prep_docs_repo_without_provider(tmp_path: Path) -> tuple[Path, str]:
    """Index-only repo that now wants docs, with no provider/key configured."""
    repo = _make_git_repo(tmp_path)
    _index_full(repo)
    base = _git(repo, "rev-parse", "HEAD")

    from repowise.cli.helpers import save_state

    # docs_enabled flipped on after an index-only start; the docs pointer sits
    # at the indexed commit so the changed file below is docs work.
    save_state(repo, {"last_sync_commit": base, "last_docs_commit": base, "docs_enabled": True})
    new_head = _commit_change(
        repo, "c.py", "from b import beta\n\n\ndef gamma():\n    return beta() * 2\n"
    )
    return repo, new_head


class _StubPrompt:
    """Stand-in for init's interactive provider prompt.

    Records call count and mimics the real prompt's persistence side effect:
    it sets the key in the environment and writes it to ``.repowise/.env``,
    exactly as ``interactive_provider_config_select`` does for a real provider,
    then returns a selection for the (unselectable-in-table) ``mock`` provider.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, console, model, reasoning=None, *, repo_path=None):
        self.calls += 1
        import os

        from repowise.cli.ui.env_persistence import _save_key_to_dotenv

        os.environ["MOCK_API_KEY"] = "sk-mock-test-key"
        if repo_path is not None:
            _save_key_to_dotenv(repo_path, "MOCK_API_KEY", "sk-mock-test-key")
        return ProviderSelection("mock", "mock-model-1", "auto")


def _force_interactive(monkeypatch) -> None:
    """Open the docs-prompt gate as if a real interactive terminal were present.

    The gate requires both a terminal stdout and a tty stdin; ``CliRunner``
    provides neither, so patch the named gate helper directly rather than
    faking two low-level tty signals.
    """
    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd.command._docs_provider_prompt_allowed",
        lambda emitter: True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_update_prompts_and_persists_when_docs_needs_a_provider(tmp_path, monkeypatch) -> None:
    repo, new_head = _prep_docs_repo_without_provider(tmp_path)

    stub = _StubPrompt()
    monkeypatch.setattr("repowise.cli.ui.interactive_provider_config_select", stub, raising=False)
    _force_interactive(monkeypatch)

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    assert result.exit_code == 0, result.output

    # The prompt fired exactly once to onboard the provider.
    assert stub.calls == 1

    # provider/model persisted to config, key persisted to .repowise/.env.
    cfg = _config(repo)
    assert cfg.get("provider") == "mock"
    assert cfg.get("model") == "mock-model-1"
    env_text = (repo / ".repowise" / ".env").read_text(encoding="utf-8")
    assert "MOCK_API_KEY=sk-mock-test-key" in env_text

    # Docs were actually generated: the docs pointer advanced to HEAD.
    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state["last_docs_commit"] == new_head

    # A second docs run reuses the persisted provider without re-prompting.
    # run_update releases its single-flight lock via atexit, which fires at
    # process exit — but back-to-back CliRunner invokes share one process, so
    # release it here to mimic the real per-process boundary (otherwise run2
    # would bail as "another update already running").
    from repowise.cli.helpers import release_update_lock

    release_update_lock(repo)
    second_head = _commit_change(
        repo, "d.py", "from c import gamma\n\n\ndef delta():\n    return gamma() + 1\n"
    )
    result2 = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    assert result2.exit_code == 0, result2.output
    assert stub.calls == 1, "provider already persisted; must not prompt again"
    state2 = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state2["last_docs_commit"] == second_head


def test_update_non_interactive_stays_a_clean_failure(tmp_path, monkeypatch) -> None:
    repo, _ = _prep_docs_repo_without_provider(tmp_path)

    stub = _StubPrompt()
    monkeypatch.setattr("repowise.cli.ui.interactive_provider_config_select", stub, raising=False)
    # No _force_interactive: under CliRunner stdin is not a tty and the console
    # is not a terminal, so the prompt gate is closed (the hook / CI case).

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])

    # Clean, non-blocking failure — never prompted, never hung.
    assert result.exit_code != 0
    assert stub.calls == 0
    assert "No provider configured" in result.output
