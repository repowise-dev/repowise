"""File holds: a health finding on a vendored or generated file, or a history
marker on a test file, is held back on every default surface, whatever the
language, and each surface says how many and why.

Mirrors the runtime audit (findings under ``src/native/external`` served
although the index stores ``code_origin = vendored``) and the PowerToys one
(``ownership_risk`` and ``change_entropy`` served on unit-test files).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from repowise.core.analysis.finding_registry import FILE_HOLDS, file_hold, split_gated

_VENDORED = "src/external/brotli/static_dict.py"
_GENERATED = "src/proto/api_pb2.py"
_TEST = "tests/test_fuzzy_matcher.py"


def test_file_hold_reads_origin_and_test_flag():
    assert file_hold("health", "large_method", "vendored", False) == "vendored"
    assert file_hold("health", "complex_method", "generated", None) == "generated"
    assert file_hold("health", "ownership_risk", "test", True) == "test_history"
    assert file_hold("health", "change_entropy", "test", True) == "test_history"
    # A code-shape finding on a test file is still served.
    assert file_hold("health", "complex_method", "test", True) is None
    # A history marker on production code is context, held under its own key.
    assert file_hold("health", "ownership_risk", "production", False) == "history"
    # Unknown flag (row written before the column) is not a test file.
    assert file_hold("health", "ownership_risk", None, None) == "history"
    assert file_hold("health", "complex_method", "production", False) is None
    # Other layers are untouched.
    assert file_hold("dead_code", "unreachable_file", "vendored", False) is None


def test_split_gated_applies_file_holds_with_their_reason():
    rows = [
        {"file_path": _VENDORED, "biomarker_type": "large_method"},
        {"file_path": _TEST, "biomarker_type": "ownership_risk"},
        {"file_path": _TEST, "biomarker_type": "complex_method"},
        {"file_path": "src/app.py", "biomarker_type": "ownership_risk"},
    ]
    files = {
        _VENDORED: SimpleNamespace(code_origin="vendored", is_test=False),
        _TEST: SimpleNamespace(code_origin="test", is_test=True),
        "src/app.py": SimpleNamespace(code_origin="production", is_test=False),
    }
    shown, gated = split_gated(
        "health", rows, kind="biomarker_type", get=lambda r, k: r[k], files=files
    )
    assert [(r["file_path"], r["biomarker_type"]) for r in shown] == [
        (_TEST, "complex_method"),
    ]
    assert gated == {
        "vendored": {"count": 1, "reason": FILE_HOLDS["vendored"]},
        "test_history": {"count": 1, "reason": FILE_HOLDS["test_history"]},
        "history": {"count": 1, "reason": FILE_HOLDS["history"]},
    }
    # Without the metric rows nothing is held by file (unknown is not a hold).
    assert len(split_gated("health", rows, kind="biomarker_type", get=lambda r, k: r[k])[0]) == 4
    assert split_gated(
        "health", rows, kind="biomarker_type", get=lambda r, k: r[k], files=files,
        include_unverified=True,
    ) == (rows, {})


async def _seed(session, rid: str) -> None:
    from repowise.core.persistence.crud import upsert_health_findings, upsert_health_metrics

    def metric(path: str, origin: str, is_test: bool) -> dict:
        return {
            "file_path": path,
            "score": 4.0,
            "max_ccn": 25,
            "max_nesting": 5,
            "nloc": 300,
            "has_test_file": False,
            "module": "x",
            "code_origin": origin,
            "is_test": is_test,
        }

    def finding(path: str, kind: str, impact: float) -> dict:
        return {
            "file_path": path,
            "biomarker_type": kind,
            "severity": "high",
            "function_name": "run",
            "line_start": 1,
            "line_end": 90,
            "details": {},
            "health_impact": impact,
            "reason": f"{kind} on {path}",
        }

    await upsert_health_metrics(
        session,
        rid,
        [
            metric(_VENDORED, "vendored", False),
            metric(_GENERATED, "generated", False),
            metric(_TEST, "test", True),
        ],
    )
    await upsert_health_findings(
        session,
        rid,
        [
            finding(_VENDORED, "large_method", 2.0),
            finding(_GENERATED, "complex_method", 2.0),
            finding(_TEST, "ownership_risk", 1.0),
            finding(_TEST, "change_entropy", 1.0),
            finding(_TEST, "complex_method", 1.0),
        ],
        file_paths=[_VENDORED, _GENERATED, _TEST],
    )
    await session.commit()


@pytest.mark.asyncio
async def test_crud_reader_holds_back_by_file(session, health_data):
    from repowise.core.persistence.crud import get_health_findings

    await _seed(session, health_data)
    served = {(f.file_path, f.biomarker_type) for f in await get_health_findings(session, health_data)}
    assert not {p for p, _ in served} & {_VENDORED, _GENERATED}
    assert (_TEST, "complex_method") in served
    assert not {(_TEST, "ownership_risk"), (_TEST, "change_entropy")} & served
    assert "src/auth/service.py" in {p for p, _ in served}

    opted = {
        (f.file_path, f.biomarker_type)
        for f in await get_health_findings(session, health_data, include_unverified=True)
    }
    assert {(_VENDORED, "large_method"), (_TEST, "ownership_risk")} <= opted


@pytest.mark.asyncio
async def test_get_health_holds_back_by_file_and_says_so(setup_mcp, session, health_data):
    from repowise.server.mcp_server import get_health

    await _seed(session, health_data)

    default = await get_health(only=["top_findings"], limit=50)
    rows = default["top_findings"] + default.get("test_findings", [])
    files = {f["file_path"] for f in rows}
    assert not files & {_VENDORED, _GENERATED}
    # Compact: a count per hold, no reason text while findings are listed.
    gated = default["_meta"]["gated"]
    assert gated["health"] == {"vendored": 1, "generated": 1, "test_history": 2}
    assert "reasons" not in gated

    targeted = await get_health(targets=[_VENDORED])
    assert not targeted.get("findings")
    # Nothing listed: the reason comes once, so the empty list is explained.
    assert targeted["_meta"]["gated"]["health"] == {"vendored": 1}
    assert targeted["_meta"]["gated"]["reasons"] == {"vendored": FILE_HOLDS["vendored"]}


def _no_holds(monkeypatch) -> None:
    from repowise.core.analysis import finding_registry
    from repowise.core.persistence.crud.analysis import shown as shown_module

    monkeypatch.setattr(shown_module, "_file_holds", lambda *a, **k: [])
    monkeypatch.setattr(finding_registry, "row_hold", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_get_health_kpis_and_worst_files_ignore_holds(
    setup_mcp, session, health_data, monkeypatch
):
    # The holds shorten finding lists; the KPIs and the worst-file order read
    # what the scores counted, so they must not move.
    from repowise.server.mcp_server import get_health

    await _seed(session, health_data)
    held = await get_health(only=["kpis", "worst_files"])
    _no_holds(monkeypatch)
    plain = await get_health(only=["kpis", "worst_files"])
    assert held["kpis"] == plain["kpis"]
    assert held["worst_files"] == plain["worst_files"]


@pytest.mark.asyncio
async def test_default_get_health_does_not_grow(setup_mcp, session, health_data, monkeypatch):
    # No gated language and no held row in the seeded repository: the default
    # response is byte-identical with and without the holds.
    from repowise.server.mcp_server import get_health

    def size(result: dict) -> int:
        result = {**result, "_meta": {k: v for k, v in result["_meta"].items() if k != "timing_ms"}}
        return len(json.dumps(result, sort_keys=True, default=str))

    with_holds = size(await get_health())
    _no_holds(monkeypatch)
    assert size(await get_health()) == with_holds


@pytest.mark.asyncio
async def test_default_get_health_grows_by_a_count_per_hold(
    setup_mcp, session, health_data, monkeypatch
):
    # With held rows the list loses them and ``_meta.gated`` gains one integer
    # per hold key: never a reason sentence while findings are listed.
    from repowise.server.mcp_server import get_health

    await _seed(session, health_data)
    result = await get_health()
    gated = result["_meta"]["gated"]
    assert set(gated) == {"health", "opt_in"}
    assert all(isinstance(v, int) for v in gated["health"].values())
    assert len(json.dumps(gated["health"])) < 64
