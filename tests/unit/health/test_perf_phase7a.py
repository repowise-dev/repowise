"""Unit tests for the Phase-7a perf markers.

The four cheap, high-precision loop markers added on top of the ``PerfDialect``
registry: ``resource_construction_in_loop`` (all dialects),
``lock_in_loop`` (Py/Java/Go/C#), ``serial_await_in_loop`` (Py/TS/JS/C#), and
``membership_test_against_list_in_loop`` (Py/TS/JS). Each case exercises the
positive shape AND the precision gate that keeps it from over-firing.

Grammar availability is best-effort: a missing tree-sitter grammar degrades to
"no perf hits" (the registry guarantee), which these exact-match assertions
would surface as an empty list rather than a wrong kind.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.lock_in_loop import LockInLoopDetector
from repowise.core.analysis.health.biomarkers.membership_test_against_list_in_loop import (
    MembershipTestAgainstListInLoopDetector,
)
from repowise.core.analysis.health.biomarkers.registry import detect_all
from repowise.core.analysis.health.biomarkers.resource_construction_in_loop import (
    ResourceConstructionInLoopDetector,
)
from repowise.core.analysis.health.biomarkers.serial_await_in_loop import (
    SerialAwaitInLoopDetector,
)
from repowise.core.analysis.health.complexity import PerfHit, walk_file
from repowise.core.analysis.health.scoring import dimensions_for, score_file


def _hits(lang: str, src: str):
    fc = walk_file(f"t.{lang}", lang, src.encode())
    return sorted((h.kind, h.detail) for h in fc.perf_hits)


# ---------------------------------------------------------------------------
# resource_construction_in_loop — all dialects
# ---------------------------------------------------------------------------

_RESOURCE_CASES = [
    (
        "python",
        "import sqlite3\ndef f(paths):\n    for p in paths:\n        c = sqlite3.connect(p)\n",
        [("resource_construction_in_loop", "")],
        "sqlite3.connect per iteration",
    ),
    (
        "python",
        "import boto3\ndef f(rs):\n    for r in rs:\n        c = boto3.client('s3')\n",
        [("resource_construction_in_loop", "")],
        "boto3.client per iteration",
    ),
    (
        "python",
        "from sqlalchemy import create_engine\n"
        "def f(urls):\n    for u in urls:\n        e = create_engine(u)\n",
        [("resource_construction_in_loop", "")],
        "create_engine resolved by distinctive name",
    ),
    (
        "python",
        "import sqlite3\ndef f(paths):\n    c = sqlite3.connect(':memory:')\n"
        "    for p in paths:\n        use(c, p)\n",
        [],
        "a hoisted connection (outside the loop) does not fire",
    ),
    (
        "typescript",
        "function f(items){ for (const x of items){ const c = new PrismaClient(); } }",
        [("resource_construction_in_loop", "")],
        "new PrismaClient per iteration",
    ),
    (
        "typescript",
        "function f(items){ for (const x of items){ const w = new Worker(); } }",
        [],
        "a non-resource constructor (Worker) does not fire",
    ),
    (
        "go",
        'package m\nimport "database/sql"\n'
        'func f(dsns []string){ for _, d := range dsns { db, _ := sql.Open("pg", d); _ = db } }',
        [("resource_construction_in_loop", "")],
        "sql.Open per range iteration",
    ),
    (
        "java",
        "class A{ void m(java.util.List<String> ids){"
        "for(String id:ids){ RestTemplate r = new RestTemplate(); } } }",
        [("resource_construction_in_loop", "")],
        "new RestTemplate per iteration",
    ),
    (
        "csharp",
        "class A{ void M(System.Collections.Generic.List<int> ids){"
        "foreach(var id in ids){ var c = new HttpClient(); } } }",
        [("resource_construction_in_loop", "")],
        "new HttpClient per iteration (socket exhaustion)",
    ),
]


@pytest.mark.parametrize(
    "lang,src,expected,note", _RESOURCE_CASES, ids=[c[3] for c in _RESOURCE_CASES]
)
def test_resource_construction_cases(lang, src, expected, note):
    assert _hits(lang, src) == sorted(expected), note


# ---------------------------------------------------------------------------
# lock_in_loop — Py/Java/Go/C# (activates the dormant ``lock`` boundary kind)
# ---------------------------------------------------------------------------

_LOCK_CASES = [
    (
        "python",
        "def f(items, lock):\n    for x in items:\n        lock.acquire()\n        use(x)\n",
        [("lock_in_loop", "")],
        "lock.acquire per iteration",
    ),
    (
        "python",
        "def f(items, lock):\n    for x in items:\n        lock.release()\n",
        [],
        "release does not fire (only the acquire side, no double count)",
    ),
    (
        "go",
        "package m\nfunc f(items []int, mu *M){ for _, x := range items { mu.Lock(); use(x) } }",
        [("lock_in_loop", "")],
        "mu.Lock per range iteration",
    ),
    (
        "java",
        "class A{ void m(java.util.List<String> ids, java.util.concurrent.locks.Lock lk){"
        "for(String id:ids){ lk.lock(); } } }",
        [("lock_in_loop", "")],
        "lk.lock() per iteration",
    ),
    (
        "java",
        "class A{ void m(java.util.List<String> ids, Object gate){"
        "for(String id:ids){ synchronized(gate){ use(id); } } } }",
        [("lock_in_loop", "")],
        "synchronized block per iteration",
    ),
    (
        "csharp",
        "class A{ void M(System.Collections.Generic.List<int> ids, object gate){"
        "foreach(var id in ids){ lock(gate){ Use(id); } } } }",
        [("lock_in_loop", "")],
        "lock(gate){} per iteration",
    ),
]


@pytest.mark.parametrize("lang,src,expected,note", _LOCK_CASES, ids=[c[3] for c in _LOCK_CASES])
def test_lock_cases(lang, src, expected, note):
    assert _hits(lang, src) == sorted(expected), note


# ---------------------------------------------------------------------------
# serial_await_in_loop — Py/TS/JS/C# (rides alongside io_in_loop)
# ---------------------------------------------------------------------------

_SERIAL_AWAIT_CASES = [
    (
        "python",
        "import httpx\nasync def f(urls):\n    for u in urls:\n        await httpx.get(u)\n",
        [("io_in_loop", "network"), ("serial_await_in_loop", "network")],
        "awaited httpx.get in a loop -> io_in_loop + serial_await",
    ),
    (
        "typescript",
        "async function f(urls){ for (const u of urls){ await fetch(u); } }",
        [("io_in_loop", "network"), ("serial_await_in_loop", "network")],
        "awaited fetch in a loop",
    ),
    (
        "python",
        "import subprocess\ndef f(paths):\n    for p in paths:\n        subprocess.run(['ls'], cwd=p)\n",
        [("io_in_loop", "subprocess")],
        "a NON-awaited sink does not get the serial_await co-signal",
    ),
]


@pytest.mark.parametrize(
    "lang,src,expected,note", _SERIAL_AWAIT_CASES, ids=[c[3] for c in _SERIAL_AWAIT_CASES]
)
def test_serial_await_cases(lang, src, expected, note):
    assert _hits(lang, src) == sorted(expected), note


# ---------------------------------------------------------------------------
# membership_test_against_list_in_loop — Py/TS/JS (gated on a list-bound name)
# ---------------------------------------------------------------------------

_MEMBERSHIP_CASES = [
    (
        "python",
        "def f(items):\n    big = [1, 2, 3]\n    for x in items:\n        if x in big:\n            use(x)\n",
        [("membership_test_against_list_in_loop", "")],
        "x in <list literal> in a loop",
    ),
    (
        "python",
        "def f(items):\n    big = [i for i in range(9)]\n    for x in items:\n        y = x not in big\n",
        [("membership_test_against_list_in_loop", "")],
        "x not in <list comprehension> also fires",
    ),
    (
        "python",
        "def f(items):\n    big = set()\n    for x in items:\n        if x in big:\n            use(x)\n",
        [],
        "membership against a set is O(1) -> not flagged",
    ),
    (
        "python",
        "def f(items, big):\n    for x in items:\n        if x in big:\n            use(x)\n",
        [],
        "an opaque (unproven) name is not flagged",
    ),
    (
        "python",
        # ``seen`` is a list in g() but a set in f(); the file-level gate must
        # treat the collided name as ambiguous and NOT fire (real-corpus FP).
        "def g(xs):\n    seen = []\n    for x in xs:\n        seen.append(x)\n"
        "def f(items):\n    seen = set()\n    for x in items:\n        if x in seen:\n            use(x)\n",
        [],
        "a name bound to a set in any scope is excluded (list/set collision)",
    ),
    (
        "typescript",
        "function f(items){ const big = [1,2,3]; "
        "for (const x of items){ if (big.includes(x)) use(x); } }",
        [("membership_test_against_list_in_loop", "")],
        "big.includes(x) where big is an array literal",
    ),
    (
        "typescript",
        "function f(items){ const big = new Set(); "
        "for (const x of items){ if (big.includes(x)) use(x); } }",
        [],
        "includes on a non-array-bound name is not flagged",
    ),
]


@pytest.mark.parametrize(
    "lang,src,expected,note", _MEMBERSHIP_CASES, ids=[c[3] for c in _MEMBERSHIP_CASES]
)
def test_membership_cases(lang, src, expected, note):
    assert _hits(lang, src) == sorted(expected), note


# ---------------------------------------------------------------------------
# Biomarkers lift hits; scoring keeps them performance-only
# ---------------------------------------------------------------------------

_NEW_KINDS = [
    "resource_construction_in_loop",
    "lock_in_loop",
    "serial_await_in_loop",
    "membership_test_against_list_in_loop",
]


def _ctx(perf_hits: list[PerfHit]) -> FileContext:
    return FileContext(
        file_path="x.py",
        language="python",
        nloc=10,
        has_test_file=False,
        module="x",
        perf_hits=perf_hits,
    )


@pytest.mark.parametrize("kind", _NEW_KINDS)
def test_each_marker_is_performance_only(kind):
    """Every Phase-7a marker maps to performance ONLY (defect golden intact)."""
    assert dimensions_for(kind) == {"performance"}


def test_detectors_only_consume_their_own_kind():
    ctx = _ctx(
        [
            PerfHit("resource_construction_in_loop", 1, "f", ""),
            PerfHit("lock_in_loop", 2, "f", ""),
            PerfHit("serial_await_in_loop", 3, "f", "network"),
            PerfHit("membership_test_against_list_in_loop", 4, "f", ""),
        ]
    )
    assert len(ResourceConstructionInLoopDetector().detect(ctx)) == 1
    assert len(LockInLoopDetector().detect(ctx)) == 1
    assert len(SerialAwaitInLoopDetector().detect(ctx)) == 1
    assert len(MembershipTestAgainstListInLoopDetector().detect(ctx)) == 1


def test_new_markers_score_performance_not_defect():
    ctx = _ctx([PerfHit(k, i + 1, "f", "") for i, k in enumerate(_NEW_KINDS)])
    results = detect_all(ctx)
    got = {r.biomarker_type for r in results}
    assert set(_NEW_KINDS) <= got
    scores, deductions = score_file(results)
    assert scores["defect"] == 10.0
    assert scores["maintainability"] == 10.0
    assert scores["performance"] < 10.0
    assert all(d == 0.0 for d in deductions)
