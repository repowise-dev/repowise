from __future__ import annotations

import pytest

from repowise.core.analysis.module_health import ModuleAccumulator, aggregate_modules, summarize


def _file(path: str, *, owner: str, bus_factor: int = 1) -> dict:
    return {
        "file_path": path,
        "primary_owner_name": owner,
        "primary_owner_email": f"{owner.lower()}@example.com",
        "is_hotspot": False,
        "bus_factor": bus_factor,
        "churn_percentile": 0.0,
        "top_authors_json": "[]",
    }


def _dead(path: str, *, lines: int = 1) -> dict:
    # The production service currently selects only these columns.
    return {"file_path": path, "lines": lines}


def _module(files: list[dict], symbols: list[dict], dead: list[dict]) -> dict:
    (acc,) = aggregate_modules(files, symbols, dead, []).values()
    return summarize(acc)


def test_finding_density_uses_symbol_count_and_preserves_other_score_terms() -> None:
    files = [
        _file("src/services/order.py", owner="Alice"),
        _file("src/services/payment.py", owner="Bob"),
    ]
    symbols = [
        {"file_path": files[i % 2]["file_path"], "docstring": "Documented" if i < 30 else ""}
        for i in range(60)
    ]
    dead = [_dead(files[i % 2]["file_path"]) for i in range(8)]

    baseline = _module(files, symbols, [])
    summary = _module(files, symbols, dead)

    assert summary["dead_code_count"] == 8
    assert summary["dead_code_lines"] == 8
    assert summary["doc_coverage_pct"] == 50.0
    assert summary["is_silo"] is False
    assert baseline["health_score"] == 97.5
    # 100 - 25*(8/60) + 15*0.5 - 10 (single-maintainer bus factor)
    assert summary["health_score"] == pytest.approx(94.16666666666667)


def test_finding_density_is_bounded_when_findings_exceed_symbols() -> None:
    files = [
        _file("src/a.py", owner="Alice", bus_factor=2),
        _file("src/b.py", owner="Bob", bus_factor=2),
    ]
    symbols = [{"file_path": file["file_path"], "docstring": ""} for file in files]
    dead = [_dead(files[i % 2]["file_path"]) for i in range(8)]

    summary = _module(files, symbols, dead)

    assert summary["symbol_count"] == 2
    assert summary["dead_code_count"] == 8
    # The 8/2 proxy saturates at 1: 100 - 25*1 + 10 (bus factor >= 2).
    assert summary["health_score"] == 85.0


def test_empty_module_and_zero_symbols_do_not_create_a_dead_code_penalty() -> None:
    empty = summarize(ModuleAccumulator(module_path="src"))
    assert empty["health_score"] == 90.0

    files = [_file("src/empty.py", owner="Alice")]
    without_findings = _module(files, [], [])
    with_finding = _module(files, [], [_dead("src/empty.py")])

    assert with_finding["symbol_count"] == 0
    assert with_finding["dead_code_count"] == 1
    assert with_finding["dead_code_lines"] == 1
    assert with_finding["health_score"] == without_findings["health_score"] == 65.0


def test_finding_density_keeps_the_existing_single_owner_penalty() -> None:
    files = [
        _file("src/a.py", owner="Alice"),
        _file("src/b.py", owner="Alice"),
    ]
    symbols = [
        {"file_path": files[i % 2]["file_path"], "docstring": "Documented" if i < 30 else ""}
        for i in range(60)
    ]
    dead = [_dead(files[i % 2]["file_path"]) for i in range(8)]

    baseline = _module(files, symbols, [])
    summary = _module(files, symbols, dead)

    assert summary["is_silo"] is True
    assert baseline["health_score"] == 72.5
    assert summary["health_score"] == pytest.approx(69.16666666666667)
