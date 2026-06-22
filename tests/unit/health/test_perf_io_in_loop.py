"""Unit tests for the performance pass + perf biomarkers (PR3).

Covers the walker's ``_collect_perf_hits`` (loop-body scoping, constant-loop
skip, execution-sink gating, dependency classification) and the three perf
biomarkers that lift its hits into findings. Like the other walker tests,
tree-sitter grammar availability is best-effort: a missing grammar skips.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.blocking_sync_in_async import (
    BlockingSyncInAsyncDetector,
)
from repowise.core.analysis.health.biomarkers.io_in_loop import IoInLoopDetector
from repowise.core.analysis.health.biomarkers.registry import detect_all
from repowise.core.analysis.health.biomarkers.string_concat_in_loop import (
    StringConcatInLoopDetector,
)
from repowise.core.analysis.health.complexity import PerfHit, walk_file
from repowise.core.analysis.health.scoring import score_file

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples"


def _walk(rel: str, lang: str):
    path = _FIXTURE_DIR / rel
    fc = walk_file(str(path), lang, path.read_bytes())
    return fc


def _kinds(perf_hits) -> Counter:
    return Counter(h.kind for h in perf_hits)


# ---------------------------------------------------------------------------
# Walker pass — inline micro-fixtures (precise, line-stable)
# ---------------------------------------------------------------------------

# (language, source, expected (kind, detail) multiset, note)
_CASES = [
    (
        "python",
        b"def f(session, repos):\n"
        b"    from sqlalchemy import select\n"
        b"    for r in repos:\n"
        b"        session.execute(select(r))\n",
        [("io_in_loop", "db")],
        "db execute in a data-dependent loop",
    ),
    (
        "python",
        b"import subprocess\n"
        b"def f(paths):\n"
        b"    for p in paths:\n"
        b"        subprocess.run(['ls'], cwd=p)\n",
        [("io_in_loop", "subprocess")],
        "subprocess spawn per iteration",
    ),
    (
        "python",
        b"async def f(session, q):\n"
        b"    for row in session.execute(q).scalars().all():\n"
        b"        use(row)\n",
        [],
        "sink in the for-header runs once (loop-body scoping)",
    ),
    (
        "python",
        b"import subprocess\n"
        b"def f():\n"
        b"    for _ in range(3):\n"
        b"        subprocess.run(['ls'])\n",
        [],
        "constant-bounded range loop is skipped",
    ),
    (
        "python",
        b"import subprocess\n"
        b"ENDPOINTS = load()\n"
        b"def f():\n"
        b"    for e in ENDPOINTS:\n"
        b"        subprocess.run(e)\n",
        [],
        "ALL_CAPS named-constant collection is skipped",
    ),
    (
        "python",
        b"from sqlalchemy import select\n"
        b"def f(repos):\n"
        b"    for r in repos:\n"
        b"        select(r).where(r.id == 1)\n",
        [],
        "query builder (not executed) is not a sink",
    ),
    (
        "python",
        b"def f(items):\n"
        b"    total = 0\n"
        b"    for x in items:\n"
        b"        total += compute(x)\n"
        b"    return total\n",
        [],
        "numeric += / pure call in a loop is clean",
    ),
    (
        "python",
        b"import git\n"
        b"def f(repos):\n"
        b"    for r in repos:\n"
        b"        r.commit()\n",
        [],
        "git repo.commit() without a db import is gated out",
    ),
    (
        "python",
        b"def f(rows):\n"
        b"    s = ''\n"
        b"    for r in rows:\n"
        b"        s += 'x'\n"
        b"    return s\n",
        [("string_concat_in_loop", "")],
        "string += in a loop",
    ),
    (
        "python",
        b"import time\n"
        b"async def f():\n"
        b"    time.sleep(1)\n",
        [("blocking_sync_in_async", "time.sleep")],
        "blocking sleep in async",
    ),
    (
        "typescript",
        b"async function f(urls) {\n  for (const u of urls) {\n    await fetch(u);\n  }\n}\n",
        # An awaited sink in a loop is BOTH an N+1 (io_in_loop) and a missed-
        # concurrency candidate (serial_await_in_loop, advisory co-signal).
        [("io_in_loop", "network"), ("serial_await_in_loop", "network")],
        "fetch in a loop (awaited -> also serial_await)",
    ),
    (
        "typescript",
        b"function f(m, keys) {\n  for (const k of keys) {\n    m.delete(k);\n  }\n}\n",
        [],
        "Map.delete is not a db sink",
    ),
    (
        "typescript",
        b"import fs from 'fs';\n"
        b'function f() {\n  for (const p of ["/proc/net/tcp", "/proc/net/tcp6"]) {\n'
        b'    fs.readFileSync(p, "utf8");\n  }\n}\n',
        [],
        "for-of over an inline array literal is constant-bounded -> skipped",
    ),
    (
        "typescript",
        b"import fs from 'fs';\nconst NAMES = ['DREAMS.md', 'dreams.md'];\n"
        b"function f() {\n  for (const n of NAMES) {\n    fs.readFileSync(n);\n  }\n}\n",
        [],
        "for-of over an ALL_CAPS named constant is constant-bounded -> skipped",
    ),
    (
        "typescript",
        b"import fs from 'fs';\n"
        b'function f(paths) {\n  for (const p of paths) {\n    fs.readFileSync(p, "utf8");\n  }\n}\n',
        [("io_in_loop", "filesystem")],
        "for-of over a data-dependent variable still fires (contrast to constant)",
    ),
    (
        "typescript",
        b"import axios from 'axios';\n"
        b"function f(items) {\n"
        b"  for (const u of items) {\n"
        b"    if (axios.isCancel(u)) continue;\n"
        b"    axios.create();\n"
        b"  }\n"
        b"}\n",
        [],
        "sync helpers on an imported I/O pkg (isCancel/create) are not sinks",
    ),
]


@pytest.mark.parametrize("lang,source,expected,note", _CASES, ids=[c[3] for c in _CASES])
def test_perf_pass_cases(lang, source, expected, note):
    fc = walk_file(f"t.{lang[:2]}", lang, source)
    got = sorted((h.kind, h.detail) for h in fc.perf_hits)
    assert got == sorted(expected), note


# ---------------------------------------------------------------------------
# Fixture files (the plan's tests/fixtures/lang_samples/*/perf_*)
# ---------------------------------------------------------------------------


def test_python_fixture_counts():
    fc = _walk("python/perf_io_in_loop.py", "python")
    counts = _kinds(fc.perf_hits)
    assert counts["io_in_loop"] == 4  # db, subprocess, network, filesystem
    assert counts["string_concat_in_loop"] == 1
    assert counts["blocking_sync_in_async"] == 2
    boundaries = {h.detail for h in fc.perf_hits if h.kind == "io_in_loop"}
    assert boundaries == {"db", "subprocess", "network", "filesystem"}
    # The import bridge resolved the I/O libraries.
    assert {"requests", "httpx", "subprocess"} <= set(fc.io_boundary_names)


def test_typescript_fixture_counts():
    fc = _walk("typescript/perf_io_in_loop.ts", "typescript")
    counts = _kinds(fc.perf_hits)
    assert counts["io_in_loop"] == 3  # fetch, axios, prisma
    assert {h.detail for h in fc.perf_hits} == {"network", "db"}


# ---------------------------------------------------------------------------
# Biomarkers — lift PerfHits into findings
# ---------------------------------------------------------------------------


def _ctx(perf_hits: list[PerfHit]) -> FileContext:
    return FileContext(
        file_path="x.py",
        language="python",
        nloc=10,
        has_test_file=False,
        module="x",
        perf_hits=perf_hits,
    )


def test_io_in_loop_detector_lifts_hits():
    ctx = _ctx([PerfHit("io_in_loop", 12, "f", "db")])
    findings = IoInLoopDetector().detect(ctx)
    assert len(findings) == 1
    f = findings[0]
    assert f.biomarker_type == "io_in_loop"
    assert f.line_start == 12
    assert f.details["boundary_kind"] == "db"


def test_detectors_only_consume_their_own_kind():
    ctx = _ctx(
        [
            PerfHit("io_in_loop", 1, "f", "db"),
            PerfHit("string_concat_in_loop", 2, "f", ""),
            PerfHit("blocking_sync_in_async", 3, "f", "time.sleep"),
        ]
    )
    assert len(IoInLoopDetector().detect(ctx)) == 1
    assert len(StringConcatInLoopDetector().detect(ctx)) == 1
    assert len(BlockingSyncInAsyncDetector().detect(ctx)) == 1


def test_no_perf_hits_yields_no_findings():
    assert IoInLoopDetector().detect(_ctx([])) == []


# ---------------------------------------------------------------------------
# End-to-end: detect_all + score_file populate the performance dimension only
# ---------------------------------------------------------------------------


def test_perf_findings_score_performance_not_defect():
    ctx = _ctx([PerfHit("io_in_loop", 5, "f", "db") for _ in range(2)])
    results = detect_all(ctx)
    perf = [r for r in results if r.biomarker_type == "io_in_loop"]
    assert len(perf) == 2
    scores, deductions = score_file(results)
    assert scores["defect"] == 10.0  # perf never touches the surfaced score
    assert scores["performance"] < 10.0
    # Every perf finding carries 0 defect-pillar impact.
    assert all(d == 0.0 for d in deductions)
