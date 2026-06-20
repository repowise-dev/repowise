"""Unit tests for the Phase-6 perf dialects (Java / Go / C#).

Mirrors ``test_perf_io_in_loop.py`` (Python/TS) but for the three dialects added
on top of the ``PerfDialect`` registry: each exercises the per-grammar callee
arm, the execution-sink lexicon + its evidence gates, the new markers
(``regex_compile_in_loop`` for Java/Go, ``defer_in_loop`` for Go,
sync-over-async ``blocking_sync_in_async`` for C#), and the precision hazards
the plan flags (ambiguous ``find``/``get``/``Find`` collisions, in-memory LINQ).

Grammar availability is best-effort: a missing tree-sitter grammar skips the
case (the walker degrades to "no perf hits", which the registry guarantees).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from repowise.core.analysis.health.complexity import walk_file

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples"


def _walk(rel: str, lang: str):
    path = _FIXTURE_DIR / rel
    return walk_file(str(path), lang, path.read_bytes())


def _kinds(perf_hits) -> Counter:
    return Counter(h.kind for h in perf_hits)


def _hits(lang: str, src: str):
    fc = walk_file(f"t.{lang}", lang, src.encode())
    return sorted((h.kind, h.detail) for h in fc.perf_hits)


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------


def test_java_fixture_counts():
    fc = _walk("java/PerfIoInLoop.java", "java")
    counts = _kinds(fc.perf_hits)
    # findByName + executeQuery (db) · Files.readString + new FileInputStream (fs)
    assert counts["io_in_loop"] == 4
    assert {h.detail for h in fc.perf_hits if h.kind == "io_in_loop"} == {"db", "filesystem"}
    assert counts["regex_compile_in_loop"] == 1
    assert counts["string_concat_in_loop"] == 1
    # The hoisted ``Pattern.compile`` and the ``matches()`` / ``length()`` calls
    # do not fire; the out-of-loop ``executeQuery`` does not fire.
    assert counts["blocking_sync_in_async"] == 0


_JAVA_CASES = [
    (
        "class A{void m(java.util.List<String> ids){"
        "for(String id:ids){ this.repo.findById(id); }}}",
        [("io_in_loop", "db")],
        "Spring-Data findById derived query (no import needed)",
    ),
    (
        "import java.util.Map;\n"
        "class A{void m(java.util.List<String> ks, Map<String,String> cache){"
        "for(String k:ks){ cache.get(k); }}}",
        [],
        "ambiguous get() with NO db import is gated out",
    ),
    (
        "import org.springframework.data.jpa.repository.JpaRepository;\n"
        "class A{void m(java.util.List<String> ks, Repo cache){"
        "for(String k:ks){ cache.get(k); }}}",
        [("io_in_loop", "db")],
        "ambiguous get() WITH a db import passes the file-level gate",
    ),
    (
        "class A{void m(java.util.List<String> ids){for(String id:ids){ helper(id); }}}",
        [],
        "a plain helper call in a loop is not a sink",
    ),
]


@pytest.mark.parametrize("src,expected,note", _JAVA_CASES, ids=[c[2] for c in _JAVA_CASES])
def test_java_cases(src, expected, note):
    assert _hits("java", src) == sorted(expected), note


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


def test_go_fixture_counts():
    fc = _walk("go/perf_io_in_loop.go", "go")
    counts = _kinds(fc.perf_hits)
    # db (db.Query) · network (http.Get) · filesystem (os.Open x2, incl. the
    # one in deferInLoop). The constant-bound and out-of-loop queries do not.
    assert counts["io_in_loop"] == 4
    assert {h.detail for h in fc.perf_hits if h.kind == "io_in_loop"} == {
        "db",
        "network",
        "filesystem",
    }
    assert counts["defer_in_loop"] == 1
    assert counts["regex_compile_in_loop"] == 1
    assert counts["string_concat_in_loop"] == 1


_GO_CASES = [
    (
        'package m\nimport "gorm.io/gorm"\n'
        "func f(db *gorm.DB, ids []int){ for _, id := range ids { db.Find(id) } }",
        [("io_in_loop", "db")],
        "GORM Find with a gorm import fires",
    ),
    (
        "package m\nfunc f(repo Repo, ids []int){ for _, id := range ids { repo.Find(id) } }",
        [],
        "ambiguous Find with NO db import is gated out",
    ),
    (
        'package m\nimport "database/sql"\nfunc f(db *sql.DB){ for {} ; db.Query("x") }',
        [],
        "clause-less for{} is a real loop but the query is outside it",
    ),
    (
        'package m\nimport "net/http"\n'
        "func f(urls []string){ for _, u := range urls { http.Get(u) } }",
        [("io_in_loop", "network")],
        "net/http Get in a range loop",
    ),
]


@pytest.mark.parametrize("src,expected,note", _GO_CASES, ids=[c[2] for c in _GO_CASES])
def test_go_cases(src, expected, note):
    assert _hits("go", src) == sorted(expected), note


# ---------------------------------------------------------------------------
# C#
# ---------------------------------------------------------------------------


def test_csharp_fixture_counts():
    fc = _walk("csharp/PerfIoInLoop.cs", "csharp")
    counts = _kinds(fc.perf_hits)
    # db (ToListAsync, SaveChangesAsync, sync ToList) · network (GetAsync) ·
    # filesystem (File.ReadAllText) = 5.
    assert counts["io_in_loop"] == 5
    assert {h.detail for h in fc.perf_hits if h.kind == "io_in_loop"} == {
        "db",
        "network",
        "filesystem",
    }
    assert counts["string_concat_in_loop"] == 1
    # .Result + .Wait() in async; the awaited ``await task`` does not fire.
    assert counts["blocking_sync_in_async"] == 2


_CSHARP_CASES = [
    (
        "using Microsoft.EntityFrameworkCore;\n"
        "class A{ void M(DbContext ctx, System.Collections.Generic.List<int> ids){"
        "foreach(var id in ids){ ctx.Users.ToList(); }}}",
        [("io_in_loop", "db")],
        "sync ToList WITH an EF import passes the file-level gate",
    ),
    (
        "class A{ void M(System.Collections.Generic.List<int> ids){"
        "foreach(var id in ids){ ids.ToList(); }}}",
        [],
        "in-memory ToList with NO db import is gated out",
    ),
    (
        "class A{ async System.Threading.Tasks.Task M("
        "System.Collections.Generic.List<int> ids){"
        "foreach(var id in ids){ await ctx.Set().FirstOrDefaultAsync(); }}}",
        # Awaited EF query in a loop: io_in_loop + the serial_await co-signal.
        [("io_in_loop", "db"), ("serial_await_in_loop", "db")],
        "EF *Async family is unambiguous db (no import gate)",
    ),
]


@pytest.mark.parametrize("src,expected,note", _CSHARP_CASES, ids=[c[2] for c in _CSHARP_CASES])
def test_csharp_cases(src, expected, note):
    assert _hits("csharp", src) == sorted(expected), note


# ---------------------------------------------------------------------------
# Phase-7c precision fixes (multi-language corpus FP classes)
# ---------------------------------------------------------------------------


def test_csharp_result_pattern_collision_not_blocking():
    """``.Result`` on a namespace/Result-DTO path is NOT a Task block.

    Phase-7c C# corpus: 10/12 ``blocking_sync_in_async`` FPs were
    ``Ardalis.Result.ResultStatus.X`` (``.Result`` as an intermediate namespace
    segment) + a ``response.Result = ...`` DTO write. Neither blocks a thread.
    """
    # Intermediate segment of a qualified name (Ardalis.Result.ResultStatus).
    assert (
        _hits(
            "csharp",
            "class A{ async System.Threading.Tasks.Task M(){ var s = "
            "Ardalis.Result.ResultStatus.Error; }}",
        )
        == []
    )
    # Write to a DTO ``.Result`` property (assignment target, not a read).
    assert (
        _hits(
            "csharp",
            "class A{ async System.Threading.Tasks.Task M(R response){ response.Result = 1; }}",
        )
        == []
    )


def test_csharp_task_result_still_blocks():
    """A genuine terminal ``task.Result`` read in async still fires."""
    assert ("blocking_sync_in_async", ".Result") in _hits(
        "csharp",
        "class A{ async System.Threading.Tasks.Task M(System.Threading.Tasks.Task<int> t){ "
        "var x = t.Result; }}",
    )


def test_csharp_task_result_chained_read_still_blocks():
    """``itemGetTask.Result.CatalogItem`` (camelCase local) still blocks.

    Phase-7c eShopOnWeb: a genuine ``Task.Result`` read FOLLOWED by a member
    access has the same ``X.Result.Y`` shape as the ``Ardalis.Result.X``
    namespace FP; the receiver-root casing gate keeps the real one.
    """
    assert ("blocking_sync_in_async", ".Result") in _hits(
        "csharp",
        "class A{ async System.Threading.Tasks.Task M(){ "
        "var c = itemGetTask.Result.CatalogItem; }}",
    )


def test_go_sql_rows_scan_not_io_in_loop():
    """``rows.Scan`` inside ``for rows.Next()`` is a cursor decode, not a sink.

    Phase-7c syft corpus: ``*sql.Rows.Scan`` FP'd ``io_in_loop`` (the query ran
    once, outside the loop). ``Scan`` is no longer a GORM finisher verb.
    """
    src = (
        'package p\nimport "database/sql"\n'
        "func f(rows *sql.Rows){ for rows.Next() { var x int; _ = rows.Scan(&x) } }\n"
    )
    assert not any(k == "io_in_loop" for k, _ in _hits("go", src))


def test_go_gorm_create_still_io_in_loop():
    """A real GORM finisher (``Create``) in a range loop still fires."""
    src = (
        'package p\nimport "gorm.io/gorm"\n'
        "func f(db *gorm.DB, items []int){ for _, it := range items { db.Create(&it) } }\n"
    )
    assert ("io_in_loop", "db") in _hits("go", src)


def test_python_asyncio_sleep_not_a_sink():
    """``await asyncio.sleep(...)`` in a loop is a yield, not network I/O.

    Phase-7c headroom corpus: the awaited-network arm FP'd ``io_in_loop`` /
    ``serial_await_in_loop`` on every backoff/poll loop.
    """
    src = (
        "import asyncio\nasync def f(items):\n    for x in items:\n        await asyncio.sleep(x)\n"
    )
    kinds = {k for k, _ in _hits("python", src)}
    assert "io_in_loop" not in kinds
    assert "serial_await_in_loop" not in kinds


# ---------------------------------------------------------------------------
# Phase-7d marker refinements (precision lifts surfaced by the 7c corpus)
# ---------------------------------------------------------------------------


def test_go_regex_dynamic_pattern_not_flagged():
    """``regexp.MustCompile(pat)`` with a dynamic arg is not hoistable.

    Phase-7c Go corpus: 10 dynamic-arg cases were UNSURE (the pattern may vary
    per iteration). Only a string-literal pattern is unambiguously hoistable.
    """
    dyn = 'package p\nimport "regexp"\nfunc f(ids []string){ for _, id := range ids { regexp.MustCompile(id) } }\n'
    lit = 'package p\nimport "regexp"\nfunc f(ids []string){ for _, id := range ids { regexp.MustCompile(`^x$`) } }\n'
    assert not any(k == "regex_compile_in_loop" for k, _ in _hits("go", dyn))
    assert ("regex_compile_in_loop", "") in _hits("go", lit)


def test_python_string_concat_reset_per_iteration_not_flagged():
    """``buf = seed; ... buf += part`` reset each iteration is bounded, not O(n^2).

    Phase-7c headroom corpus: reset-per-iteration was the dominant Py FP (77.8%).
    """
    reset = (
        "def f(rows):\n"
        "    for r in rows:\n"
        "        buf = 'x'\n"
        "        for c in r:\n"
        "            buf += 'y'\n"
    )
    accum = (
        "def g(rows):\n    out = ''\n    for r in rows:\n        out += 'line'\n    return out\n"
    )
    assert not any(k == "string_concat_in_loop" for k, _ in _hits("python", reset))
    assert ("string_concat_in_loop", "") in _hits("python", accum)


def test_ts_nested_io_requires_collection_outer_loop():
    """A ``while`` cursor wrapping an inner ``for ... of`` is io_in_loop, not nested.

    Phase-7c dub corpus: pagination ``while (hasMore) { for (row of chunk) … }``
    miscounted as ``nested_loop_with_io``; the outer loop must iterate a
    collection for the O(n*m) round-trip claim to hold.
    """
    cursor = (
        "async function f(prisma){ while (hasMore) { for (const r of chunk) {"
        " await prisma.user.findMany(); } } }"
    )
    nested = (
        "async function g(prisma, xs, ys){ for (const x of xs) { for (const y of ys) {"
        " await prisma.user.findMany(); } } }"
    )
    assert not any(k == "nested_loop_with_io" for k, _ in _hits("typescript", cursor))
    assert ("nested_loop_with_io", "db") in _hits("typescript", nested)


# ---------------------------------------------------------------------------
# Phase-7d language-specific markers
# ---------------------------------------------------------------------------


def test_go_goroutine_in_range_loop_but_not_accept_loop():
    spawn = "package m\nfunc f(items []int){ for _, it := range items { go work(it) } }"
    accept = "package m\nfunc f(){ for { go handle() } }"
    # Single-variable ``for i := range n`` is a bounded count loop (Go 1.22
    # range-over-int / a count constant), not a per-element fan-out (Phase-7d).
    count = "package m\nfunc f(){ for i := range numG { go work(i) } }"
    assert ("goroutine_in_unbounded_loop", "") in _hits("go", spawn)
    assert not any(k == "goroutine_in_unbounded_loop" for k, _ in _hits("go", accept))
    assert not any(k == "goroutine_in_unbounded_loop" for k, _ in _hits("go", count))


def test_python_list_insert_zero_vs_variable_index():
    front = "def f(xs):\n    out = []\n    for x in xs:\n        out.insert(0, x)\n"
    idx = "def f(xs):\n    out = []\n    for i, x in enumerate(xs):\n        out.insert(i, x)\n"
    # A list re-created fresh each iteration is bounded, not O(n^2) (Phase-7d
    # reset guard — the same FP class as string_concat).
    reset = "def f(xs):\n    for x in xs:\n        cand = [x]\n        cand.insert(0, prev)\n"
    assert ("list_insert_zero_in_loop", "") in _hits("python", front)
    assert not any(k == "list_insert_zero_in_loop" for k, _ in _hits("python", idx))
    assert not any(k == "list_insert_zero_in_loop" for k, _ in _hits("python", reset))


def test_ts_json_parse_only_deep_clone_idiom():
    """Bare ``JSON.parse(x.payload)`` of a distinct per-iteration payload is
    necessary work (Phase-7d: bare parse/stringify was 0% precision)."""
    bare = "function f(xs){ for (const x of xs) { const c = JSON.parse(x.payload); } }"
    assert not any(k == "json_parse_in_loop" for k, _ in _hits("typescript", bare))


def test_python_pd_concat_in_loop():
    src = (
        "import pandas as pd\n"
        "def f(chunks):\n"
        "    df = pd.DataFrame()\n"
        "    for c in chunks:\n"
        "        df = pd.concat([df, c])\n"
    )
    assert ("pd_concat_in_loop", "") in _hits("python", src)


def test_python_pandas_iterrows_in_loop():
    # The iterrows() call lives in the loop HEADER, so the body call-markers
    # never see it — the loop_iterable_call_marker hook fires on the loop node.
    iterrows = "def f(df):\n    for _, row in df.iterrows():\n        use(row)\n"
    assert ("pandas_iterrows_in_loop", "") in _hits("python", iterrows)
    # itertuples is the recommended faster alternative — never flagged.
    tuples = "def f(df):\n    for row in df.itertuples():\n        use(row)\n"
    assert not any(k == "pandas_iterrows_in_loop" for k, _ in _hits("python", tuples))
    # A plain collection iterable is not a header-call smell.
    plain = "def f(rows):\n    for row in rows:\n        use(row)\n"
    assert not any(k == "pandas_iterrows_in_loop" for k, _ in _hits("python", plain))


def test_ts_json_parse_in_loop():
    src = "function f(xs){ for (const x of xs) { const c = JSON.parse(JSON.stringify(x)); } }"
    assert ("json_parse_in_loop", "") in _hits("typescript", src)


def test_ts_array_spread_in_reduce_vs_push():
    spread = "function f(xs){ return xs.reduce((acc, x) => [...acc, x], []); }"
    push = "function f(xs){ return xs.reduce((acc, x) => { acc.push(x); return acc; }, []); }"
    assert ("array_spread_in_reduce", "") in _hits("typescript", spread)
    assert not any(k == "array_spread_in_reduce" for k, _ in _hits("typescript", push))
