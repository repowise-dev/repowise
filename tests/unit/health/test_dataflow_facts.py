"""The shared per-file dataflow service and the DataflowFacts derivation."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

from repowise.core.analysis.health.complexity import FileComplexity, PerfHit
from repowise.core.analysis.health.dataflow import (
    FileDataflow,
    FileDataflowCache,
    derive_facts,
    function_analysis_at,
)
from repowise.core.analysis.health.perf.promotion import apply_perf_promotions


def _require_python() -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing for python")
    if _get_language("python") is None:
        pytest.skip("tree-sitter language pack missing for python")


def _facts(src: str, start_line: int, name: str | None = None, path: str = "x.py"):
    _require_python()
    source = textwrap.dedent(src).encode()
    analysis = function_analysis_at(path, "python", source, start_line, name)
    assert analysis is not None
    return derive_facts(analysis)


# ---------------------------------------------------------------------------
# DataflowFacts derivation
# ---------------------------------------------------------------------------


def test_dead_store_overwritten_before_use():
    facts = _facts(
        """
        def f(x):
            y = first(x)
            y = second(x)
            return y
        """,
        2,
    )
    assert [(d.var, d.line) for d in facts.dead_stores] == [("y", 3)]
    assert "y" in facts.writes
    assert "y" in facts.flows_out


def test_unreachable_lines_after_return():
    facts = _facts(
        """
        def f(x):
            if x:
                return 1
            return 2
            cleanup()
        """,
        2,
    )
    assert 6 in facts.unreachable_lines


def test_params_read_and_unused():
    facts = _facts(
        """
        def f(a, b):
            return a + 1
        """,
        2,
    )
    assert facts.params_read == ("a",)
    assert facts.params_unused == ("b",)


def test_tuple_unpack_partial_use_marks_unused_element_dead():
    facts = _facts(
        """
        def f(pair):
            a, b = pair
            return a
        """,
        2,
    )
    dead_vars = {d.var for d in facts.dead_stores}
    assert "b" in dead_vars
    assert "a" not in dead_vars


def test_reads_are_free_names_only():
    facts = _facts(
        """
        def f(x):
            y = helper(x, CONSTANT)
            return y
        """,
        2,
    )
    assert "helper" in facts.reads or "CONSTANT" in facts.reads
    assert "x" not in facts.reads  # a parameter, not a free name
    assert "y" not in facts.reads  # a local, not a free name


def test_self_referencing_assignment_keeps_prior_def_alive():
    # ``y = y + 1`` reads the PREVIOUS def; neither write is a dead store here.
    facts = _facts(
        """
        def f(x):
            y = x
            y = y + 1
            return y
        """,
        2,
    )
    assert facts.dead_stores == ()


def test_facts_are_deterministic_across_instances():
    src = textwrap.dedent(
        """
        def f(a, b):
            total = 0
            unused = a
            for item in b:
                total = total + item
            return total
        """
    ).encode()
    _require_python()
    one = derive_facts(FileDataflow("x.py", "python", src).analysis_at(2))
    two = derive_facts(FileDataflow("x.py", "python", src).analysis_at(2))
    assert one == two


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def test_lookup_miss_returns_none():
    _require_python()
    source = b"def f(x):\n    return x\n"
    assert function_analysis_at("x.py", "python", source, 99) is None


def test_lookup_falls_back_to_unique_name_match():
    _require_python()
    source = b"\n\ndef target(x):\n    return x\n"
    analysis = function_analysis_at("x.py", "python", source, 1, name="target")
    assert analysis is not None
    assert analysis.name == "target"
    assert analysis.start_line == 3


def test_lookup_unsupported_language_is_silent():
    assert function_analysis_at("x.zig", "zig", b"fn f() {}", 1) is None


# ---------------------------------------------------------------------------
# The shared cache: one parse per file across both consumers
# ---------------------------------------------------------------------------


@dataclass
class _FileInfo:
    path: str
    abs_path: str
    language: str


@dataclass
class _ParsedFile:
    file_info: _FileInfo


def test_one_parse_when_promotion_and_extract_method_hit_same_file(tmp_path: Path, monkeypatch):
    _require_python()
    from repowise.core.analysis.health.dataflow import facts as facts_mod

    # A function that is BOTH structurally flagged (ccn >= 9) and carries a
    # promotable advisory hit, so the two consumer gates fire on one file.
    branches = "\n".join(f"        if r == {i}:\n            out.append({i})" for i in range(9))
    src = (
        "async def f(items):\n"
        "    out = []\n"
        "    for item in items:\n"
        "        r = await fetch(item.id)  # HIT\n"
        f"{branches}\n"
        "    return out\n"
    )
    p = tmp_path / "both.py"
    p.write_text(src, encoding="utf-8")
    hit_line = next(i for i, ln in enumerate(src.splitlines(), start=1) if "HIT" in ln)

    calls = {"n": 0}
    real = facts_mod.parse_source

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(facts_mod, "parse_source", _counting)

    cache = FileDataflowCache()
    pf = _ParsedFile(_FileInfo(path="both.py", abs_path=str(p), language="python"))
    fcx = FileComplexity(
        functions=[],
        classes=[],
        perf_hits=[PerfHit(kind="serial_await_in_loop", line=hit_line, function="f")],
    )

    # Consumer 1: the promotion pass.
    apply_perf_promotions([(pf, fcx)], dataflow=cache)
    assert fcx.perf_hits[0].promoted is True

    # Consumer 2: the Extract Method view, through the same cache.
    flagged = cache.get(str(p), "python").flagged_analyses()
    assert flagged and flagged[0].name == "f"
    assert flagged[0].ccn >= 9

    assert calls["n"] == 1


def test_cache_construction_does_not_touch_the_file(tmp_path: Path):
    fd = FileDataflow(str(tmp_path / "missing.py"), "python")
    # Nothing read or parsed yet; a consumer call on a missing file is silent.
    assert fd.flagged_analyses() == []
    assert fd.analyses_covering({1}) == []
