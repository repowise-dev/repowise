"""Layer x language gates: a layer measured below the precision bar on a
language is held back on that language's files, everywhere, unless the caller
opts in, and every surface says how many rows it held back.

The seeded repository is Python; these tests add Java, C# and C++ rows beside
it, so each case checks both halves: the gated rows leave, the Python rows stay.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.core.analysis import finding_registry
from repowise.core.analysis.finding_registry import (
    LANGUAGE_GATES,
    LanguageGate,
    gate_for,
    gated_extensions,
    language_of,
    split_gated,
)
from repowise.core.persistence.models import DeadCodeFinding


def test_audited_languages_are_gated_on_every_layer():
    for layer in ("health", "refactoring", "performance", "dead_code"):
        for path in ("src/Foo.java", "src/Foo.cs", "src/foo.c", "src/foo.cpp", "inc/foo.h"):
            assert gate_for(layer, path) is not None, (layer, path)


def test_unaudited_languages_are_never_gated():
    for layer in ("health", "refactoring", "performance", "dead_code"):
        for path in ("a.py", "a.ts", "a.tsx", "a.js", "a.go", "a.rs", "a.kt", "pom.xml", ""):
            assert gate_for(layer, path) is None, (layer, path)


def test_every_gate_names_a_language_with_extensions_and_a_reason():
    for gate in LANGUAGE_GATES:
        assert gated_extensions(gate.language), gate.language
        assert gate.reason and 0.0 <= gate.precision < 0.9
    assert language_of("x/Y.JAVA") == "java"


def test_a_gate_can_name_kinds(monkeypatch):
    monkeypatch.setattr(
        finding_registry,
        "LANGUAGE_GATES",
        (
            LanguageGate(
                "dead_code", "csharp", 0.1, "test", "2026-10-02", "test",
                kinds=frozenset({"unreachable_file"}),
            ),
        ),
    )
    assert gate_for("dead_code", "A.cs", "unreachable_file") is not None
    assert gate_for("dead_code", "A.cs", "unused_export") is None
    assert gate_for("health", "A.cs", "complex_method") is None


def test_split_gated_counts_what_it_holds_back():
    rows = [
        {"file_path": "A.java", "kind": "unused_export"},
        {"file_path": "B.java", "kind": "unused_export"},
        {"file_path": "c.py", "kind": "unused_export"},
    ]
    shown, gated = split_gated("dead_code", rows, kind="kind", get=lambda r, k: r[k])
    assert [r["file_path"] for r in shown] == ["c.py"]
    assert gated["java"]["count"] == 2 and gated["java"]["reason"]


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------


async def _seed_java_health(session, rid: str) -> None:
    from repowise.core.persistence.crud import upsert_health_findings, upsert_health_metrics

    gated = ["src/Billing.java"]
    await upsert_health_metrics(
        session,
        rid,
        [
            {
                "file_path": "src/Billing.java",
                "score": 3.0,
                "max_ccn": 30,
                "max_nesting": 6,
                "nloc": 400,
                "has_test_file": False,
                "module": "billing",
            }
        ],
    )
    await upsert_health_findings(
        session,
        rid,
        [
            {
                "file_path": "src/Billing.java",
                "biomarker_type": "complex_method",
                "severity": "critical",
                "function_name": "charge",
                "line_start": 10,
                "line_end": 200,
                "details": {"ccn": 30},
                "health_impact": 3.0,
                "reason": "charge has cyclomatic complexity 30",
            }
        ],
        file_paths=gated,
    )
    await session.commit()


async def _seed_gated_dead_code(session, rid: str) -> None:
    now = datetime.now(UTC)
    for i, path in enumerate(("src/Billing.java", "src/Ui/Form.cs")):
        session.add(
            DeadCodeFinding(
                id=f"gated{i}",
                repository_id=rid,
                kind="unreachable_file",
                file_path=path,
                confidence=0.9,
                reason="No imports found",
                lines=80,
                safe_to_delete=True,
                status="open",
                analyzed_at=now,
            )
        )
    await session.commit()


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crud_readers_hold_back_gated_rows(session, health_data):
    from repowise.core.persistence.crud import (
        get_dead_code_findings,
        get_dead_code_summary,
        get_health_findings,
    )

    await _seed_java_health(session, health_data)
    await _seed_gated_dead_code(session, health_data)

    paths = {f.file_path for f in await get_health_findings(session, health_data)}
    assert "src/Billing.java" not in paths and "src/auth/service.py" in paths
    opted = {
        f.file_path
        for f in await get_health_findings(session, health_data, include_unverified=True)
    }
    assert "src/Billing.java" in opted

    dead = {f.file_path for f in await get_dead_code_findings(session, health_data)}
    assert not dead & {"src/Billing.java", "src/Ui/Form.cs"}
    assert "src/legacy/old_auth.py" in dead
    summary = await get_dead_code_summary(session, health_data)
    assert summary["gated"]["java"]["count"] == 1
    assert summary["gated"]["csharp"]["count"] == 1
    assert summary["total_findings"] == 3
    opted_summary = await get_dead_code_summary(session, health_data, include_unverified=True)
    assert opted_summary["total_findings"] == 5 and opted_summary["gated"] == {}


@pytest.mark.asyncio
async def test_get_health_holds_back_gated_findings_and_says_so(setup_mcp, session, health_data):
    from repowise.server.mcp_server import get_health

    await _seed_java_health(session, health_data)

    default = await get_health(only=["top_findings"], limit=50)
    files = {f["file_path"] for f in default["top_findings"]}
    assert "src/Billing.java" not in files and "src/auth/service.py" in files
    gated = default["_meta"]["gated"]
    assert gated["health"]["java"]["count"] == 1
    assert gated["opt_in"] == {"include": ["unverified"]}

    opted = await get_health(only=["top_findings"], include=["unverified"], limit=50)
    assert "src/Billing.java" in {f["file_path"] for f in opted["top_findings"]}
    assert "gated" not in opted["_meta"]

    # Targeted at the gated file: nothing listed, and the count says why.
    targeted = await get_health(targets=["src/Billing.java"])
    assert not targeted.get("findings")
    assert targeted["_meta"]["gated"]["health"]["java"]["count"] == 1


@pytest.mark.asyncio
async def test_get_dead_code_holds_back_gated_findings_and_says_so(setup_mcp, session, health_data):
    from repowise.server.mcp_server import get_dead_code

    await _seed_gated_dead_code(session, health_data)

    def _paths(result):
        return {f["file_path"] for t in result["tiers"].values() for f in t["findings"]}

    default = await get_dead_code(min_confidence=0.0)
    assert not _paths(default) & {"src/Billing.java", "src/Ui/Form.cs"}
    assert "src/legacy/old_auth.py" in _paths(default)
    gated = default["summary"]["gated"]
    assert gated["java"]["count"] == 1 and gated["csharp"]["count"] == 1
    assert gated["opt_in"] == {"include_unverified": True}

    opted = await get_dead_code(min_confidence=0.0, include_unverified=True)
    assert {"src/Billing.java", "src/Ui/Form.cs"} <= _paths(opted)
    assert "gated" not in opted["summary"]

    # A lookup by id still answers for a gated finding.
    found = await get_dead_code(finding_id="gated0")
    assert found["resolved"] is True
