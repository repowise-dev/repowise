"""Resolving a declaration from a source position across checkouts."""

from __future__ import annotations

from pathlib import Path

from repowise.server.mcp_server import _checkout


def test_source_root_none_without_borrowed_checkout() -> None:
    assert _checkout.source_root(None) is None
    assert _checkout.source_root("/some/path") is None


def test_source_root_returns_served_checkout(monkeypatch) -> None:
    caller = _checkout.CallerCheckout(Path("/worktree"), Path("/main"))
    monkeypatch.setattr(_checkout, "current", lambda: caller)
    monkeypatch.setattr(_checkout, "same_path", lambda a, b: str(a) == str(b))

    assert _checkout.source_root("/main") == Path("/main")
    assert _checkout.source_root("/worktree") is None
