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
# Rust
# ---------------------------------------------------------------------------


def test_rust_fixture_counts():
    fc = _walk("rust/perf_io_in_loop.rs", "rust")
    counts = _kinds(fc.perf_hits)
    # db (sqlx fetch_all) · filesystem (std::fs::read + fs::read_to_string) ·
    # network (reqwest::get) = 4. The constant-range and out-of-loop reads and
    # the push_str loop (amortized O(1)) do not fire.
    assert counts["io_in_loop"] == 4
    assert {h.detail for h in fc.perf_hits if h.kind == "io_in_loop"} == {
        "db",
        "filesystem",
        "network",
    }
    # The two awaited sinks (sqlx + reqwest) carry the serial_await co-signal.
    assert counts["serial_await_in_loop"] == 2
    assert counts["regex_compile_in_loop"] == 1
    assert counts["resource_construction_in_loop"] == 1
    assert counts["blocking_sync_in_async"] == 1
    # Rust String building is amortized, so string_concat is never a Rust marker.
    assert counts["string_concat_in_loop"] == 0


_RUST_CASES = [
    (
        "async fn f(pool: &PgPool, ids: Vec<i32>){ for id in ids { "
        'let _ = sqlx::query("x").fetch_one(pool).await; } }',
        [("io_in_loop", "db"), ("serial_await_in_loop", "db")],
        "a sqlx fetch_one verb in a for loop is an awaited db round-trip",
    ),
    (
        "fn f(paths: Vec<String>){ for p in paths { let _ = std::fs::read(&p); } }",
        [("io_in_loop", "filesystem")],
        "std::fs::read keys on the `fs` module qualifier, not the `std` root",
    ),
    (
        "use reqwest;\nasync fn f(urls: Vec<String>){ for u in urls { "
        "let _ = reqwest::get(&u).await; } }",
        [("io_in_loop", "network"), ("serial_await_in_loop", "network")],
        "reqwest::get free function in a loop is a network round-trip",
    ),
    (
        "async fn f(tx: Sender<i32>, n: i32){ for i in 0..n { tx.send(i).await; } }",
        [],
        "a channel tx.send(i).await is NOT a reqwest round-trip (no false sink)",
    ),
    (
        'fn f(items: Vec<i32>){ for x in items { let r = Regex::new("^a$"); let _ = r; } }',
        [("regex_compile_in_loop", "")],
        "Regex::new with a static literal pattern is hoistable",
    ),
    (
        "fn f(items: Vec<String>){ for x in items { let r = Regex::new(&x); let _ = r; } }",
        [],
        "Regex::new with a dynamic arg may vary per iteration — not flagged",
    ),
    (
        "async fn f(urls: Vec<String>){ for u in urls { let _ = PgPool::connect(&u).await; } }",
        [("resource_construction_in_loop", "")],
        "a fresh PgPool::connect per iteration is resource construction",
    ),
    (
        "fn f(items: Vec<i32>){ let mut s = String::new(); "
        "for x in items { s.push_str(&x.to_string()); } let _ = s; }",
        [],
        "String::push_str is amortized O(1) — Rust has no string_concat marker",
    ),
    (
        "async fn f(){ let _ = futures::executor::block_on(g()); }",
        [("blocking_sync_in_async", "block_on")],
        "block_on inside an async fn blocks the executor",
    ),
]


@pytest.mark.parametrize("src,expected,note", _RUST_CASES, ids=[c[2] for c in _RUST_CASES])
def test_rust_cases(src, expected, note):
    assert _hits("rust", src) == sorted(expected), note


def test_rust_execute_gated_on_db_import():
    """``.execute()`` is ambiguous, so it fires only with file-level db evidence
    (a ``use sqlx::...`` resolved through the ``use_declaration`` io_names pass)."""
    with_import = (
        "use sqlx::PgPool;\nasync fn f(pool: &PgPool, ids: Vec<i32>){ "
        'for id in ids { let _ = sqlx::query("x").execute(pool).await; } }'
    )
    no_import = "async fn f(c: &Conn, ids: Vec<i32>){ for id in ids { c.execute(id); } }"
    assert ("io_in_loop", "db") in _hits("rust", with_import)
    assert not any(k == "io_in_loop" for k, _ in _hits("rust", no_import))


