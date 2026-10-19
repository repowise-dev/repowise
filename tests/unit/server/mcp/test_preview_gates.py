"""The ``preview`` gate state: a (layer, language) row that is served by
default, counted as served, and labelled with its measurement.

Tests patch ``LANGUAGE_GATES`` rather than read today's table, so they pin the
mechanism: one row moves gated -> preview -> ungated by a table edit and every
surface follows.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.analysis import finding_registry
from repowise.core.analysis.finding_registry import (
    LanguageGate,
    gate_for,
    gated_summary,
    label_previews,
    preview_for,
    preview_lines,
    preview_note,
    served_previews,
    split_gated,
)
from tests.unit.server.mcp.test_language_gates import _seed_gated_dead_code, _seed_java_health

_NOTE = "preview, measured 22% precision on 55 judged plans from 2 Java repos, 2026-10-15"


def _row(layer: str, state: str, precision: float | None = 0.22) -> LanguageGate:
    return LanguageGate(
        layer, "java", precision, "55 judged plans from 2 Java repos", "2026-10-15", "test",
        state=state,
    )


def _table(monkeypatch, *rows: LanguageGate) -> None:
    monkeypatch.setattr(finding_registry, "LANGUAGE_GATES", rows)


def _get(row: dict, key: str):
    return row.get(key)


def test_shipped_table_states():
    # The one live-table pin: what ships today. Everything else patches a table.
    states = {(g.layer, g.language): g.state for g in finding_registry.LANGUAGE_GATES}
    audited = ("java", "csharp", "c", "cpp")
    expected = {
        ("health", "cpp"): "preview",
        **{(layer, lang): "preview" for layer in ("refactoring", "performance") for lang in audited},
        **{("dead_code", lang): "preview" for lang in (*audited, "kotlin", "swift")},
    }
    assert states == expected
    assert all(finding_registry.gates_on(layer) == () for layer in ("health", "refactoring",
                                                                     "performance", "dead_code"))
    health_cpp = finding_registry.preview_for("health", "src/a.cpp")
    assert health_cpp is not None and health_cpp.measure == "not wrong"
    for path in ("src/A.java", "src/A.cs", "src/a.c"):
        assert finding_registry.preview_for("health", path) is None


def test_preview_note_quotes_the_measurement():
    assert preview_note(_row("refactoring", "preview")) == _NOTE
    unmeasured = LanguageGate(
        "performance", "c", None, "no served opportunities on 2 C repos", "2026-10-15", "x",
        state="preview",
    )
    assert preview_note(unmeasured) == (
        "preview, not measured: no served opportunities on 2 C repos, 2026-10-15"
    )
    not_wrong = LanguageGate(
        "health", "java", 0.95, "40 judged findings from 2 Java repos", "2026-10-20", "x",
        state="preview", measure="not wrong",
    )
    assert preview_note(not_wrong).startswith("preview, measured 95% not wrong on 40 judged")
    assert "—" not in _NOTE


def test_one_row_moves_gated_to_preview_to_ungated(monkeypatch):
    rows = [{"file_path": "src/A.java", "kind": "x"}, {"file_path": "a.py", "kind": "x"}]

    _table(monkeypatch, _row("refactoring", "gated"))
    shown, gated = split_gated("refactoring", rows, kind="kind", get=_get)
    assert [r["file_path"] for r in shown] == ["a.py"]
    assert gated["java"]["count"] == 1
    assert preview_for("refactoring", "src/A.java") is None

    _table(monkeypatch, _row("refactoring", "preview"))
    shown, gated = split_gated("refactoring", rows, kind="kind", get=_get)
    # Served and not counted as held back: the list and the count agree.
    assert shown == rows and gated == {}
    assert gate_for("refactoring", "src/A.java") is None
    assert gated_summary("refactoring", {"java": 3}) == {}
    assert served_previews("refactoring", rows, kind="kind", get=_get) == {"java": _NOTE}
    labelled = label_previews("refactoring", rows, kind="kind")
    assert labelled[0]["preview"] == _NOTE and "preview" not in labelled[1]
    assert "preview" not in rows[0]

    _table(monkeypatch)
    assert split_gated("refactoring", rows, kind="kind", get=_get) == (rows, {})
    assert served_previews("refactoring", rows, kind="kind", get=_get) == {}


def test_a_preview_row_is_scoped_to_its_layer(monkeypatch):
    _table(monkeypatch, _row("refactoring", "preview"), _row("dead_code", "gated"))
    assert preview_for("refactoring", "A.java") is not None
    assert preview_for("dead_code", "A.java") is None
    assert gate_for("dead_code", "A.java") is not None


def test_preview_lines_name_layer_and_language(monkeypatch):
    _table(monkeypatch, _row("refactoring", "preview"))
    rows = [{"file_path": "A.java", "refactoring_type": "extract_method"}] * 2
    assert preview_lines("refactoring", rows, kind="refactoring_type", get=_get) == [
        f"Refactoring on Java: {_NOTE}"
    ]
    assert preview_lines("refactoring", [{"file_path": "a.py"}], kind="k", get=_get) == []


def test_cli_prints_one_dim_line_for_listed_preview_rows(monkeypatch):
    from repowise.cli.commands.health_cmd import refactoring_targets
    from repowise.cli.helpers import console

    _table(monkeypatch, _row("refactoring", "preview"))
    opportunities = [{"file_path": "A.java", "lead_refactoring_type": "extract_method"}]
    plans = [{"file_path": "B.java", "refactoring_type": "extract_method"}]
    with console.capture() as out:
        refactoring_targets._render_preview_lines(opportunities, plans)
    assert out.get().strip() == f"Refactoring on Java: {_NOTE}"
    with console.capture() as out:
        refactoring_targets._render_preview_lines([{"file_path": "a.py"}], [])
    assert out.get() == ""


# ---------------------------------------------------------------------------
# SQL readers: counts and lists agree for a preview row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sql_readers_serve_and_count_a_preview_row(monkeypatch, session, health_data):
    from repowise.core.persistence.crud import (
        gated_health_counts,
        get_dead_code_findings,
        get_dead_code_summary,
        get_health_findings,
    )

    await _seed_java_health(session, health_data)
    await _seed_gated_dead_code(session, health_data, ("src/Billing.java",))

    _table(monkeypatch, _row("health", "gated"), _row("dead_code", "gated"))
    held = await get_dead_code_summary(session, health_data)
    assert held["gated"]["java"]["count"] == 1

    _table(monkeypatch, _row("health", "preview"), _row("dead_code", "preview"))
    paths = {f.file_path for f in await get_health_findings(session, health_data)}
    assert "src/Billing.java" in paths
    assert "java" not in await gated_health_counts(session, health_data)
    dead = {f.file_path for f in await get_dead_code_findings(session, health_data)}
    assert "src/Billing.java" in dead
    summary = await get_dead_code_summary(session, health_data)
    assert summary["gated"] == {}
    assert summary["total_findings"] == held["total_findings"] + 1


# ---------------------------------------------------------------------------
# MCP: ``_meta.preview`` names only the cells the response lists
# ---------------------------------------------------------------------------


def _size(result: dict) -> int:
    meta = {k: v for k, v in result["_meta"].items() if k != "timing_ms"}
    return len(json.dumps({**result, "_meta": meta}, sort_keys=True, default=str))


@pytest.mark.asyncio
async def test_get_health_labels_served_preview_cells_in_meta(
    monkeypatch, setup_mcp, session, health_data
):
    from repowise.server.mcp_server import get_health

    await _seed_java_health(session, health_data)
    _table(monkeypatch, _row("health", "preview"), _row("refactoring", "preview"))

    result = await get_health(only=["top_findings"], limit=50)
    assert "src/Billing.java" in {f["file_path"] for f in result["top_findings"]}
    # Only the pair the response lists: no refactoring rows were sent.
    assert result["_meta"]["preview"] == {"health": {"java": preview_note(_row("health", "x"))}}
    assert "gated" not in result["_meta"]
    # Nothing per row.
    assert all("preview" not in f for f in result["top_findings"])

    # A response that lists no row of the cell carries no label.
    kpis = await get_health(only=["kpis"])
    assert "preview" not in kpis["_meta"]


@pytest.mark.asyncio
async def test_default_get_health_without_preview_rows_does_not_grow(
    monkeypatch, setup_mcp, session, health_data
):
    from repowise.server.mcp_server import get_health

    # The seeded repository is Python: the shipped table adds nothing.
    shipped = await get_health()
    assert "preview" not in shipped["_meta"]
    _table(monkeypatch)
    assert _size(await get_health()) == _size(shipped)


@pytest.mark.asyncio
async def test_get_dead_code_labels_served_preview_cells_in_meta(
    monkeypatch, setup_mcp, session, health_data
):
    from repowise.server.mcp_server import get_dead_code

    await _seed_gated_dead_code(session, health_data, ("src/Billing.java",))
    _table(monkeypatch, _row("dead_code", "preview"))

    result = await get_dead_code(min_confidence=0.0)
    paths = {f["file_path"] for t in result["tiers"].values() for f in t["findings"]}
    assert "src/Billing.java" in paths
    assert result["_meta"]["preview"] == {"dead_code": {"java": preview_note(_row("x", "x"))}}
    assert "gated" not in result["summary"]

    _table(monkeypatch)
    assert "preview" not in (await get_dead_code(min_confidence=0.0))["_meta"]


@pytest.mark.asyncio
async def test_get_health_labels_refactoring_preview_cells(
    monkeypatch, setup_mcp, session, health_data
):
    from repowise.core.persistence import crud
    from repowise.server.mcp_server import get_health
    from tests.unit.server.test_language_gates_rest import _plan

    await _seed_java_health(session, health_data)
    await crud.save_refactoring_suggestions(
        session, health_data, [_plan("src/Billing.java", "charge")]
    )
    await crud.finalize_refactoring_opportunities(session, health_data, analyzed_commit="c" * 40)
    await session.commit()
    _table(monkeypatch, _row("refactoring", "preview"))

    result = await get_health(
        include=["refactoring"], only=["refactoring_plans", "refactoring_opportunities"]
    )
    listed = {
        r["file_path"]
        for key in ("refactoring_plans", "refactoring_opportunities")
        for r in result.get(key) or []
    }
    assert "src/Billing.java" in listed
    assert result["_meta"]["preview"] == {"refactoring": {"java": _NOTE}}


@pytest.mark.asyncio
async def test_get_health_labels_file_rows_and_fix_first_in_one_map(
    monkeypatch, setup_mcp, session, health_data
):
    from repowise.server.mcp_server import get_health

    await _seed_java_health(session, health_data)
    _table(monkeypatch, _row("health", "preview"))
    note = preview_note(_row("health", "x"))

    # File rows alone label the cell.
    rows = await get_health(only=["worst_files"], limit=50)
    assert "src/Billing.java" in {r["file_path"] for r in rows["worst_files"]}
    assert rows["_meta"]["preview"] == {"health": {"java": note}}

    # Fix first items are labelled by the layer their kind was built from.
    queue = await get_health(only=["fix_first"])
    items = [*(queue.get("fix_first") or {}).get("items", [])]
    on_java = [i for i in items if i["target"]["file_path"] == "src/Billing.java"]
    if on_java:
        assert queue["_meta"]["preview"]["health"] == {"java": note}

    # The whole dashboard: one map, nothing per row.
    dashboard = await get_health()
    assert dashboard["_meta"]["preview"] == {"health": {"java": note}}
    assert len(json.dumps(dashboard["_meta"]["preview"])) < 160
    for value in dashboard.values():
        if isinstance(value, list):
            assert all("preview" not in r for r in value if isinstance(r, dict))


def test_fix_first_items_label_the_layer_their_kind_came_from(monkeypatch):
    from repowise.server.mcp_server.tool_health.tool import _attach_preview

    _table(monkeypatch, _row("refactoring", "preview"), _row("health", "preview"))
    result = {
        "fix_first": {
            "lead": {"kind": "refactor", "target": {"file_path": "src/A.java"}},
            "items": [
                {"kind": "refactor", "target": {"file_path": "src/A.java"}},
                {"kind": "perf_fix", "target": {"file_path": "src/B.java"}},
                {"kind": "finding", "target": {"file_path": "src/c.py"}},
            ],
        },
        "_meta": {},
    }
    _attach_preview(result)
    assert result["_meta"]["preview"] == {"refactoring": {"java": _NOTE}}
    assert "preview" not in result["fix_first"]["items"][0]
