"""A failed detached drain leaves a traceback under .repowise."""

from __future__ import annotations

from repowise.core.pipeline import commit_health_drain as drain_mod


def test_failed_drain_logs_traceback(tmp_path, monkeypatch):
    (tmp_path / ".repowise").mkdir()

    async def boom(*_a, **_k):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(drain_mod, "wait_for_turn", lambda _p: True)
    monkeypatch.setattr(drain_mod, "_drain_store", boom)
    monkeypatch.setattr("repowise.core.update_lock.release_commit_health_lock", lambda _p: None)

    assert drain_mod.main([str(tmp_path), "r1"]) == 1
    log = (tmp_path / ".repowise" / "commit_health_drain.log").read_text(encoding="utf-8")
    assert "scan exploded" in log
