"""get_context's ``measured`` line: present with runtime evidence, absent without it."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from repowise.core.persistence.models import SymbolHotness
from repowise.server.mcp_server.tool_context import measured
from repowise.server.mcp_server.tool_context.measured import file_line, symbol_line

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
FILE = "src/auth/service.py"
LOGIN = f"{FILE}::login"


def _row(symbol_id: str = LOGIN, **kw):
    base = {
        "symbol_id": symbol_id,
        "run_id": "r1",
        "tier": "ranked",
        "rank": 3,
        "samples": 120,
        "run_samples": 1000,
        "share_low": 0.10,
        "share_high": 0.14,
        "total_share": 0.5,
        "self_share": 0.12,
        "repo_self_share": 0.4,
        "workload_name": "cli build",
        "workload_class": "entry_point",
        "ordering_only": False,
        "freshness": "current",
        "measured_at": NOW - timedelta(days=2),
    }
    base.update(kw)
    return SimpleNamespace(**base)


# -- wording -----------------------------------------------------------------


def test_symbol_line_names_basis_self_share_interval_repo_self_and_age():
    # The cost (self) leads; inclusive time is never stated as the function's.
    line = symbol_line(_row(), NOW)
    assert line == (
        'rank 3 of repo code in entry_point "cli build" '
        "(12% self, 10-14%; repo code itself 40%), 2d old"
    )
    assert "50%" not in line and "explains" not in line


def test_a_run_stored_without_its_split_leaves_it_out():
    assert symbol_line(_row(repo_self_share=None), NOW) == (
        'rank 3 of repo code in entry_point "cli build" (12% self, 10-14%), 2d old'
    )


def test_no_hot_or_cold_wording_and_lead_tier_reads_as_ranked():
    line = symbol_line(_row(tier="lead_candidate"), NOW)
    assert line.startswith("rank 3 of repo code")
    assert "hot" not in line.lower() and "cold" not in line.lower()


def test_unit_test_run_says_in_tests():
    line = symbol_line(_row(workload_class="unit_test", workload_name="pytest"), NOW)
    assert 'in tests "pytest"' in line
    assert "unit_test" not in line


def test_tracer_run_is_ordering_only():
    row = _row(ordering_only=True, share_low=None, share_high=None, total_share=None,
               self_share=None)
    assert symbol_line(row, NOW) == (
        'rank 3 of repo code in entry_point "cli build" '
        "(ordering only; repo code itself 40%), 2d old"
    )


def test_without_counted_samples_the_share_has_no_interval():
    row = _row(samples=None, run_samples=None, share_low=None, share_high=None)
    assert "(12% self; repo code itself 40%)" in symbol_line(row, NOW)


def test_measured_tier_is_not_ranked():
    line = symbol_line(_row(tier="measured", rank=14, samples=12), NOW)
    assert line.startswith("measured, not ranked (12 samples)")


def test_stale_row_is_flagged_without_rank_or_share():
    row = _row(freshness="stale", rank=None, measured_at=NOW - timedelta(days=9))
    assert symbol_line(row, NOW) == (
        'stale: measured in entry_point "cli build" before the body changed, 9d old'
    )


def test_body_unchanged_says_earlier_commit():
    assert symbol_line(_row(freshness="body_unchanged"), NOW).endswith(
        "2d old, earlier commit, same body"
    )


def test_file_line_caps_named_functions_and_counts_the_rest():
    rows = [_row(f"{FILE}::f{i}", rank=i + 1, self_share=0.10 - i / 1000) for i in range(5)]
    rows.append(_row(f"{FILE}::old", freshness="stale", rank=None))
    line = file_line(rows, NOW)
    assert line.startswith('in entry_point "cli build" (repo code itself 40%), 2d old: ')
    assert line.count(" rank ") == measured.MAX_NAMED_IN_FILE
    assert "f0 rank 1 (10% self, 10-14%)" in line
    assert "f3" not in line and "old rank" not in line
    assert line.endswith("; 2 more measured; 1 stale")


def test_file_line_names_only_the_lead_run():
    rows = [_row(f"{FILE}::a", rank=1), _row(f"{FILE}::b", rank=2, run_id="r2")]
    line = file_line(rows, NOW)
    assert "a rank 1" in line and "b rank" not in line
    # Another run's function is never counted under the lead run's basis.
    assert line.endswith("; 1 in other runs")
    assert "more measured" not in line
    rows += [_row(f"{FILE}::c{i}", rank=3 + i) for i in range(3)]
    rows.append(_row(f"{FILE}::old", freshness="stale", rank=None))
    assert file_line(rows, NOW).endswith("; 1 more measured; 1 in other runs; 1 stale")
    # One run, within the cap: no counts at all.
    assert file_line(rows[:1], NOW).endswith("a rank 1 (12% self, 10-14%)")


def test_file_line_all_stale():
    rows = [_row(freshness="stale", rank=None), _row(f"{FILE}::x", freshness="stale")]
    assert file_line(rows, NOW) == "stale: 2 functions measured before their bodies changed"


# -- end to end ----------------------------------------------------------------


async def _add(session, repo_id: str, symbol_id: str, **kw) -> None:
    row = _row(symbol_id, **kw).__dict__
    session.add(SymbolHotness(repository_id=repo_id, **row))
    await session.flush()


def _wire(response: dict) -> str:
    """The response as sent, minus the timing that differs call to call."""
    response = json.loads(json.dumps(response, default=str))
    response.get("_meta", {}).pop("timing_ms", None)
    return json.dumps(response, sort_keys=True)


TARGETS = [FILE, LOGIN, "src/db/models.py"]


@pytest.mark.asyncio
async def test_symbol_and_file_cards_carry_the_line(setup_mcp, session, populated_db):
    from repowise.server.mcp_server import get_context

    await _add(session, populated_db, LOGIN)
    result = await get_context(TARGETS)
    cards = result["targets"]
    assert cards[LOGIN]["type"] == "symbol"
    assert cards[LOGIN]["measured"].startswith('rank 3 of repo code in entry_point "cli build"')
    assert cards[FILE]["measured"].endswith("login rank 3 (12% self, 10-14%)")
    # No evidence for this file: no key at all, never an empty or null one.
    assert "measured" not in cards["src/db/models.py"]
    assert "_measured_target" not in json.dumps(result)


@pytest.mark.asyncio
async def test_no_evidence_is_byte_identical(setup_mcp, session, populated_db, monkeypatch):
    from repowise.server.mcp_server import get_context
    from repowise.server.mcp_server.tool_context import context as context_mod

    with_feature = _wire(await get_context(TARGETS))

    async def _off(session, repository_id, cards, **kw):
        for card in cards.values():
            card.pop(measured.MEASURED_TARGET, None)

    monkeypatch.setattr(context_mod, "attach_measured", _off)
    assert _wire(await get_context(TARGETS)) == with_feature
    monkeypatch.undo()

    # Rows elsewhere in the repo: the read runs, these cards stay as they were.
    await _add(session, populated_db, "src/other.py::g")
    assert _wire(await get_context(TARGETS)) == with_feature


@pytest.mark.asyncio
async def test_stale_symbol_is_flagged(setup_mcp, session, populated_db):
    from repowise.server.mcp_server import get_context

    await _add(session, populated_db, LOGIN, freshness="stale", rank=None)
    card = (await get_context([LOGIN]))["targets"][LOGIN]
    assert card["measured"].startswith("stale: measured in entry_point")


@pytest.mark.asyncio
async def test_store_without_the_table_answers_as_no_evidence(
    setup_mcp, session, populated_db, factory
):
    from repowise.server.mcp_server import get_context

    async with factory() as s:
        await s.execute(text("DROP TABLE symbol_hotness"))
        await s.commit()
    card = (await get_context([LOGIN]))["targets"][LOGIN]
    assert card["type"] == "symbol"
    assert "measured" not in card


def test_low_share_interval_never_rounds_past_the_estimate():
    row = _row(self_share=0.004, share_low=0.0016, share_high=0.0102)
    assert "(<1% self, 0-1%;" in symbol_line(row, NOW)
    # A lower end that is exact in decimal but not in binary keeps its value.
    row = _row(self_share=0.6, share_low=0.57, share_high=0.63)
    assert "(60% self, 57-63%;" in symbol_line(row, NOW)


def test_run_without_a_class_names_only_the_workload():
    assert 'rank 3 of repo code in "cli build"' in symbol_line(_row(workload_class=None), NOW)


def test_a_backslash_file_path_is_marked_with_forward_slashes():
    data: dict = {}
    measured.mark_measured_target(data, "file", "src\\auth\\service.py", None)
    assert data[measured.MEASURED_TARGET] == ("file", FILE)
    data = {}
    measured.mark_measured_target(data, "module", "src/auth", None)
    assert data == {}


@pytest.mark.asyncio
async def test_class_target_and_missed_symbol(setup_mcp, session, populated_db):
    from repowise.server.mcp_server import get_context

    await _add(session, populated_db, LOGIN)
    cls, miss = f"{FILE}::AuthService", f"{FILE}::no_such_name"
    cards = (await get_context([cls, miss]))["targets"]
    # Rows are per function: a class card has no line of its own.
    assert cards[cls]["type"] == "symbol"
    assert "measured" not in cards[cls]
    # A missed name degrades to the file's card, which carries the file's line.
    assert cards[miss]["resolved_to"] == FILE
    assert cards[miss]["measured"].endswith("login rank 3 (12% self, 10-14%)")
