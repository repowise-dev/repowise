"""How the prior-fix block renders each state it can reach.

The block was silent for five different reasons, so an index read that failed
was indistinguishable on the wire from a change whose files have never been
patched. These tests pin which of those stay silent and which one must not.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from repowise.core.analysis.prior_fix_impact import (
    FixRecord,
    summarize_prior_fixes,
    unavailable_prior_fixes,
    unsupported_prior_fixes,
)
from repowise.server.mcp_server import tool_change_risk as tool


async def _block(monkeypatch, impact, changed=None):
    async def _fake(ctx, changed_lines):
        return impact

    monkeypatch.setattr(tool, "_read_prior_fixes", _fake)
    return await tool._prior_fixes_block(object(), changed or {"a.py": {1}})


# ---------------------------------------------------------------------------
# What stays silent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_index_without_fix_events_stays_silent(monkeypatch):
    """A repo without the feature must not grow a noise block."""
    block = await _block(monkeypatch, unsupported_prior_fixes("this index predates it"))

    assert block is None


@pytest.mark.asyncio
async def test_files_with_no_past_stay_silent(monkeypatch):
    block = await _block(monkeypatch, summarize_prior_fixes([], {"a.py": {1}}))

    assert block is None


# ---------------------------------------------------------------------------
# What must not stay silent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_read_renders_rather_than_vanishing(monkeypatch):
    block = await _block(monkeypatch, unavailable_prior_fixes("disk I/O error"))

    assert block is not None
    assert block["status"] == "unavailable"
    assert "disk I/O error" in block["reason"]
    # It must not carry the shape of an answer it does not have.
    assert "files" not in block
    assert "total_fixes" not in block


@pytest.mark.asyncio
async def test_a_failed_read_does_not_claim_the_change_is_clear(monkeypatch):
    block = await _block(monkeypatch, unavailable_prior_fixes("connection reset"))

    assert "not cleared" in block["summary"]


# ---------------------------------------------------------------------------
# The populated block keeps its historical shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_populated_block_keeps_its_keys(monkeypatch):
    impact = summarize_prior_fixes(
        [FixRecord(fix_sha="aaa", file_path="a.py", old_ranges=((1, 4),))],
        {"a.py": {1, 2}},
    )

    block = await _block(monkeypatch, impact, {"a.py": {1, 2}})

    assert set(block) >= {
        "files",
        "truncated",
        "total_fixes",
        "files_with_fixes",
        "changed_lines_in_fixed_files",
        "line_overlap",
        "summary",
    }
    assert block["line_overlap"] == "approximate"
    assert block["files"][0]["file_path"] == "a.py"
    assert block["files"][0]["overlapping_lines"] == 2


@pytest.mark.asyncio
async def test_a_dateless_fix_omits_the_age_key_rather_than_nulling_it(monkeypatch):
    impact = summarize_prior_fixes([FixRecord(fix_sha="aaa", file_path="a.py")], {"a.py": {1}})

    block = await _block(monkeypatch, impact)

    assert "last_fix_days_ago" not in block["files"][0]


@pytest.mark.asyncio
async def test_the_list_is_capped_and_says_so(monkeypatch):
    paths = [f"f{i}.py" for i in range(tool._PRIOR_FIXES_LIMIT + 3)]
    changed = {p: {1} for p in paths}
    impact = summarize_prior_fixes(
        [FixRecord(fix_sha=f"sha{i}", file_path=p) for i, p in enumerate(paths)], changed
    )

    block = await _block(monkeypatch, impact, changed)

    assert len(block["files"]) == tool._PRIOR_FIXES_LIMIT
    assert block["truncated"] is True
    # The counts describe the whole population, not the capped list.
    assert block["files_with_fixes"] == len(paths)


@pytest.mark.asyncio
async def test_concentration_only_names_a_file_the_block_shows(monkeypatch):
    changed = {"big.py": set(range(1, 20)), "small.py": {1}}
    impact = summarize_prior_fixes(
        [
            FixRecord(fix_sha="aaa", file_path="big.py"),
            FixRecord(fix_sha="bbb", file_path="small.py"),
        ],
        changed,
    )

    block = await _block(monkeypatch, impact, changed)

    assert "big.py" in block["concentration"]


# ---------------------------------------------------------------------------
# Telling a missing table from a real failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,missing",
    [
        ("no such table: fix_events", True),
        ('relation "fix_events" does not exist', True),
        ("Undefined table: 7 ERROR", True),
        ("database is locked", False),
        ("disk I/O error", False),
        ("connection reset by peer", False),
        # Postgres says "does not exist" for plenty that is not a missing
        # table, and every one of these is a real failure that must surface.
        ('column "fix_sha" does not exist', False),
        ('database "wiki" does not exist', False),
        ('role "repowise" does not exist', False),
        ("function now_utc() does not exist", False),
    ],
)
def test_a_missing_table_is_told_apart_from_a_broken_read(message, missing):
    exc = OperationalError("SELECT 1", {}, Exception(message))

    assert tool._is_missing_table(exc) is missing


def test_the_failure_reason_names_the_class_without_quoting_the_driver():
    """Driver text can carry connection-string fragments, and this goes on the wire."""
    exc = OperationalError(
        "SELECT 1", {}, Exception("could not connect: host=db.internal user=admin password=hunter2")
    )

    reason = tool._read_failure(exc)

    assert "OperationalError" in reason
    assert "hunter2" not in reason
    assert "db.internal" not in reason