def test_rust_fs_in_async_blocks():
    """Sync ``std::fs`` inside an ``async fn`` is executor-blocking; the awaited
    ``tokio::fs`` equivalent is excluded by the walker's not-awaited gate."""
    blocking = "async fn f(p: String){ let _ = std::fs::read(&p); }"
    assert ("blocking_sync_in_async", "fs::read") in _hits("rust", blocking)
    # tokio::fs::read is always awaited -> never read as a blocking sync call.
    awaited = "async fn f(p: String){ let _ = tokio::fs::read(&p).await; }"
    assert not any(k == "blocking_sync_in_async" for k, _ in _hits("rust", awaited))


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
    # Soft-gated on a pandas import present in the file (a bare .iterrows() with
    # no pandas import is now treated as a name collision, not a DataFrame).
    iterrows = "import pandas\ndef f(df):\n    for _, row in df.iterrows():\n        use(row)\n"
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


# ---------------------------------------------------------------------------
# Dart
# ---------------------------------------------------------------------------


def test_dart_fixture_counts():
    fc = _walk("dart/perf_io_in_loop.dart", "dart")
    counts = _kinds(fc.perf_hits)
    assert counts["io_in_loop"] == 3
    assert {h.detail for h in fc.perf_hits if h.kind == "io_in_loop"} == {
        "db",
        "filesystem",
        "network",
    }
    assert counts["serial_await_in_loop"] == 3
    assert counts["string_concat_in_loop"] == 1
    assert counts["resource_construction_in_loop"] == 1


_DART_CASES = [
    (
        "import 'package:http/http.dart' as http;\n"
        "Future<void> f(List<Uri> urls) async {\n"
        "  for (final u in urls) { final r = await http.get(u); }\n"
        "}\n",
        [("io_in_loop", "network"), ("serial_await_in_loop", "network")],
        "an awaited http verb in a for-in loop is a serial network round-trip",
    ),
    (
        "Future<void> f(List<dynamic> files) async {\n"
        "  for (final x in files) { final t = await x.readAsString(); }\n"
        "}\n",
        [("io_in_loop", "filesystem"), ("serial_await_in_loop", "filesystem")],
        "readAsString is a File round-trip regardless of receiver name",
    ),
    (
        "String f(List<String> parts) {\n"
        "  var s = '';\n"
        "  for (final p in parts) { s += 'x'; }\n"
        "  return s;\n"
        "}\n",
        [("string_concat_in_loop", "")],
        "immutable-string += accumulation in a loop is O(n^2)",
    ),
    (
        "void f(List<int> xs) {\n"
        "  for (final x in xs) { final d = Dio(); }\n"
        "}\n",
        [("resource_construction_in_loop", "")],
        "a Dio client constructed per-iteration",
    ),
    (
        "import 'package:http/http.dart' as http;\n"
        "void f() {\n"
        "  for (var i = 0; i < 3; i++) { http.get(Uri.parse('u')); }\n"
        "}\n",
        [],
        "a constant-bound loop is not data-dependent N+1",
    ),
    (
        "void f(List<String> parts) {\n"
        "  var s = '';\n"
        "  for (final p in parts) { s = ''; s += p; }\n"
        "}\n",
        [],
        "opaque-variable += is not provably string concat (precision-first)",
    ),
]


@pytest.mark.parametrize("src,expected,note", _DART_CASES, ids=[c[2] for c in _DART_CASES])
def test_dart_cases(src, expected, note):
    assert _hits("dart", src) == sorted(expected), note


def test_dart_string_concat_reset_per_iteration_not_flagged():
    # ``name`` is declared fresh inside the loop body, so ``name += '/'``
    # is not a cross-iteration accumulator (shelf directory_listing FP).
    src = (
        "void f(List<String> xs) {\n"
        "  for (final x in xs) {\n"
        "    var name = x;\n"
        "    name += '/';\n"
        "    use(name);\n"
        "  }\n"
        "}\n"
    )
    assert not any(k == "string_concat_in_loop" for k, _ in _hits("dart", src))


# ---------------------------------------------------------------------------
# Scala
# ---------------------------------------------------------------------------


def test_scala_fixture_counts():
    fc = _walk("scala/perf_io_in_loop.scala", "scala")
    counts = _kinds(fc.perf_hits)
    # Source.fromFile in readAll + in matrix's inner loop.
    assert counts["io_in_loop"] == 2
    assert {h.detail for h in fc.perf_hits if h.kind == "io_in_loop"} == {"filesystem"}
    assert counts["nested_loop_with_io"] == 1
    # ``"a+b".r`` (StringOps) + ``Pattern.compile`` (JVM interop); the hoisted
    # ``.r`` outside the loop stays quiet.
    assert counts["regex_compile_in_loop"] == 2
    assert counts["string_concat_in_loop"] == 1
    assert counts["lock_in_loop"] == 1
    assert counts["blocking_sync_in_async"] == 1
    # The constant-bound ``1 to 3`` loop and the plain helper call do not fire.
    assert counts["resource_construction_in_loop"] == 0


