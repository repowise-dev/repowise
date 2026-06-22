"""Tests for the TTL-cached PyPI update check."""

from __future__ import annotations

import json

import repowise.cli.helpers as helpers
import repowise.cli.update_check as uc
from repowise.cli.update_check import UpdateCheck, get_cli_update_check_cached


def _fake_check(latest: str | None = "9.9.9", error: str | None = None) -> UpdateCheck:
    return UpdateCheck(
        current_version="0.21.0",
        latest_version=latest,
        resolved_executable="/x/repowise",
        running_executable="/x/repowise",
        python="/x/python",
        update_available=(latest is not None),
        suggested_command="pipx upgrade repowise",
        install_hint="pipx",
        error=error,
    )


def _redirect_global_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(helpers, "user_global_dir", lambda: tmp_path)


def test_miss_then_hit_avoids_second_network_call(monkeypatch, tmp_path):
    _redirect_global_dir(monkeypatch, tmp_path)
    calls = {"n": 0}

    def _live(timeout=2.0):
        calls["n"] += 1
        return _fake_check()

    monkeypatch.setattr(uc, "get_cli_update_check", _live)

    first = get_cli_update_check_cached(ttl_hours=24)
    second = get_cli_update_check_cached(ttl_hours=24)

    assert calls["n"] == 1  # second served from cache
    assert first.latest_version == "9.9.9"
    assert second.latest_version == "9.9.9"
    assert (tmp_path / "update-check.json").exists()


def test_expired_cache_triggers_refetch(monkeypatch, tmp_path):
    _redirect_global_dir(monkeypatch, tmp_path)
    # Pre-seed an old cache entry.
    (tmp_path / "update-check.json").write_text(
        json.dumps({"checked_at": 0, "latest_version": "1.0.0", "error": None}),
        encoding="utf-8",
    )
    calls = {"n": 0}

    def _live(timeout=2.0):
        calls["n"] += 1
        return _fake_check(latest="9.9.9")

    monkeypatch.setattr(uc, "get_cli_update_check", _live)
    result = get_cli_update_check_cached(ttl_hours=24)
    assert calls["n"] == 1
    assert result.latest_version == "9.9.9"


def test_corrupt_cache_falls_back_to_live(monkeypatch, tmp_path):
    _redirect_global_dir(monkeypatch, tmp_path)
    (tmp_path / "update-check.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(uc, "get_cli_update_check", lambda timeout=2.0: _fake_check())
    result = get_cli_update_check_cached()
    assert result.latest_version == "9.9.9"


def test_cached_update_available_recomputed_from_local_current(monkeypatch, tmp_path):
    """A cache hit recomputes update_available against the live current version."""
    _redirect_global_dir(monkeypatch, tmp_path)
    (tmp_path / "update-check.json").write_text(
        json.dumps({"checked_at": _now(), "latest_version": "0.21.0", "error": None}),
        encoding="utf-8",
    )
    monkeypatch.setattr("repowise.cli.__version__", "0.21.0", raising=False)
    result = get_cli_update_check_cached()
    # latest == current -> no update available, even though it was a cache hit.
    assert result.update_available is False


def _now() -> float:
    import time

    return time.time()
