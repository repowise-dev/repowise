"""False-flag regression tests for the performance perf pass + dialects.

Each fixture is a counterexample the perf detectors previously mis-flagged. The
tests drive the real collector via ``walk_file`` (which runs
``_collect_perf_hits`` + ``get_language_map`` end to end) and the dialect marker
methods it consumes. Every "not flagged" case is paired with a genuine-positive
guard so the fix cannot pass by simply suppressing the whole marker.
"""

from __future__ import annotations

from repowise.core.analysis.health.complexity import walk_file


def _kinds(lang: str, ext: str, src: bytes) -> list[str]:
    fc = walk_file(f"t.{ext}", lang, src)
    return [h.kind for h in fc.perf_hits]


def _hits(lang: str, ext: str, src: bytes):
    return walk_file(f"t.{ext}", lang, src).perf_hits


# ---------------------------------------------------------------------------
# R1 — loop/lock depth must reset at a nested function/lambda boundary
# ---------------------------------------------------------------------------


def test_r1_python_closure_defined_in_loop_not_io_in_loop():
    # ``handler`` is DEFINED in the loop but invoked elsewhere — its body does
    # not run per outer-loop-iteration.
    src = (
        b"import requests\n"
        b"def register(items, handlers):\n"
        b"    for item in items:\n"
        b"        def handler():\n"
        b"            return requests.get(item.url)\n"
        b"        handlers.append(handler)\n"
        b"    for h in handlers:\n"
        b"        h()\n"
    )
    assert "io_in_loop" not in _kinds("python", "py", src)


def test_r1_python_direct_sink_in_loop_still_fires():
    # Guard against over-suppression: a sink directly in the loop body fires.
    src = (
        b"import requests\ndef f(items):\n    for item in items:\n        requests.get(item.url)\n"
    )
    assert "io_in_loop" in _kinds("python", "py", src)


def test_r1_go_iife_with_defer_not_flagged():
    # The idiomatic ``func(){ ...; defer f.Close() }()`` loop-body wrapper — the
    # recommended defer-in-loop fix — must not be flagged with the leak it fixes.
    src = (
        b"package main\n"
        b'import "os"\n'
        b"func processAll(items []string) {\n"
        b"    for _, item := range items {\n"
        b"        func() {\n"
        b"            f, _ := os.Open(item)\n"
        b"            defer f.Close()\n"
        b"            _ = f\n"
        b"        }()\n"
        b"    }\n"
        b"}\n"
    )
    kinds = _kinds("go", "go", src)
    assert "defer_in_loop" not in kinds
    assert "io_in_loop" not in kinds


def test_r1_csharp_lambda_in_lock_not_blocking_io_under_lock():
    # ``a`` is queued inside the lock but invoked later, off the critical section.
    src = (
        b"using System.IO;\n"
        b"class C {\n"
        b"    void Foo() {\n"
        b"        lock (obj) {\n"
        b"            System.Action a = () => File.ReadAllText(path);\n"
        b"            tasks.Add(a);\n"
        b"        }\n"
        b"    }\n"
        b"}\n"
    )
    assert "blocking_io_under_lock" not in _kinds("csharp", "cs", src)


def test_r1_csharp_direct_io_in_lock_still_fires():
    # Guard: I/O executed directly inside the lock body still fires.
    src = (
        b"using System.IO;\n"
        b"class C {\n"
        b"    void Foo() {\n"
        b"        lock (obj) {\n"
        b"            File.ReadAllText(path);\n"
        b"        }\n"
        b"    }\n"
        b"}\n"
    )
    assert "blocking_io_under_lock" in _kinds("csharp", "cs", src)


# ---------------------------------------------------------------------------
# M10 — a parenthesized await must count as awaited
# ---------------------------------------------------------------------------


def test_m10_parenthesized_await_not_blocking():
    src = b"import time\nasync def f():\n    await (time.sleep(1))\n"
    assert "blocking_sync_in_async" not in _kinds("python", "py", src)


def test_m10_unawaited_sleep_still_fires():
    src = b"import time\nasync def f():\n    time.sleep(1)\n"
    assert "blocking_sync_in_async" in _kinds("python", "py", src)


# ---------------------------------------------------------------------------
# R4 — deque.insert(0, x) is O(1), not O(n^2)
# ---------------------------------------------------------------------------


def test_r4_deque_insert_not_flagged():
    src = (
        b"from collections import deque\n"
        b"def process(items):\n"
        b"    buf = deque()\n"
        b"    for x in items:\n"
        b"        buf.insert(0, x)\n"
        b"    return buf\n"
    )
    assert "list_insert_zero_in_loop" not in _kinds("python", "py", src)


