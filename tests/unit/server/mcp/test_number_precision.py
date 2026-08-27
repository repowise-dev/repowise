"""No raw double, and no two scales under one key, ever reach an agent.

Two regressions this file exists to prevent, both found by dogfooding the
live MCP surface:

1. **Full-precision floats.** ``hotspot_score: 0.791581408944753`` and
   ``pagerank: 0.0005258389771750028`` shipped verbatim from SQL/NumPy. Every
   digit past the fourth is tokens the agent pays for and cannot act on. Fixed
   centrally in ``_rounding.quantize``, composed into every tool by
   ``tool_middleware`` — so the guard here wraps tools in the *real*
   composition rather than asserting per-field, and a tool added later is
   covered without touching this file.

2. **One key, two scales.** ``change_entropy_pct`` was 0-1 in
   ``get_health.findings[].details`` and 0-100 in ``metrics[].signals`` of the
   same response; ``churn_percentile`` likewise. An agent reading 0.973 as a
   percentage reports "0.97%" for a 97th-percentile file.
"""

from __future__ import annotations

import math

import pytest

from repowise.server.mcp_server._rounding import round_float, round_numbers

# Mirrors _rounding._SIG_DIGITS. Duplicated deliberately: if someone widens the
# constant, this suite should fail and make them justify it, not silently
# follow along.
_SIG_DIGITS = 4


# ---------------------------------------------------------------------------
# The rounding primitives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The two values that prompted the fix.
        (0.791581408944753, 0.7916),
        (0.0005258389771750028, 0.0005258),
        # Significant digits, not decimal places: a small magnitude keeps its
        # ranking information instead of collapsing to 0.0.
        (0.00012927455637682034, 0.0001293),
        (1.2345678e-08, 1.235e-08),
        # Already short, or exactly representable: unchanged.
        (0.92, 0.92),
        (8.7, 8.7),
        (100.0, 100.0),
        (0.0, 0.0),
        # Large magnitudes stay float-typed and do not gain width.
        (1149.5, 1150.0),
        (536836.0, 536836.0),
        # Sign is preserved.
        (-0.791581408944753, -0.7916),
    ],
)
def test_round_float_keeps_four_significant_digits(raw, expected):
    assert round_float(raw) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_becomes_none(bad):
    """NaN/Infinity are not JSON — a strict parser rejects the bare literal."""
    assert round_float(bad) is None


def test_round_numbers_walks_nested_payloads():
    payload = {
        "targets": {
            "a.py": {
                "hotspot_score": 0.791581408944753,
                "impact_surface": [{"pagerank": 0.0005258389771750028}],
                "co_change": ({"count": 5.5199999999}, 1.23456789),
            }
        }
    }
    out = round_numbers(payload)
    target = out["targets"]["a.py"]
    assert target["hotspot_score"] == 0.7916
    assert target["impact_surface"][0]["pagerank"] == 0.0005258
    assert target["co_change"][0]["count"] == 5.52
    assert target["co_change"][1] == 1.235


def test_ints_and_bools_are_not_touched():
    """Line numbers, counts and flags must keep their exact type."""
    payload = {"line_start": 30, "is_hotspot": True, "bus_factor": 1, "name": "x"}
    out = round_numbers(dict(payload))
    assert out == payload
    assert isinstance(out["line_start"], int)
    assert isinstance(out["is_hotspot"], bool)


def test_rounding_is_idempotent():
    once = round_numbers({"score": 0.791581408944753})
    twice = round_numbers(dict(once))
    assert once == twice


# ---------------------------------------------------------------------------
# The guard that survives new tools: the real middleware composition
# ---------------------------------------------------------------------------


def _offending_floats(obj, path="$"):
    """Every float in *obj* carrying more than _SIG_DIGITS significant digits.

    Returns ``(path, value)`` pairs so a failure names the exact field.
    """
    bad = []
    if isinstance(obj, bool | int):
        return bad
    if isinstance(obj, float):
        # Non-finite is never valid JSON; anything the rounder would change
        # still carries digits the agent pays for.
        if math.isnan(obj) or math.isinf(obj) or obj != round_float(obj):
            bad.append((path, obj))
        return bad
    if isinstance(obj, dict):
        for key, val in obj.items():
            bad.extend(_offending_floats(val, f"{path}.{key}"))
        return bad
    if isinstance(obj, list | tuple):
        for i, val in enumerate(obj):
            bad.extend(_offending_floats(val, f"{path}[{i}]"))
        return bad
    return bad


