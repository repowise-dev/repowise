"""Commit id shapes: full ids are 40 or 64 lowercase hex, prefixes 7 to 64 by default."""

from __future__ import annotations

import re

import pytest

from repowise.core.git_sha import (
    FULL_SHA_PATTERN,
    SHA_PREFIX_PATTERN,
    is_full_sha,
    is_sha_prefix,
)


@pytest.mark.parametrize(
    ("value", "full"),
    [
        ("a" * 40, True),
        ("0123456789abcdef" * 4, True),  # 64: a SHA-256 repository
        ("a" * 39, False),
        ("a" * 41, False),
        ("a" * 63, False),
        ("a" * 65, False),
        ("A" * 40, False),  # callers lowercase first
        ("g" * 40, False),
        (" " + "a" * 40, False),
        ("a" * 40 + "\n", False),
        ("", False),
        (None, False),
        (40, False),
    ],
)
def test_is_full_sha(value: object, full: bool) -> None:
    assert is_full_sha(value) is full


@pytest.mark.parametrize(
    ("value", "prefix"),
    [
        ("a" * 7, True),
        ("a" * 40, True),
        ("a" * 64, True),
        ("a" * 6, False),
        ("a" * 65, False),
        ("abcDEF1", False),
        ("abcdeg1", False),
        ("abcdef1 ", False),
        ("-abcdef1", False),
        (None, False),
    ],
)
def test_is_sha_prefix(value: object, prefix: bool) -> None:
    assert is_sha_prefix(value) is prefix


def test_a_shorter_floor_is_a_parameter() -> None:
    assert is_sha_prefix("abcd", min_len=4)
    assert not is_sha_prefix("abc", min_len=4)
    assert not is_sha_prefix("abcd")


def test_the_patterns_agree_with_the_predicates() -> None:
    for value in ("a" * 40, "a" * 64, "a" * 50, "a" * 7, "a" * 6, "a" * 65):
        assert bool(re.fullmatch(FULL_SHA_PATTERN, value)) is is_full_sha(value)
        assert bool(re.fullmatch(SHA_PREFIX_PATTERN, value)) is is_sha_prefix(value)


def test_sites_reading_typed_ids_lowercase_before_the_check() -> None:
    from repowise.cli._helpers.git import as_commit_id
    from repowise.core.forges.base import looks_like_sha

    assert looks_like_sha("ABCDEF1") and looks_like_sha("a" * 64)
    assert not looks_like_sha("main")
    assert as_commit_id("ABCDEF1") == "abcdef1"
    assert as_commit_id("-abcdef1") is None
