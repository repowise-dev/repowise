"""Unit tests for the shared I/O-boundary classifier.

The classifier is a shared primitive (C4 today; perf / security later), so the
contract is narrow and explicit: a curated name maps to its boundary kind, an
unknown name maps to ``None`` (never a guess), and the value set is frozen and
mirrored in TypeScript.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.external_systems.io_kind import (
    IO_KINDS,
    classify_io_kind,
)


def test_io_kinds_are_the_frozen_canonical_set() -> None:
    # Cross-language parity guard: the TS mirror (C4_IO_KINDS) and
    # packages/types/__tests__/contracts.test.ts assert the same membership.
    assert IO_KINDS == ("db", "network", "filesystem", "subprocess", "lock")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # Python network
        ("httpx", "network"),
        ("requests", "network"),
        ("aiohttp", "network"),
        ("socket", "network"),
        # Python db
        ("sqlalchemy", "db"),
        ("psycopg", "db"),
        ("asyncpg", "db"),
        ("redis", "db"),
        ("pymongo", "db"),
        # Python subprocess / filesystem / lock (stdlib-ish)
        ("subprocess", "subprocess"),
        ("open", "filesystem"),
        ("filelock", "lock"),
        # TS / Node network
        ("axios", "network"),
        ("node-fetch", "network"),
        # TS / Node db
        ("@prisma/client", "db"),
        ("drizzle-orm", "db"),
        ("knex", "db"),
        ("pg", "db"),
        ("mongoose", "db"),
        # TS / Node filesystem / subprocess
        ("node:fs", "filesystem"),
        ("child_process", "subprocess"),
    ],
)
def test_known_libs_map_to_expected_io_kind(name: str, expected: str) -> None:
    assert classify_io_kind(name) == expected
    # Every produced value must be a member of the canonical set.
    assert classify_io_kind(name) in IO_KINDS


@pytest.mark.parametrize(
    "name",
    ["", "left-pad", "lodash", "numpy", "pytest", "react", "some-unknown-pkg"],
)
def test_unknown_or_non_io_libs_return_none(name: str) -> None:
    assert classify_io_kind(name) is None


def test_classification_is_case_and_whitespace_insensitive() -> None:
    assert classify_io_kind("HTTPX") == "network"
    assert classify_io_kind("  SQLAlchemy  ") == "db"
    assert classify_io_kind("@Prisma/Client") == "db"
