"""Unit tests for repowise.cli.helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from repowise.cli.helpers import (
    CONFIG_FILENAME,
    ensure_repowise_dir,
    find_repowise_repo_root,
    get_db_url_for_repo,
    get_head_commit,
    get_repowise_dir,
    load_state,
    resolve_provider,
    resolve_reasoning,
    resolve_repo_path,
    run_async,
    save_state,
    validate_provider_config,
)

# ---------------------------------------------------------------------------
# run_async
# ---------------------------------------------------------------------------


class TestRunAsync:
    def test_returns_coroutine_result(self):
        async def _add(a, b):
            return a + b

        assert run_async(_add(3, 4)) == 7

    def test_raises_exception_from_coroutine(self):
        async def _fail():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_async(_fail())


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


class TestResolveRepoPath:
    def test_none_defaults_to_cwd(self):
        result = resolve_repo_path(None)
        assert result == Path.cwd().resolve()

    def test_resolves_relative_path(self, tmp_path):
        import os

        old = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = resolve_repo_path(".")
            assert result == tmp_path.resolve()
        finally:
            os.chdir(old)

    def test_resolves_absolute_path(self, tmp_path):
        result = resolve_repo_path(str(tmp_path))
        assert result == tmp_path.resolve()


class TestFindRepowiseRepoRoot:
    def test_finds_parent_repowise_dir(self, tmp_path):
        root = tmp_path / "repo"
        nested = root / "src" / "pkg"
        nested.mkdir(parents=True)
        (root / ".repowise").mkdir()

        assert find_repowise_repo_root(nested) == root.resolve()

    def test_returns_none_when_missing(self, tmp_path):
        assert find_repowise_repo_root(tmp_path) is None

    def test_ignores_nested_git_dirs(self, tmp_path):
        root = tmp_path / "repo"
        nested = root / "vendor" / "dep" / "src"
        nested.mkdir(parents=True)
        (root / ".repowise").mkdir()
        (root / "vendor" / "dep" / ".git").mkdir()

        assert find_repowise_repo_root(nested) == root.resolve()


# ---------------------------------------------------------------------------
# .repowise/ directory
# ---------------------------------------------------------------------------


class TestrepowiseDir:
    def test_get_repowise_dir(self, tmp_path):
        assert get_repowise_dir(tmp_path) == tmp_path / ".repowise"

    def test_ensure_repowise_dir_creates(self, tmp_path):
        d = ensure_repowise_dir(tmp_path)
        assert d.exists()
        assert d == tmp_path / ".repowise"

    def test_ensure_repowise_dir_idempotent(self, tmp_path):
        ensure_repowise_dir(tmp_path)
        d = ensure_repowise_dir(tmp_path)
        assert d.exists()


# ---------------------------------------------------------------------------
# DB URL
# ---------------------------------------------------------------------------


class TestDbUrl:
    def test_defaults_to_repo_local_database(self, tmp_path):
        url = get_db_url_for_repo(tmp_path)
        expected_path = (tmp_path / ".repowise" / "wiki.db").as_posix()
        assert url == f"sqlite+aiosqlite:///{expected_path}"
        assert (tmp_path / ".repowise").exists()


class TestResolveReasoning:
    def test_flag_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("REPOWISE_REASONING", "minimal")
        assert resolve_reasoning("off", {"reasoning": "auto"}) == "off"

    def test_env_wins_over_config(self, monkeypatch):
        monkeypatch.setenv("REPOWISE_REASONING", "high")
        assert resolve_reasoning(config={"reasoning": "off"}) == "high"

    def test_config_wins_over_default(self, monkeypatch):
        monkeypatch.delenv("REPOWISE_REASONING", raising=False)
        assert resolve_reasoning(config={"reasoning": "xhigh"}) == "xhigh"


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


class TestStateFile:
    def test_load_missing_returns_empty(self, tmp_path):
        ensure_repowise_dir(tmp_path)
        assert load_state(tmp_path) == {}

    def test_save_and_load_roundtrip(self, tmp_path):
        ensure_repowise_dir(tmp_path)
        state = {"last_sync_commit": "abc123", "total_pages": 42}
        save_state(tmp_path, state)
        loaded = load_state(tmp_path)
        assert loaded == state

    def test_save_creates_repowise_dir(self, tmp_path):
        save_state(tmp_path, {"key": "value"})
        assert (tmp_path / ".repowise" / "state.json").exists()


# ---------------------------------------------------------------------------
# save_config_partial
# ---------------------------------------------------------------------------


class TestSaveConfigPartial:
    def test_save_config_partial_persists_exclude_patterns(self, tmp_path):
        """save_config_partial should merge keys into existing config.yaml."""
        from repowise.cli.helpers import load_config, save_config_partial

        rw_dir = tmp_path / ".repowise"
        rw_dir.mkdir()
        (rw_dir / "config.yaml").write_text("embedder: minilm\n", encoding="utf-8")

        save_config_partial(tmp_path, exclude_patterns=[".claude/", "tools/"])

        cfg = load_config(tmp_path)
        assert cfg["exclude_patterns"] == [".claude/", "tools/"]
        assert cfg["embedder"] == "minilm"  # existing keys preserved

    def test_save_config_partial_noop_when_no_values(self, tmp_path):
        """save_config_partial with no values should not modify config."""
        from repowise.cli.helpers import load_config, save_config_partial

        rw_dir = tmp_path / ".repowise"
        rw_dir.mkdir()
        (rw_dir / "config.yaml").write_text("embedder: minilm\n", encoding="utf-8")

        save_config_partial(tmp_path)  # no kwargs

        cfg = load_config(tmp_path)
        assert "exclude_patterns" not in cfg
        assert cfg["embedder"] == "minilm"

    def test_save_config_partial_creates_config_if_missing(self, tmp_path):
        """save_config_partial should create config.yaml if it doesn't exist."""
        from repowise.cli.helpers import load_config, save_config_partial

        rw_dir = tmp_path / ".repowise"
        rw_dir.mkdir()

        save_config_partial(tmp_path, exclude_patterns=[".claude/"])

        cfg = load_config(tmp_path)
        assert cfg["exclude_patterns"] == [".claude/"]

    def test_save_config_partial_persists_commit_limit(self, tmp_path):
        """save_config_partial should persist commit_limit alongside other keys."""
        from repowise.cli.helpers import load_config, save_config_partial

        rw_dir = tmp_path / ".repowise"
        rw_dir.mkdir()
        (rw_dir / "config.yaml").write_text("embedder: minilm\n", encoding="utf-8")

        save_config_partial(tmp_path, commit_limit=500)

        cfg = load_config(tmp_path)
        assert cfg["commit_limit"] == 500
        assert cfg["embedder"] == "minilm"