def test_the_detector_actually_detects():
    """Guard the guard — a walker that never fires would make this file inert."""
    assert _offending_floats({"a": {"b": [0.791581408944753]}}) == [
        ("$.a.b[0]", 0.791581408944753)
    ]
    assert _offending_floats({"a": float("nan")})
    assert _offending_floats({"ok": 0.7916, "n": 12, "flag": True}) == []


@pytest.mark.asyncio
async def test_tool_middleware_rounds_a_tool_response():
    """The composition wired in ``ensure_full_surface`` must include rounding.

    Wraps a stand-in tool in the real ``tool_middleware`` so removing the
    quantize layer fails here rather than silently shipping raw doubles.
    """
    from repowise.server.mcp_server import tool_middleware

    async def get_thing():  # stands in for a registered tool
        return {"result": {"hotspot_score": 0.791581408944753, "n": 3}}

    wrapped = tool_middleware(get_thing)
    out = await wrapped()
    assert out["result"]["hotspot_score"] == 0.7916
    assert out["result"]["n"] == 3
    assert _offending_floats(out) == []


@pytest.mark.asyncio
async def test_middleware_preserves_the_tool_signature():
    """FastMCP builds each tool's schema from its signature."""
    import inspect

    from repowise.server.mcp_server import tool_middleware

    async def get_thing(targets: list[str], limit: int = 5) -> dict:
        return {}

    assert inspect.signature(tool_middleware(get_thing)) == inspect.signature(get_thing)


#: A full-precision percentile, as ``PERCENT_RANK()`` actually returns it.
#: The seeded fixture values (0.92, 0.65) are already short, so a payload scan
#: over stock fixtures passes with or without the fix — this is what makes the
#: end-to-end assertion below bite.
_RAW_PERCENTILE = 0.791581408944753


@pytest.mark.asyncio
async def test_real_tool_payload_rounds_a_seeded_raw_double(setup_mcp):
    """End-to-end over a real payload, through the real middleware.

    ``get_risk`` is the worst historical offender: hotspot_score, owner_pct,
    recent_owner_pct and impact_surface[].pagerank all shipped raw. Seeding the
    git row with a genuine ``PERCENT_RANK()`` output proves the rounding runs on
    the real path, not just on a synthetic dict.
    """
    import sqlalchemy as sa

    import repowise.server.mcp_server as mcp_mod
    from repowise.core.persistence.models import GitMetadata
    from repowise.server.mcp_server import get_risk, tool_middleware

    async with mcp_mod._session_factory() as session:
        await session.execute(
            sa.update(GitMetadata)
            .where(GitMetadata.file_path == "src/auth/service.py")
            .values(churn_percentile=_RAW_PERCENTILE)
        )
        await session.commit()

    raw = await get_risk(["src/auth/service.py"])
    assert raw["targets"]["src/auth/service.py"]["hotspot_score"] == _RAW_PERCENTILE, (
        "fixture no longer carries the raw value — this test would be vacuous"
    )

    out = await tool_middleware(get_risk)(["src/auth/service.py"])
    assert out["targets"]["src/auth/service.py"]["hotspot_score"] == 0.7916
    offenders = _offending_floats(out, "$get_risk")
    assert not offenders, f"raw doubles in get_risk: {offenders[:5]}"


@pytest.mark.asyncio
async def test_other_tool_payloads_carry_no_raw_doubles(setup_mcp):
    """Breadth pass over the remaining high-float tools."""
    from repowise.server.mcp_server import get_context, get_dead_code, tool_middleware

    for tool, args in ((get_context, (["src/auth/service.py"],)), (get_dead_code, ())):
        out = await tool_middleware(tool)(*args)
        offenders = _offending_floats(out, f"${tool.__name__}")
        assert not offenders, f"raw doubles in {tool.__name__}: {offenders[:5]}"


# ---------------------------------------------------------------------------
# One key, one scale
# ---------------------------------------------------------------------------


