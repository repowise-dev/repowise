"""IndexStampCache bounded by the weight of its answers, not only their count."""

from __future__ import annotations

import pytest

from repowise.core.persistence.index_cache import IndexStampCache


def _key(repo: str, part: str = "") -> tuple[str, ...]:
    return ("db", repo, "stamp", part)


def test_least_recently_used_answers_go_until_the_total_fits_the_budget() -> None:
    cache = IndexStampCache(10, weigh=len, budget=10)
    cache.remember(_key("a"), "x" * 4)
    cache.remember(_key("b"), "x" * 4)
    cache.get(_key("a"))  # b is now the least recently used

    cache.remember(_key("c"), "x" * 4)

    assert _key("b") not in cache
    assert _key("a") in cache and _key("c") in cache
    assert cache.weight == 8


def test_an_answer_over_the_budget_on_its_own_is_still_kept() -> None:
    cache = IndexStampCache(10, weigh=len, budget=10)
    cache.remember(_key("a"), "x" * 3)

    cache.remember(_key("big"), "x" * 50)

    assert list(cache._entries) == [_key("big")]
    assert cache.weight == 50


def test_dropping_and_replacing_answers_keeps_the_total_exact() -> None:
    cache = IndexStampCache(10, weigh=len, budget=100)
    cache.remember(_key("a", "one"), "x" * 5)
    cache.remember(_key("a", "two"), "x" * 6)
    cache.remember(_key("b"), "x" * 7)
    cache.remember(_key("b"), "x" * 2)  # replaced, not added twice
    assert cache.weight == 13

    cache.drop_repo_except("a", _key("a", "one"))
    assert cache.weight == 7
    cache.forget(_key("b"))
    assert cache.weight == 5
    cache.drop_repo("a")
    assert cache.weight == 0 and len(cache) == 0


def test_without_a_weigher_only_the_entry_count_bounds_the_cache() -> None:
    cache = IndexStampCache(2)
    for repo in ("a", "b", "c"):
        cache.remember(_key(repo), "x" * 1000)

    assert [k[1] for k in cache._entries] == ["b", "c"]
    assert cache.weight == 0


def test_a_weigher_that_raises_leaves_the_cache_as_it_was() -> None:
    def weigh(value: str) -> int:
        if value == "bad":
            raise ValueError(value)
        return len(value)

    cache = IndexStampCache(10, weigh=weigh, budget=10)
    cache.remember(_key("a"), "xxx")

    with pytest.raises(ValueError):
        cache.remember(_key("b"), "bad")

    assert list(cache._entries) == [_key("a")]
    assert cache.weight == 3