class TestConfigFingerprint:
    def test_config_fingerprint_detects_change(self, tmp_path):
        """config_fingerprint returns a stable hash that changes with config."""
        from repowise.cli.helpers import config_fingerprint

        rw_dir = tmp_path / ".repowise"
        rw_dir.mkdir()
        (rw_dir / "config.yaml").write_text("exclude_patterns: [.claude/]", encoding="utf-8")
        (rw_dir / "health-rules.json").write_text(
            '{"disabled_biomarkers": []}', encoding="utf-8"
        )

        fp1 = config_fingerprint(tmp_path)
        assert isinstance(fp1, str)
        assert len(fp1) == 64  # sha256 hex
        assert config_fingerprint(tmp_path) == fp1

        (rw_dir / "health-rules.json").write_text(
            '{"disabled_biomarkers": ["ungoverned_hotspot"]}', encoding="utf-8"
        )
        assert config_fingerprint(tmp_path) != fp1

    def test_config_fingerprint_missing_files(self, tmp_path):
        """config_fingerprint handles missing config files gracefully."""
        from repowise.cli.helpers import config_fingerprint

        rw_dir = tmp_path / ".repowise"
        rw_dir.mkdir()
        fp = config_fingerprint(tmp_path)
        assert isinstance(fp, str)
        assert len(fp) == 64


# ---------------------------------------------------------------------------
# Update lock
# ---------------------------------------------------------------------------


