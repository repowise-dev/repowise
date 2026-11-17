"""``repowise workspace add`` defaults.

By default an added repo gets documentation built from code: a configured
provider or a key in the environment never makes it spend unasked. These tests
pin the decision matrix in :func:`_resolve_docs_flag`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands.workspace_cmd import _resolve_docs_flag
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig


@pytest.fixture
def ws(tmp_path: Path) -> tuple[Path, WorkspaceConfig]:
    backend = tmp_path / "backend"
    (backend / ".git").mkdir(parents=True)
    (backend / ".repowise").mkdir(parents=True)
    cfg = WorkspaceConfig(
        version=1,
        repos=[RepoEntry(path="backend", alias="backend", is_primary=True)],
        default_repo="backend",
    )
    cfg.save(tmp_path)
    return tmp_path, cfg


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    """Strip every provider key so tests start from a clean slate."""
    for key in (
        "REPOWISE_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
        "KIMI_API_KEY",
        "OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def _mode(run_docs=None, provider_name=None):
    return _resolve_docs_flag(run_docs=run_docs, provider_name=provider_name)


def test_explicit_docs_uses_the_model():
    assert _mode(run_docs=True) == ("llm", None)


def test_explicit_no_docs_records_reason():
    assert _mode(run_docs=False) == ("none", "--no-docs flag")


def test_provider_flag_uses_the_model():
    assert _mode(provider_name="anthropic") == ("llm", None)


def test_default_builds_from_code_even_with_a_configured_provider(ws, monkeypatch):
    ws_root, _cfg = ws
    (ws_root / "backend" / ".repowise" / "config.yaml").write_text(
        "provider: anthropic\nmodel: claude-sonnet-4-5\n", encoding="utf-8"
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.chdir(ws_root)
    assert _mode() == ("deterministic", None)


# ---------------------------------------------------------------------------
# distill verdict inheritance — a repo added to a workspace must not
# silently re-enable rewrites the workspace declined at init.
# ---------------------------------------------------------------------------


def _new_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "newrepo"
    (repo / ".repowise").mkdir(parents=True)
    return repo


def _distill_enabled(repo: Path) -> object:
    import yaml

    cfg_path = repo / ".repowise" / "config.yaml"
    if not cfg_path.exists():
        return None
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return ((cfg.get("distill") or {}).get("commands") or {}).get("enabled")


def test_inherits_primary_distill_decline(tmp_path: Path):
    from repowise.cli.commands.workspace_cmd import _inherit_distill_verdict

    repo = _new_repo(tmp_path)
    _inherit_distill_verdict(repo, {"distill": {"commands": {"enabled": False}}})
    assert _distill_enabled(repo) is False


def test_inherits_primary_distill_optin(tmp_path: Path):
    from repowise.cli.commands.workspace_cmd import _inherit_distill_verdict

    repo = _new_repo(tmp_path)
    _inherit_distill_verdict(repo, {"distill": {"commands": {"enabled": True}}})
    assert _distill_enabled(repo) is True


def test_no_primary_verdict_leaves_config_untouched(tmp_path: Path):
    from repowise.cli.commands.workspace_cmd import _inherit_distill_verdict

    repo = _new_repo(tmp_path)
    _inherit_distill_verdict(repo, {})
    _inherit_distill_verdict(repo, {"distill": "garbage"})
    _inherit_distill_verdict(repo, {"distill": {"commands": {"permission": "allow"}}})
    assert not (repo / ".repowise" / "config.yaml").exists()


# ---------------------------------------------------------------------------
# inherit_workspace_distill_verdict — the post-update backfill for members
# that got `.repowise/` outside the init flow.
# ---------------------------------------------------------------------------


def _ws_with_primary_verdict(tmp_path: Path, enabled: bool) -> Path:
    """Workspace root with a primary whose distill verdict is *enabled*."""
    root = tmp_path / "wsroot"
    (root / "primary" / ".repowise").mkdir(parents=True)
    (root / "primary" / ".repowise" / "config.yaml").write_text(
        f"distill:\n  commands:\n    enabled: {str(enabled).lower()}\n",
        encoding="utf-8",
    )
    (root / "member" / ".repowise").mkdir(parents=True)
    cfg = WorkspaceConfig(
        version=1,
        repos=[
            RepoEntry(path="primary", alias="primary", is_primary=True),
            RepoEntry(path="member", alias="member"),
        ],
        default_repo="primary",
    )
    cfg.save(root)
    return root


def test_backfill_inherits_primary_decline(tmp_path: Path):
    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict

    root = _ws_with_primary_verdict(tmp_path, enabled=False)
    inherit_workspace_distill_verdict(root / "member")
    assert _distill_enabled(root / "member") is False


def test_backfill_skips_member_with_own_verdict(tmp_path: Path):
    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict

    root = _ws_with_primary_verdict(tmp_path, enabled=False)
    (root / "member" / ".repowise" / "config.yaml").write_text(
        "distill:\n  commands:\n    enabled: true\n", encoding="utf-8"
    )
    inherit_workspace_distill_verdict(root / "member")
    assert _distill_enabled(root / "member") is True


def test_backfill_noop_without_repowise_dir(tmp_path: Path):
    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict

    root = _ws_with_primary_verdict(tmp_path, enabled=False)
    bare = root / "bare"
    bare.mkdir()
    inherit_workspace_distill_verdict(bare)
    assert not (bare / ".repowise").exists()


def test_backfill_noop_outside_workspace(tmp_path: Path):
    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict

    repo = _new_repo(tmp_path)
    inherit_workspace_distill_verdict(repo)
    assert not (repo / ".repowise" / "config.yaml").exists()


def test_backfill_noop_for_primary_itself(tmp_path: Path):
    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict

    root = _ws_with_primary_verdict(tmp_path, enabled=False)
    before = (root / "primary" / ".repowise" / "config.yaml").read_text(encoding="utf-8")
    inherit_workspace_distill_verdict(root / "primary")
    after = (root / "primary" / ".repowise" / "config.yaml").read_text(encoding="utf-8")
    assert before == after
