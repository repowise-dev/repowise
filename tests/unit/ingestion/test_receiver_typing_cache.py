"""The per-file scan caches of receiver typing keep their recent files."""

from __future__ import annotations

from repowise.core.ingestion.call_receiver_typing._shared import _store_capped


def test_a_full_cache_evicts_its_oldest_file_and_keeps_the_rest() -> None:
    cache: dict[str, int] = {}
    for n, path in enumerate(("a.py", "b.py", "c.py")):
        _store_capped(cache, path, n, 3)

    _store_capped(cache, "d.py", 3, 3)

    # Emptying the cache on every overflow made interleaved lookups rescan each file.
    assert cache == {"b.py": 1, "c.py": 2, "d.py": 3}


def test_storing_a_cached_file_again_evicts_nothing() -> None:
    cache = {"a.py": 0, "b.py": 1}

    _store_capped(cache, "a.py", 5, 2)

    assert cache == {"a.py": 5, "b.py": 1}