class TestUpdateLock:
    def test_acquire_writes_payload(self, tmp_path):
        from repowise.cli.helpers import read_update_lock, try_acquire_update_lock

        assert try_acquire_update_lock(tmp_path, "abc123def") is None
        payload = read_update_lock(tmp_path)
        assert payload is not None
        assert payload["target_commit"] == "abc123def"
        assert isinstance(payload["pid"], int)
        assert isinstance(payload["started_at"], (int, float))

    def test_release_removes_lock(self, tmp_path):
        from repowise.cli.helpers import (
            read_update_lock,
            release_update_lock,
            try_acquire_update_lock,
        )

        try_acquire_update_lock(tmp_path, "abc")
        release_update_lock(tmp_path)
        assert read_update_lock(tmp_path) is None

    def test_release_is_idempotent(self, tmp_path):
        from repowise.cli.helpers import release_update_lock

        # Should not raise even when no lock exists.
        release_update_lock(tmp_path)
        release_update_lock(tmp_path)

    def test_read_returns_none_when_stale(self, tmp_path):
        import json

        from repowise.cli.helpers import (
            UPDATE_LOCK_FILENAME,
            ensure_repowise_dir,
            read_update_lock,
        )

        ensure_repowise_dir(tmp_path)
        lock_path = tmp_path / ".repowise" / UPDATE_LOCK_FILENAME
        # Stale: started 2 hours ago
        lock_path.write_text(
            json.dumps({"pid": 1, "target_commit": "x", "started_at": 0}),
            encoding="utf-8",
        )
        assert read_update_lock(tmp_path) is None

    def test_read_handles_corrupt_payload(self, tmp_path):
        from repowise.cli.helpers import (
            UPDATE_LOCK_FILENAME,
            ensure_repowise_dir,
            read_update_lock,
        )

        ensure_repowise_dir(tmp_path)
        (tmp_path / ".repowise" / UPDATE_LOCK_FILENAME).write_text("not json", encoding="utf-8")
        assert read_update_lock(tmp_path) is None


# ---------------------------------------------------------------------------
# Git HEAD
# ---------------------------------------------------------------------------


class TestGetHeadCommit:
    def test_non_git_returns_none(self, tmp_path):
        assert get_head_commit(tmp_path) is None


# ---------------------------------------------------------------------------
# Provider validation
# ---------------------------------------------------------------------------


