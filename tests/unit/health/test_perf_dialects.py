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
        "class A{void m(java.util.List<String> ids){"
        "for(String id:ids){ helper(id); }}}",
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
    # db (db.Query) · network (http.Get) · filesystem (os.Open ×2, incl. the
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
        "package m\n"
        "func f(repo Repo, ids []int){ for _, id := range ids { repo.Find(id) } }",
        [],
        "ambiguous Find with NO db import is gated out",
    ),
    (
        'package m\nimport "database/sql"\n'
        'func f(db *sql.DB){ for {} ; db.Query("x") }',
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