def test_r4_list_insert_still_flagged():
    src = (
        b"def process(items):\n"
        b"    buf = []\n"
        b"    for x in items:\n"
        b"        buf.insert(0, x)\n"
        b"    return buf\n"
    )
    assert "list_insert_zero_in_loop" in _kinds("python", "py", src)


# ---------------------------------------------------------------------------
# N1 — a local shadowing an I/O import is not the import
# ---------------------------------------------------------------------------


def test_n1_shadowed_requests_not_network():
    src = (
        b"import requests\n"
        b"def build_index(pages):\n"
        b"    counts = {}\n"
        b"    for page in pages:\n"
        b"        requests = counts.setdefault(page.key, {})\n"
        b'        requests["count"] = requests.get("count", 0) + 1\n'
        b"    return counts\n"
    )
    assert "io_in_loop" not in _kinds("python", "py", src)


# ---------------------------------------------------------------------------
# N2 — a shadowing function parameter is not the module-level list
# ---------------------------------------------------------------------------


def test_n2_parameter_shadow_not_flagged():
    src = (
        b"def other_scope(big_list, items):\n"
        b"    for x in items:\n"
        b"        if x in big_list:\n"
        b"            pass\n"
        b"\n"
        b"big_list = [1, 2, 3]\n"
    )
    assert "membership_test_against_list_in_loop" not in _kinds("python", "py", src)


def test_n2_real_module_list_membership_still_fires():
    src = (
        b"big_list = [1, 2, 3]\n"
        b"def scan(items):\n"
        b"    for x in items:\n"
        b"        if x in big_list:\n"
        b"            pass\n"
    )
    assert "membership_test_against_list_in_loop" in _kinds("python", "py", src)


# ---------------------------------------------------------------------------
# N4 — iterrows needs a pandas import present
# ---------------------------------------------------------------------------


def test_n4_iterrows_without_pandas_not_flagged():
    src = b"def process(qb):\n    for _, row in qb.iterrows():\n        handle(row)\n"
    assert "pandas_iterrows_in_loop" not in _kinds("python", "py", src)


def test_n4_iterrows_with_pandas_flagged():
    src = (
        b"import pandas\ndef process(df):\n    for _, row in df.iterrows():\n        handle(row)\n"
    )
    assert "pandas_iterrows_in_loop" in _kinds("python", "py", src)


# ---------------------------------------------------------------------------
# R5 — a nested reduce shadowing the accumulator must not flag the outer reduce
# ---------------------------------------------------------------------------


def test_r5_shadowed_nested_reduce_flags_inner_only():
    src = (
        b"function f(items) {\n"
        b"  return items.reduce((acc, x) => {\n"
        b"    acc.push(x);\n"
        b"    const nested = x.parts.reduce((acc, p) => [...acc, p], []);\n"
        b"    return acc;\n"
        b"  }, []);\n"
        b"}\n"
    )
    hits = [h for h in _hits("typescript", "ts", src) if h.kind == "array_spread_in_reduce"]
    # Exactly the inner reduce (line 4) fires; the outer mutate-and-return does not.
    assert len(hits) == 1
    assert hits[0].line == 4


# ---------------------------------------------------------------------------
# R6 — a semaphore-bounded goroutine spawn is not "unbounded"
# ---------------------------------------------------------------------------


def test_r6_semaphore_bounded_goroutine_not_flagged():
    src = (
        b"package main\n"
        b'import "sync"\n'
        b"func process(items []int) {\n"
        b"    sem := make(chan struct{}, 8)\n"
        b"    var wg sync.WaitGroup\n"
        b"    for _, item := range items {\n"
        b"        sem <- struct{}{}\n"
        b"        wg.Add(1)\n"
        b"        go func(it int) {\n"
        b"            defer wg.Done()\n"
        b"            defer func() { <-sem }()\n"
        b"            handle(it)\n"
        b"        }(item)\n"
        b"    }\n"
        b"    wg.Wait()\n"
        b"}\n"
    )
    assert "goroutine_in_unbounded_loop" not in _kinds("go", "go", src)


def test_r6_unbounded_goroutine_still_flagged():
    src = (
        b"package main\n"
        b"func process(items []int) {\n"
        b"    for _, item := range items {\n"
        b"        go func(it int) {\n"
        b"            handle(it)\n"
        b"        }(item)\n"
        b"    }\n"
        b"}\n"
    )
    assert "goroutine_in_unbounded_loop" in _kinds("go", "go", src)