class TestValidateProviderConfig:
    def test_no_provider_returns_empty_warnings(self, monkeypatch):
        # Clear all provider env vars
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("REPOWISE_PROVIDER", raising=False)

        assert validate_provider_config() == []

    def test_anthropic_missing_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("REPOWISE_PROVIDER", "anthropic")

        warnings = validate_provider_config()
        assert len(warnings) == 1
        assert "anthropic" in warnings[0]
        assert "ANTHROPIC_API_KEY" in warnings[0]

    def test_anthropic_valid_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("REPOWISE_PROVIDER", "anthropic")

        assert validate_provider_config() == []

    def test_anthropic_empty_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setenv("REPOWISE_PROVIDER", "anthropic")

        warnings = validate_provider_config()
        assert len(warnings) == 1
        assert "ANTHROPIC_API_KEY" in warnings[0]

    def test_openai_missing_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("REPOWISE_PROVIDER", "openai")

        warnings = validate_provider_config()
        assert len(warnings) == 1
        assert "openai" in warnings[0]
        assert "OPENAI_API_KEY" in warnings[0]

    def test_gemini_with_gemini_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("REPOWISE_PROVIDER", "gemini")

        assert validate_provider_config() == []

    def test_gemini_with_google_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        monkeypatch.setenv("REPOWISE_PROVIDER", "gemini")

        assert validate_provider_config() == []

    def test_gemini_missing_keys(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("REPOWISE_PROVIDER", "gemini")

        warnings = validate_provider_config()
        assert len(warnings) == 1
        assert "gemini" in warnings[0]

    def test_ollama_missing_url(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        monkeypatch.setenv("REPOWISE_PROVIDER", "ollama")

        warnings = validate_provider_config()
        assert len(warnings) == 1
        assert "ollama" in warnings[0]
        assert "OLLAMA_BASE_URL" in warnings[0]

    def test_unknown_provider(self, monkeypatch):
        warnings = validate_provider_config("unknown")
        assert len(warnings) == 1
        assert "unknown provider" in warnings[0].lower()

    def test_auto_detect_anthropic(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

        # Should not warn when env var is properly set
        assert validate_provider_config() == []

    def test_anthropic_empty_key_auto_detect(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

        # Should warn when env var exists but is empty
        warnings = validate_provider_config()
        assert len(warnings) == 1
        assert "anthropic" in warnings[0]
        assert "ANTHROPIC_API_KEY" in warnings[0]


# ---------------------------------------------------------------------------
# Provider base_url resolution
# ---------------------------------------------------------------------------


class TestResolveProviderBaseUrl:
    @staticmethod
    def test_env_base_url_forwarded(monkeypatch, tmp_path):
        captured: dict[str, Any] = {}

        def fake_get_provider(name: str, **kwargs: Any):
            captured["name"] = name
            captured["kwargs"] = kwargs
            return "provider"

        monkeypatch.setattr("repowise.core.providers.get_provider", fake_get_provider)
        monkeypatch.setattr(
            "repowise.cli.helpers.validate_provider_config", lambda *_args, **_kw: []
        )
        monkeypatch.setenv("REPOWISE_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://proxy.local")

        result = resolve_provider(None, None, repo_path=tmp_path)

        assert result == "provider"
        assert captured["name"] == "openai"
        assert captured["kwargs"].get("base_url") == "http://proxy.local"

    @staticmethod
    def test_config_base_url_used_when_env_missing(monkeypatch, tmp_path):
        captured: dict[str, Any] = {}

        def fake_get_provider(name: str, **kwargs: Any):
            captured["name"] = name
            captured["kwargs"] = kwargs
            return "provider"

        monkeypatch.setattr("repowise.core.providers.get_provider", fake_get_provider)
        monkeypatch.setattr(
            "repowise.cli.helpers.validate_provider_config", lambda *_args, **_kw: []
        )
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        cfg = {
            "provider": "ollama",
            "model": "llama3",
            "ollama": {"base_url": "http://ollama.local:11434"},
        }
        repowise_dir = ensure_repowise_dir(tmp_path)
        config_path = repowise_dir / CONFIG_FILENAME

        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            yaml = None

        if "yaml" in locals() and yaml is not None:
            config_path.write_text(
                yaml.dump(cfg, default_flow_style=False, sort_keys=False), encoding="utf-8"
            )
        else:
            config_path.write_text(
                "provider: ollama\nmodel: llama3\nollama:\n  base_url: http://ollama.local:11434\n",
                encoding="utf-8",
            )

        result = resolve_provider(None, None, repo_path=tmp_path)

        assert result == "provider"
        assert captured["name"] == "ollama"
        assert captured["kwargs"].get("base_url") == "http://ollama.local:11434"


# ---------------------------------------------------------------------------
# Provider model resolution from config.yaml (issue #416)
# ---------------------------------------------------------------------------


class TestResolveProviderConfigModel:
    """The config.yaml ``model`` must be honored whenever no model is passed
    explicitly, regardless of how the *provider* was resolved. Otherwise the
    provider constructor falls back to its hardcoded default (issue #416)."""

    @staticmethod
    def _capture(monkeypatch, tmp_path, cfg: dict[str, Any]) -> dict[str, Any]:
        import yaml  # type: ignore[import-untyped]

        (ensure_repowise_dir(tmp_path) / CONFIG_FILENAME).write_text(
            yaml.dump(cfg, default_flow_style=False, sort_keys=False), encoding="utf-8"
        )
        captured: dict[str, Any] = {}

        def fake_get_provider(name: str, **kwargs: Any):
            captured["name"] = name
            captured["kwargs"] = kwargs
            return "provider"

        monkeypatch.setattr("repowise.core.providers.get_provider", fake_get_provider)
        monkeypatch.setattr(
            "repowise.cli.helpers.validate_provider_config", lambda *_args, **_kw: []
        )
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        return captured

    def test_config_model_used_when_provider_from_env(self, monkeypatch, tmp_path):
        captured = self._capture(monkeypatch, tmp_path, {"model": "google/gemini-3.1"})
        monkeypatch.setenv("REPOWISE_PROVIDER", "openrouter")

        assert resolve_provider(None, None, repo_path=tmp_path) == "provider"
        assert captured["name"] == "openrouter"
        assert captured["kwargs"].get("model") == "google/gemini-3.1"

    def test_config_model_used_when_provider_from_flag(self, monkeypatch, tmp_path):
        captured = self._capture(monkeypatch, tmp_path, {"model": "google/gemini-3.1"})
        monkeypatch.delenv("REPOWISE_PROVIDER", raising=False)

        assert resolve_provider("openrouter", None, repo_path=tmp_path) == "provider"
        assert captured["kwargs"].get("model") == "google/gemini-3.1"

    def test_config_model_used_on_api_key_auto_detect(self, monkeypatch, tmp_path):
        captured = self._capture(monkeypatch, tmp_path, {"model": "google/gemini-3.1"})
        monkeypatch.delenv("REPOWISE_PROVIDER", raising=False)
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)

        assert resolve_provider(None, None, repo_path=tmp_path) == "provider"
        assert captured["name"] == "openrouter"
        assert captured["kwargs"].get("model") == "google/gemini-3.1"

    def test_explicit_model_overrides_config(self, monkeypatch, tmp_path):
        captured = self._capture(monkeypatch, tmp_path, {"model": "google/gemini-3.1"})
        monkeypatch.delenv("REPOWISE_PROVIDER", raising=False)

        assert resolve_provider("openrouter", "anthropic/claude-opus-4", repo_path=tmp_path)
        assert captured["kwargs"].get("model") == "anthropic/claude-opus-4"


# ---------------------------------------------------------------------------
# Update queued / pending markers — coalescing primitives that prevent the
# post-commit hook from spawning N concurrent updates on rapid-fire commits.
# ---------------------------------------------------------------------------


class TestUpdateQueuedMarker:
    """The hook drops .update.queued *before* backgrounding update_cmd so the
    augment hook can suppress its stale-wiki warning during the start-up
    window where the real lock hasn't been acquired yet."""

    def test_round_trip(self, tmp_path):
        from repowise.cli.helpers import (
            clear_update_queued,
            read_update_queued,
            write_update_queued,
        )

        write_update_queued(tmp_path, "abc123")
        payload = read_update_queued(tmp_path)
        assert payload is not None
        assert payload["target_commit"] == "abc123"
        assert isinstance(payload["queued_at"], float)

        clear_update_queued(tmp_path)
        assert read_update_queued(tmp_path) is None

    def test_stale_queued_returns_none(self, tmp_path):
        # A queued marker older than UPDATE_QUEUED_STALE_AFTER_SECONDS
        # (5 min) is treated as crashed-hook noise and ignored.
        import json

        from repowise.cli.helpers import ensure_repowise_dir, read_update_queued

        ensure_repowise_dir(tmp_path)
        (tmp_path / ".repowise" / ".update.queued").write_text(
            json.dumps({"target_commit": "abc", "queued_at": 0}),
            encoding="utf-8",
        )
        assert read_update_queued(tmp_path) is None

    def test_missing_marker_returns_none(self, tmp_path):
        from repowise.cli.helpers import read_update_queued

        assert read_update_queued(tmp_path) is None


class TestUpdatePendingMarker:
    """A new update_cmd run that finds an existing lock writes the latest
    HEAD into .update.pending so the running update can roll forward to
    it instead of stopping at a stale commit."""

    def test_round_trip(self, tmp_path):
        from repowise.cli.helpers import (
            clear_update_pending,
            read_update_pending,
            write_update_pending,
        )

        write_update_pending(tmp_path, "deadbeef")
        assert read_update_pending(tmp_path) == "deadbeef"

        clear_update_pending(tmp_path)
        assert read_update_pending(tmp_path) is None

    def test_write_with_none_head_is_noop(self, tmp_path):
        from repowise.cli.helpers import read_update_pending, write_update_pending

        write_update_pending(tmp_path, None)
        assert read_update_pending(tmp_path) is None


class TestRotateUpdateLog:
    """The post-commit hook appends every run's output to .update.log.
    Without rotation it would grow unboundedly on a busy repo."""

    def test_no_op_when_under_cap(self, tmp_path):
        from repowise.cli.helpers import (
            ensure_repowise_dir,
            rotate_update_log_if_needed,
            update_log_path,
        )

        ensure_repowise_dir(tmp_path)
        path = update_log_path(tmp_path)
        path.write_text("small log", encoding="utf-8")

        rotate_update_log_if_needed(tmp_path)
        assert path.read_text(encoding="utf-8") == "small log"

    def test_truncates_when_over_cap(self, tmp_path):
        from repowise.cli.helpers import (
            UPDATE_LOG_KEEP_TAIL_BYTES,
            UPDATE_LOG_MAX_BYTES,
            ensure_repowise_dir,
            rotate_update_log_if_needed,
            update_log_path,
        )

        ensure_repowise_dir(tmp_path)
        path = update_log_path(tmp_path)
        # Build a payload comfortably over the cap with a known tail so we
        # can assert what survives.
        body_size = UPDATE_LOG_MAX_BYTES * 2
        path.write_bytes(b"x" * (body_size - 20) + b"TAIL_MARKER_ZZZZ\n")

        rotate_update_log_if_needed(tmp_path)

        after = path.read_bytes()
        # Capped to roughly KEEP_TAIL_BYTES + the truncation banner; far
        # below the original size in any case.
        assert len(after) < UPDATE_LOG_MAX_BYTES
        assert b"TAIL_MARKER_ZZZZ" in after
        assert after.startswith(b"... (log truncated) ...")