_SCALA_CASES = [
    (
        "import slick.jdbc.PostgresProfile.api._\n"
        "object A { def m(ids: List[Long], db: Database): Unit = {\n"
        "  for (id <- ids) { db.run(query(id)) }\n"
        "} }\n",
        [("io_in_loop", "db")],
        "Slick db.run WITH a db import passes the file-level gate",
    ),
    (
        "object A { def m(ids: List[Long], job: Runner): Unit = {\n"
        "  for (id <- ids) { job.run(id) }\n"
        "} }\n",
        [],
        "ambiguous run() with NO db import is gated out",
    ),
    (
        "object A { def m(ids: List[Long], xa: Any): Unit = {\n"
        "  for (id <- ids) { query(id).transact(xa) }\n"
        "} }\n",
        [("io_in_loop", "db")],
        "doobie .transact is distinctive enough to fire ungated",
    ),
    (
        "import sttp.client3._\n"
        "object A { def m(reqs: List[Req], backend: Backend): Unit = {\n"
        "  for (r <- reqs) { r.send(backend) }\n"
        "} }\n",
        [("io_in_loop", "network")],
        "sttp request.send WITH a network import",
    ),
    (
        "object A { def m(msgs: List[Msg], actor: Actor): Unit = {\n"
        "  for (m <- msgs) { actor.send(m) }\n"
        "} }\n",
        [],
        "generic .send with NO network import is gated out",
    ),
    (
        "object A { def m(paths: List[String]): Unit = {\n"
        "  for (p <- paths) { os.read(p) }\n"
        "} }\n",
        [("io_in_loop", "filesystem")],
        "os-lib os.read is method-gated (no import needed)",
    ),
    (
        "import scala.concurrent.Future\n"
        "object A { def m(): Future[Int] = {\n"
        "  Thread.sleep(100)\n"
        "  Future.successful(1)\n"
        "} }\n",
        [("blocking_sync_in_async", "Thread.sleep")],
        "Thread.sleep inside a Future-returning def",
    ),
    (
        "import scala.concurrent.Await\n"
        "object A { def m(fut: Fut): Int = {\n"
        "  Await.result(fut, d)\n"
        "} }\n",
        [],
        "Await.result in a NON-Future def is not sync-over-async",
    ),
    (
        "object A { def m(items: List[String]): String = {\n"
        '  var acc = ""\n'
        "  var i = 0\n"
        "  while (i < items.length) {\n"
        '    acc = acc + "x"\n'
        "    i += 1\n"
        "  }\n"
        "  acc\n"
        "} }\n",
        [("string_concat_in_loop", "")],
        "`acc = acc + \"lit\"` reassignment form on a string var",
    ),
]


@pytest.mark.parametrize("src,expected,note", _SCALA_CASES, ids=[c[2] for c in _SCALA_CASES])
def test_scala_cases(src, expected, note):
    assert _hits("scala", src) == sorted(expected), note


def test_scala_same_collection_nested_loop_fact():
    # Two nested for-comprehensions over the SAME collection record the
    # centrality-gated ``nested_loop_quadratic`` fact (not a raw hit).
    src = (
        "object A { def m(items: List[Int]): Unit = {\n"
        "  for (a <- items) {\n"
        "    for (b <- items) {\n"
        "      combine(a, b)\n"
        "    }\n"
        "  }\n"
        "} }\n"
    )
    fc = walk_file("t.scala", "scala", src.encode())
    assert any(f.nested_loop_line for f in fc.perf_fn_facts)
    assert not any(h.kind == "nested_loop_quadratic" for h in fc.perf_hits)


# ---------------------------------------------------------------------------
# Ruby
# ---------------------------------------------------------------------------