def test_change_entropy_pct_is_zero_to_one_hundred():
    """The biomarker detail must match FileSignals._entropy_pct's contract."""
    from repowise.core.analysis.health.signals import _entropy_pct

    stored = 0.973  # the column is 0-1
    assert _entropy_pct(stored) == 97.3
    assert round(stored * 100.0, 1) == _entropy_pct(stored)


def test_churn_percentile_is_zero_to_one_hundred():
    from repowise.core.analysis.health.churn_complexity import _churn_pct

    assert _churn_pct(0.989) == 98.9


def _file_context(**git_meta):
    from repowise.core.analysis.health.biomarkers.base import FileContext

    return FileContext(
        file_path="src/app.py",
        language="python",
        nloc=10,
        has_test_file=False,
        module="src",
        git_meta=git_meta,
    )


def test_change_entropy_biomarker_emits_percentile_on_the_zero_to_one_hundred_scale():
    """Stored 0-1 in, emitted 0-100 out — the same contract FileSignals uses.

    0.973 must not reach the agent as ``0.973`` beside a ``metrics[].signals``
    copy of the same field reading ``97.3``.
    """
    from repowise.core.analysis.health.biomarkers.change_entropy import BIOMARKER

    results = BIOMARKER.detect(
        _file_context(change_entropy=3.5264, change_entropy_pct=0.973, commit_count_90d=10)
    )
    assert results, "detector did not fire — preconditions changed, fix the fixture"
    assert results[0].details["change_entropy_pct"] == 97.3


def test_churn_risk_biomarker_emits_percentile_on_the_zero_to_one_hundred_scale():
    from repowise.core.analysis.health.biomarkers.churn_risk import BIOMARKER

    results = BIOMARKER.detect(
        _file_context(
            churn_percentile=0.989,
            lines_added_90d=427,
            lines_deleted_90d=538,
            commit_count_90d=5,
            is_hotspot=True,
        )
    )
    assert results, "detector did not fire — preconditions changed, fix the fixture"
    assert results[0].details["churn_percentile"] == 98.9


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (0.973, "top 2.7% among eligible files"),
        (0.99, "top 1% among eligible files"),
        (0.999, "top 0.1% among eligible files"),
        (0.9995, "top <0.1% among eligible files"),
        (1.0, "top <0.1% among eligible files"),
    ],
)
def test_health_percentile_formatter_is_bounded_and_preserves_precision(stored, expected):
    from repowise.core.analysis.health.semantics import format_top_percentile

    assert format_top_percentile(stored, "eligible files") == expected
    assert "top 0%" not in expected


def test_health_biomarkers_share_honest_extreme_percentile_wording():
    from repowise.core.analysis.health.biomarkers.change_entropy import (
        BIOMARKER as ENTROPY_BIOMARKER,
    )
    from repowise.core.analysis.health.biomarkers.churn_risk import (
        BIOMARKER as CHURN_BIOMARKER,
    )

    entropy = ENTROPY_BIOMARKER.detect(
        _file_context(change_entropy=4.0, change_entropy_pct=1.0, commit_count_90d=10)
    )[0]
    churn = CHURN_BIOMARKER.detect(
        _file_context(
            churn_percentile=1.0,
            lines_added_90d=10,
            lines_deleted_90d=0,
            commit_count_90d=5,
            is_hotspot=True,
        )
    )[0]
    assert "top <0.1%" in entropy.reason
    assert "top <0.1%" in churn.reason
    assert "top 0%" not in entropy.reason
    assert "top 0%" not in churn.reason
    assert entropy.details["change_entropy_pct"] == 100.0
    assert churn.details["churn_percentile"] == 100.0


# ---------------------------------------------------------------------------
# NaN at the source, not stripped per-boundary
# ---------------------------------------------------------------------------


def test_unknown_change_risk_feature_is_none_not_nan():
    """``exp`` is unknown on a diff-only caller; NaN there is not valid JSON."""
    import json

    from repowise.core.analysis.change_risk.model import ChangeFeatures, score_change

    risk = score_change(ChangeFeatures(la=10, ld=2, nf=1, nd=1, ns=1, entropy=0.5, exp=None))
    exp_driver = next(d for d in risk.drivers if d.feature == "exp")
    assert exp_driver.value is None

    payload = [{"feature": d.feature, "value": d.value} for d in risk.top_drivers]
    json.dumps(payload, allow_nan=False)  # raises ValueError if a NaN survives
