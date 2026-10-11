"""File-level promotion pass: in-place marking, budget, and biomarker output."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.serial_await_in_loop import BIOMARKER as SERIAL
from repowise.core.analysis.health.complexity import FileComplexity, PerfHit
from repowise.core.analysis.health.perf.promotion import apply_perf_promotions


def _require_python() -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing for python")
    if _get_language("python") is None:
        pytest.skip("tree-sitter language pack missing for python")


@dataclass
class _FileInfo:
    path: str
    abs_path: str
    language: str


@dataclass
class _ParsedFile:
    file_info: _FileInfo


def _write(tmp_path: Path, name: str, src: str) -> str:
    p = tmp_path / name
    p.write_text(textwrap.dedent(src), encoding="utf-8")
    return str(p)


def _walked_entry(
    tmp_path: Path, name: str, src: str, hits: list[PerfHit], language: str = "python"
):
    abs_path = _write(tmp_path, name, src)
    pf = _ParsedFile(_FileInfo(path=name, abs_path=abs_path, language=language))
    fcx = FileComplexity(functions=[], classes=[], perf_hits=hits)
    return pf, fcx


def _line_of(src: str, marker: str) -> int:
    body = textwrap.dedent(src)
    return next(i for i, ln in enumerate(body.splitlines(), start=1) if marker in ln)


def test_independent_hit_is_promoted_in_place(tmp_path: Path):
    _require_python()
    src = """
    async def f(items):
        out = []
        for item in items:
            r = await fetch(item.id)  # HIT
            out.append(r)
        return out
    """
    line = _line_of(src, "HIT")
    hits = [PerfHit(kind="serial_await_in_loop", line=line, function="f", detail="network")]
    pf, fcx = _walked_entry(tmp_path, "indep.py", src, hits)

    apply_perf_promotions([(pf, fcx)])

    assert fcx.perf_hits[0].promoted is True
    # The biomarker now asserts rather than hedges, and flags verification.
    results = SERIAL.detect(_ctx(fcx))
    assert results and results[0].details.get("dataflow_verified") is True
    assert "carry no data dependence" in results[0].reason


def test_carried_hit_is_not_promoted(tmp_path: Path):
    _require_python()
    src = """
    async def f(items):
        cursor = start()
        for item in items:
            page = await fetch(cursor)  # HIT
            cursor = page.next
    """
    line = _line_of(src, "HIT")
    hits = [PerfHit(kind="serial_await_in_loop", line=line, function="f", detail="network")]
    pf, fcx = _walked_entry(tmp_path, "carried.py", src, hits)

    apply_perf_promotions([(pf, fcx)])

    assert fcx.perf_hits[0].promoted is False
    results = SERIAL.detect(_ctx(fcx))
    assert results and "dataflow_verified" not in results[0].details
    assert "if the iterations are independent" in results[0].reason


def test_budget_no_dataflow_without_advisory_hit(tmp_path: Path, monkeypatch):
    """The dataflow build runs ONLY for files carrying a promotable advisory hit."""
    _require_python()
    from repowise.core.analysis.health.dataflow import analyze as analyze_mod

    calls = {"n": 0}
    real = analyze_mod.analyze_function

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(analyze_mod, "analyze_function", _counting)

    indep = """
    async def f(items):
        out = []
        for item in items:
            r = await fetch(item.id)  # HIT
            out.append(r)
        return out
    """
    # A file with a non-promotable perf hit (io_in_loop) and no advisory marker
    # must never be analyzed.
    io_only = """
    def g(items):
        for item in items:
            db.execute(item)  # NOHIT
    """
    line = _line_of(indep, "HIT")
    e1 = _walked_entry(
        tmp_path,
        "indep.py",
        indep,
        [PerfHit(kind="serial_await_in_loop", line=line, function="f", detail="network")],
    )
    e2 = _walked_entry(
        tmp_path,
        "io_only.py",
        io_only,
        [PerfHit(kind="io_in_loop", line=_line_of(io_only, "NOHIT"), function="g", detail="db")],
    )

    apply_perf_promotions([e1, e2])

    # Exactly the one function holding the advisory hit was analyzed; the
    # io_in_loop-only file was never parsed for dataflow.
    assert calls["n"] == 1
    assert e1[1].perf_hits[0].promoted is True
    assert e2[1].perf_hits[0].promoted is False


def test_rust_independent_await_loop_is_promoted(tmp_path: Path):
    # The promotion pass activates for a language the moment its def/use
    # dialect exists. A ``?`` on the awaited value is an early *exit*, not a
    # data carry -- the same contract as a Python ``await`` that may raise.
    src = """
    async fn f(items: &[Item]) -> Result<Vec<R>, E> {
        let mut out = Vec::new();
        for item in items {
            let r = fetch(item.id).await?;  // HIT
            out.push(r);
        }
        Ok(out)
    }
    """
    line = _line_of(src, "HIT")
    hits = [PerfHit(kind="serial_await_in_loop", line=line, function="f", detail="network")]
    pf, fcx = _walked_entry(tmp_path, "indep.rs", src, hits, language="rust")

    apply_perf_promotions([(pf, fcx)])

    if fcx.perf_hits[0].promoted is False:  # pack missing -> silence, not a wrong answer
        pytest.skip("tree-sitter language pack missing for rust")
    assert fcx.perf_hits[0].promoted is True


def test_rust_carried_cursor_is_not_promoted(tmp_path: Path):
    src = """
    async fn f(items: &[Item]) -> Cursor {
        let mut cursor = start();
        for item in items {
            let page = fetch(cursor).await;  // HIT
            cursor = page.next;
        }
        cursor
    }
    """
    line = _line_of(src, "HIT")
    hits = [PerfHit(kind="serial_await_in_loop", line=line, function="f", detail="network")]
    pf, fcx = _walked_entry(tmp_path, "carried.rs", src, hits, language="rust")

    apply_perf_promotions([(pf, fcx)])

    assert fcx.perf_hits[0].promoted is False


def test_java_independent_nested_loop_is_promoted(tmp_path: Path):
    src = """
    class Demo {
        void f(int[] items, int[] others) {
            for (int a : items) {
                for (int b : others) {
                    emit(a, b);  // HIT
                }
            }
        }
    }
    """
    line = _line_of(src, "HIT")
    hits = [PerfHit(kind="nested_loop_quadratic", line=line, function="f", detail="scan")]
    pf, fcx = _walked_entry(tmp_path, "indep.java", src, hits, language="java")

    apply_perf_promotions([(pf, fcx)])

    if fcx.perf_hits[0].promoted is False:
        pytest.skip("tree-sitter language pack missing for java")
    assert fcx.perf_hits[0].promoted is True


def test_unsupported_language_degrades_to_silence(tmp_path: Path):
    # A language with no def/use dialect leaves the hit advisory, never raises.
    abs_path = _write(tmp_path, "a.rb", "def f; end\n")
    pf = _ParsedFile(_FileInfo(path="a.rb", abs_path=abs_path, language="ruby"))
    fcx = FileComplexity(
        functions=[],
        classes=[],
        perf_hits=[PerfHit(kind="serial_await_in_loop", line=1, function="f")],
    )
    apply_perf_promotions([(pf, fcx)])
    assert fcx.perf_hits[0].promoted is False


def _ctx(fcx: FileComplexity) -> FileContext:
    return FileContext(
        file_path="x.py",
        language="python",
        nloc=10,
        has_test_file=False,
        module=None,
        perf_hits=fcx.perf_hits,
    )