def test_ruby_fixture_counts():
    fc = _walk("ruby/perf_io_in_loop.rb", "ruby")
    counts = _kinds(fc.perf_hits)
    # Net::HTTP.get + File.read (map) + Dir.glob + nested File.read +
    # Order.where + conn.get (require-gated Faraday) + backticks + File.write.
    assert counts["io_in_loop"] == 8
    assert {h.detail for h in fc.perf_hits if h.kind == "io_in_loop"} == {
        "network",
        "filesystem",
        "db",
        "subprocess",
    }
    assert counts["nested_loop_with_io"] == 1
    # In-loop Regexp.new fires; the hoisted one stays quiet.
    assert counts["regex_compile_in_loop"] == 1
    # ``out += "<lit>"`` fires; ``buf << line`` and the reset-per-iteration
    # accumulator do not.
    assert counts["string_concat_in_loop"] == 1
    assert counts["resource_construction_in_loop"] == 1
    assert counts["lock_in_loop"] == 1
    assert counts["blocking_io_under_lock"] == 1
    # The constant-bound 3.times / [1, 2].each loops contribute nothing.


_RUBY_CASES = [
    (
        "def m(ids)\n  ids.each do |id|\n    Order.where(id: id)\n  end\nend\n",
        [("io_in_loop", "db")],
        "AR .where on a constant receiver inside .each (the canonical N+1)",
    ),
    (
        "def m(users)\n  users.each do |u|\n    u.posts.find_by(name: u.name)\n  end\nend\n",
        [("io_in_loop", "db")],
        "distinctive find_by fires ungated on any member call",
    ),
    (
        "def m(ids, repo)\n  ids.each do |id|\n    repo.find(id)\n  end\nend\n",
        [],
        "ambiguous find() with NO db require is gated out",
    ),
    (
        'require "active_record"\n'
        "def m(ids, repo)\n  ids.each do |id|\n    repo.find(id)\n  end\nend\n",
        [("io_in_loop", "db")],
        "ambiguous find() WITH a db require passes the file-level gate",
    ),
    (
        "def m(paths)\n  paths.each do |p|\n    get p\n  end\nend\n",
        [],
        "bare get (Sinatra route DSL shape) is not a network sink",
    ),
    (
        'require "faraday"\n'
        "def m(conn, urls)\n  urls.each do |u|\n    conn.get(u)\n  end\nend\n",
        [("io_in_loop", "network")],
        "instance client verb WITH a network require",
    ),
    (
        "def m(conn, urls)\n  urls.each do |u|\n    conn.get(u)\n  end\nend\n",
        [],
        "instance client verb with NO network require is gated out",
    ),
    (
        "def m(queue)\n  loop do\n    File.read(queue.pop)\n  end\nend\n",
        [("io_in_loop", "filesystem")],
        "loop do ... end is an unconditional-repeat loop scope",
    ),
    (
        "def m\n  3.times do\n    File.read(\"x\")\n  end\nend\n",
        [],
        "literal-receiver .times is a constant-bound loop",
    ),
    (
        "def m(items)\n  items.map(&:to_s)\n  File.read(items.first.path)\nend\n",
        [],
        "a combinator WITHOUT an inline block is not a loop scope",
    ),
    (
        "def m(rows)\n  rows.each do |r|\n    transform(r)\n  end\nend\n",
        [],
        "a loop-nested plain helper call is not a sink",
    ),
]


@pytest.mark.parametrize("src,expected,note", _RUBY_CASES, ids=[c[2] for c in _RUBY_CASES])
def test_ruby_cases(src, expected, note):
    assert _hits("ruby", src) == sorted(expected), note


def test_ruby_backticks_are_subprocess():
    src = "def m(names)\n  names.each do |n|\n    `grep #{n} log.txt`\n  end\nend\n"
    assert _hits("ruby", src) == [("io_in_loop", "subprocess")]


def test_ruby_receiver_runs_once():
    # The receiver chain of an iteration call runs ONCE — only the block body
    # is per-iteration. ``Order.where(...)`` here must not be io_in_loop.
    src = "def m\n  Order.where(active: true).each do |o|\n    transform(o)\n  end\nend\n"
    assert _hits("ruby", src) == []


def test_ruby_same_collection_nested_block_loops_fact():
    # Two nested .each blocks over the SAME collection record the
    # centrality-gated ``nested_loop_quadratic`` fact (not a raw hit).
    src = (
        "def m(items)\n"
        "  items.each do |a|\n"
        "    items.each do |b|\n"
        "      combine(a, b)\n"
        "    end\n"
        "  end\n"
        "end\n"
    )
    fc = walk_file("t.rb", "ruby", src.encode())
    assert any(f.nested_loop_line for f in fc.perf_fn_facts)
    assert not any(h.kind == "nested_loop_quadratic" for h in fc.perf_hits)
