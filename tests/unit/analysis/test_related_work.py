"""``related_work``: rows from every lens grouped under the files asked about."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.analysis.health.fix_first.model import FixTarget
from repowise.core.analysis.related_work import LENSES, related_work


def _finding(fid: str, path: str, severity: str, line: int | None, **details) -> dict:
    return {
        "id": fid,
        "file_path": path,
        "biomarker_type": "complex_method",
        "severity": severity,
        "function_name": "run",
        "line_start": line,
        "details": details,
    }


def test_groups_by_file_in_request_order_and_drops_other_files() -> None:
    out = related_work(
        ["b.py", "a.py"],
        findings=[_finding("f1", "a.py", "high", 3), _finding("f2", "elsewhere.py", "high", 1)],
        dead_code=[{"id": "d1", "file_path": "b.py", "kind": "unused_export",
                    "symbol_name": "x", "start_line": 9, "confidence": 0.9,
                    "safe_to_delete": True}],
    ).as_dict()
    assert [f["file_path"] for f in out["files"]] == ["b.py", "a.py"]
    b, a = out["files"]
    assert list(b["lenses"]) == ["dead_code"]
    assert b["lenses"]["dead_code"]["items"][0] == {
        "lens": "dead_code", "id": "d1", "kind": "unused_export", "title": None,
        "symbol": "x", "severity": None, "tier": "safe_to_delete", "rank": None, "line": 9,
    }
    assert [i["id"] for i in a["lenses"]["findings"]["items"]] == ["f1"]


def test_findings_order_by_severity_then_line_and_cap_keeps_the_total() -> None:
    rows = [
        _finding("low", "a.py", "low", 1),
        _finding("high_late", "a.py", "high", 50),
        _finding("crit", "a.py", "critical", 99),
        _finding("high_early", "a.py", "high", 5),
    ]
    lens = related_work(["a.py"], findings=rows, per_lens_limit=3).files[0].lenses["findings"]
    assert [i.id for i in lens.items] == ["crit", "high_early", "high_late"]
    assert lens.total == 4


def test_flags_are_copied_only_when_the_row_carries_them() -> None:
    out = related_work(
        ["a.py"],
        findings=[_finding("plain", "a.py", "high", 1),
                  _finding("old", "a.py", "high", 2, deprecated=True)],
    ).as_dict()
    plain, old = out["files"][0]["lenses"]["findings"]["items"]
    assert "deprecated" not in plain and "code_origin" not in plain
    assert old["deprecated"] is True


def test_reads_orm_shaped_rows_and_fix_first_items() -> None:
    refactoring = SimpleNamespace(
        opportunity_id="refop_a", file_path="a.py", lead_refactoring_type="extract_method",
        effort_bucket="M", rank_position=2, details_json="{}",
    )
    perf = [
        SimpleNamespace(opportunity_id=f"perf_{r}", file_path="a.py", biomarker_type="io_in_loop",
                        intervention_symbol="a.py::load", actionability_state="fix",
                        rank_position=r, details_json="{}")
        for r in (7, 0)
    ]
    item = SimpleNamespace(id="fix1_x", kind="refactor", title="Split run", tier="now", rank=4,
                           target=FixTarget(file_path="a.py", symbol="run", line_start=10))
    lenses = related_work(
        ["a.py"], refactoring=[refactoring], performance=perf, fix_first=[item]
    ).files[0].lenses
    assert list(lenses) == ["fix_first", "refactoring", "performance"]
    assert [i.id for i in lenses["performance"].items] == ["perf_0", "perf_7"]
    fix = lenses["fix_first"].items[0]
    assert (fix.title, fix.rank, fix.tier, fix.line, fix.symbol) == ("Split run", 4, "now", 10, "run")
    assert lenses["refactoring"].items[0].kind == "extract_method"


def test_nothing_in_nothing_out() -> None:
    out = related_work(["a.py"]).as_dict()
    assert out == {"files": [{"file_path": "a.py", "lenses": {}}], "per_lens_limit": 5}
    assert LENSES == ("findings", "fix_first", "refactoring", "performance", "dead_code")
