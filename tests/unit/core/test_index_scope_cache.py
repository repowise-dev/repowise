"""The scope every MCP response embeds is parsed once per index state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core import index_scope
from repowise.core.index_scope import load_index_scope


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    index_scope._SCOPE_CACHE.clear()


def _write(repo: Path, *, run_mode: str = "standard") -> None:
    repowise = repo / ".repowise"
    repowise.mkdir(parents=True, exist_ok=True)
    (repowise / "state.json").write_text(
        json.dumps({"index_scope": {"version": 1, "run_mode": run_mode}}),
        encoding="utf-8",
    )
    (repowise / "config.yaml").write_text("provider: openai\n", encoding="utf-8")


def test_repeated_reads_do_not_reparse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path)
    assert load_index_scope(tmp_path) is not None

    calls: list[int] = []
    real = index_scope._read_index_scope

    def counted(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(index_scope, "_read_index_scope", counted)
    for _ in range(5):
        load_index_scope(tmp_path)

    assert calls == []


def test_a_rebuild_invalidates_the_cache(tmp_path: Path) -> None:
    """The easy case: "standard" and "fast" differ in length, so size catches it."""
    _write(tmp_path, run_mode="standard")
    assert load_index_scope(tmp_path)["run_mode"] == "standard"

    _write(tmp_path, run_mode="fast")

    assert load_index_scope(tmp_path)["run_mode"] == "fast"


def test_a_same_size_rewrite_still_invalidates(tmp_path: Path) -> None:
    """The harder case: identical length, so only mtime can catch it."""
    _write(tmp_path, run_mode="fast")
    assert load_index_scope(tmp_path)["run_mode"] == "fast"

    state = tmp_path / ".repowise" / "state.json"
    size_before = state.stat().st_size
    # "fast" and "slow" are the same length, so st_size cannot tell them apart.
    state.write_text(
        json.dumps({"index_scope": {"version": 1, "run_mode": "slow"}}), encoding="utf-8"
    )
    assert state.stat().st_size == size_before

    # run_mode only stores a known choice, so "slow" resolves to "unknown". What
    # matters here is that the cached "fast" did not survive the rewrite.
    assert load_index_scope(tmp_path)["run_mode"] != "fast"


def test_callers_cannot_corrupt_the_cache(tmp_path: Path) -> None:
    """Responses embed this dict and later passes mutate it in place."""
    _write(tmp_path)

    first = load_index_scope(tmp_path)
    first["run_mode"] = "MUTATED"
    first.setdefault("nested", {})["added"] = True

    second = load_index_scope(tmp_path)
    assert second["run_mode"] == "standard"
    assert "nested" not in second


def test_missing_state_is_not_cached_as_a_readable_index(tmp_path: Path) -> None:
    assert load_index_scope(tmp_path) is None

    _write(tmp_path)
    assert load_index_scope(tmp_path) is not None


def test_cache_does_not_grow_without_bound(tmp_path: Path) -> None:
    for i in range(index_scope._SCOPE_CACHE_MAX + 4):
        repo = tmp_path / f"repo{i}"
        _write(repo)
        load_index_scope(repo)

    assert len(index_scope._SCOPE_CACHE) <= index_scope._SCOPE_CACHE_MAX
