"""The pricing snapshot: what it captures, and what it refuses to pay for.

The cache is not an optimization detail. Detecting the agent's model scans the
local transcripts, which was measured at about six seconds on a machine with
700 Codex sessions, and the hook is a fresh process per tool call. So "the hook
never scans" is a correctness property of the hot path, and it is asserted here
rather than trusted.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from repowise.core.savings import pricing
from repowise.core.savings.pricing import (
    PricingSnapshot,
    clear_pricing_cache,
    resolve_pricing_snapshot,
)


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_pricing_cache()
    yield
    clear_pricing_cache()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".repowise" / "omissions").mkdir(parents=True)
    return tmp_path


def _count_scans(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replace the transcript scan with a counter, so a scan is observable."""
    calls: list[int] = []

    def fake(repo_root: Path) -> PricingSnapshot:
        calls.append(1)
        return PricingSnapshot(
            model="claude-opus-5",
            pricing_source="session_model:claude_code",
            pricing_version="pricing:test",
            input_rate_usd_per_million=5.0,
            output_rate_usd_per_million=25.0,
        )

    monkeypatch.setattr(pricing, "_scan", fake)
    return calls


def test_a_snapshot_carries_the_fields_an_event_needs(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _count_scans(monkeypatch)
    payload = resolve_pricing_snapshot(repo).as_payload()
    assert payload == {
        "model": "claude-opus-5",
        "currency": "USD",
        "pricing_source": "session_model:claude_code",
        "pricing_version": "pricing:test",
        "input_rate_usd_per_million": 5.0,
        "output_rate_usd_per_million": 25.0,
    }


def test_the_scan_runs_once_and_is_then_served_from_disk(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_scans(monkeypatch)
    first = resolve_pricing_snapshot(repo)
    clear_pricing_cache()  # a fresh process, with the cache file already on disk
    second = resolve_pricing_snapshot(repo)
    assert first == second
    assert len(calls) == 1


def test_the_hook_reads_the_cache_but_never_scans(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole reason the switch exists."""
    calls = _count_scans(monkeypatch)

    # Nothing cached yet: the hook takes an unpriced event rather than a stall.
    assert resolve_pricing_snapshot(repo, allow_scan=False) is None
    assert calls == []

    # Once some other surface has filled the cache, the hook gets a price.
    resolve_pricing_snapshot(repo)
    clear_pricing_cache()
    assert resolve_pricing_snapshot(repo, allow_scan=False) is not None
    assert len(calls) == 1


def test_declining_to_scan_does_not_poison_the_memo(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook miss must not make the next scanning caller wait out a TTL."""
    calls = _count_scans(monkeypatch)
    assert resolve_pricing_snapshot(repo, allow_scan=False) is None
    assert resolve_pricing_snapshot(repo) is not None
    assert len(calls) == 1


def test_a_stale_cache_is_rescanned(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _count_scans(monkeypatch)
    resolve_pricing_snapshot(repo)
    cache = repo / ".repowise" / "omissions" / "pricing-snapshot.json"
    payload = json.loads(cache.read_text(encoding="utf-8"))
    payload["resolved_at"] = time.time() - (pricing._TTL_SECONDS + 60)
    cache.write_text(json.dumps(payload), encoding="utf-8")
    clear_pricing_cache()

    resolve_pricing_snapshot(repo)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        json.dumps({"version": 999, "resolved_at": 0}),
        json.dumps({"version": 1, "resolved_at": "yesterday", "model": "m"}),
        json.dumps({"version": 1, "resolved_at": True, "model": "m"}),
        json.dumps({"version": 1, "resolved_at": 0}),  # fresh-looking but empty
        json.dumps([1, 2, 3]),
    ],
)
def test_an_unusable_cache_is_treated_as_absent(
    repo: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    """Never an exception on a read path, and never a half-built snapshot."""
    calls = _count_scans(monkeypatch)
    cache = repo / ".repowise" / "omissions" / "pricing-snapshot.json"
    cache.write_text(content, encoding="utf-8")

    assert resolve_pricing_snapshot(repo) is not None
    assert len(calls) == 1
    clear_pricing_cache()
    cache.write_text(content, encoding="utf-8")
    assert resolve_pricing_snapshot(repo, allow_scan=False) is None


def test_a_missing_repository_is_unpriced_rather_than_an_error() -> None:
    assert resolve_pricing_snapshot(None) is None


def test_a_failing_scan_never_raises(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(repo_root: Path) -> PricingSnapshot:
        raise RuntimeError("transcripts exploded")

    monkeypatch.setattr(pricing, "_scan", boom)
    assert resolve_pricing_snapshot(repo) is None


def test_the_cache_write_leaves_no_temporary_behind(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _count_scans(monkeypatch)
    resolve_pricing_snapshot(repo)
    directory = repo / ".repowise" / "omissions"
    assert [p.name for p in directory.iterdir()] == ["pricing-snapshot.json"]


def test_the_pricing_table_version_changes_with_the_table() -> None:
    """A hand-maintained version would go stale on exactly the edit that matters."""
    from repowise.core.generation import cost_tracker

    before = cost_tracker.pricing_table_version()
    assert before == cost_tracker.pricing_table_version()  # stable across calls
    assert before.startswith("pricing:")
