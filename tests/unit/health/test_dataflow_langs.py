"""Dataflow def/use + CFG + Extract Method coverage for the ported languages.

Covers the Go, TS/JS, Java, and Rust ``DefUseDialect``s and the language-
agnostic CFG / slicer once the Python-specific grammar was lifted onto
``LanguageNodeMap``. Best-effort like the other dataflow tests: each language
skips when its tree-sitter pack is missing rather than failing.
"""

from __future__ import annotations

import textwrap

import pytest

from repowise.core.analysis.health.complexity.ast_utils import _collect_function_nodes
from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import (
    analyze_file,
    analyze_function,
    build_cfgs_for_file,
    find_extractions,
)
from repowise.core.analysis.health.perf.promotion import _loop_iterations_independent


def _require(language: str) -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")


def _analyses(language: str, src: str):
    _require(language)
    res = analyze_file(f"m.{language}", language, textwrap.dedent(src).encode(), flagged_only=False)
    if res.stats.functions_seen == 0:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return res.functions


def _first(language: str, src: str):
    fns = _analyses(language, src)
    assert fns, "no function analysed"
    return fns[0]


def _def_names(fn) -> set[str]:
    return {d.var for d in fn.def_use.definitions}


def _use_names(fn) -> set[str]:
    return {u.name for b in fn.def_use.blocks.values() for u in b.uses}


# == Go =========================================================================

# Every Go write shape in one function: ``:=``, ``var``, ``=``, ``+=``, ``x++``,
# a ``range`` binder, a multi-assign, and a selector/index target (not a local).
_GO_SHAPES = """
package main
func shapes(input []int, base int) int {
    total := 0
    var scale int = 2
    for i, v := range input {
        total += v * scale
        seen[i] = v
        cfg.count++
    }
    a, b := base, total
    a, b = b, a
    return a + b
}
"""


def test_go_def_use_classification():
    fn = _first("go", _GO_SHAPES)
    defs = _def_names(fn)
    # ``:=`` / ``var`` / ``range`` / multi-assign targets are all locals.
    assert {"total", "scale", "i", "v", "a", "b"} <= defs
    assert {"input", "base"} <= _def_names(fn)  # parameters seeded as defs
    # A selector target (``cfg.count++``) and index target (``seen[i] = v``) bind
    # no local: their bases are reads, never defs.
    assert "seen" not in defs
    assert "cfg" not in defs
    assert "count" not in defs
    uses = _use_names(fn)
    assert {"scale", "v", "seen", "cfg", "base"} <= uses


def test_go_for_clause_and_while_heads():
    fn = _first(
        "go",
        """
        package main
        func loops(n int) int {
            sum := 0
            for j := 0; j < n; j++ {
                sum += j
            }
            for sum < 100 {
                sum *= 2
            }
            return sum
        }
        """,
    )
    # ``j`` is bound by the C-style for-clause initializer.
    assert "j" in _def_names(fn)
    # The CFG has two loop headers with back-edges.
    headers = [b for b in fn.cfg.blocks if b.kind == "loop_header"]
    assert len(headers) == 2
    assert fn.cfg.back_edges()


def test_go_method_receiver_is_seeded():
    fn = _first(
        "go",
        """
        package main
        func (s *Server) handle(req int) int {
            x := s.lookup(req)
            return x
        }
        """,
    )
    assert "s" in _def_names(fn)  # the receiver is available in the body


def test_go_else_if_chain_branches():
    # The C-family ``else if`` nests in the ``alternative`` field; each arm is a
    # branch and every path reaches exit.
    fn = _first(
        "go",
        """
        package main
        func grade(x int) int {
            y := 0
            if x == 1 {
                y = 1
            } else if x == 2 {
                y = 2
            } else if x == 3 {
                y = 3
            } else {
                y = 4
            }
            return y
        }
        """,
    )
    branches = [b for b in fn.cfg.blocks if b.kind == "branch"]
    assert len(branches) == 3  # three ``if`` tests
    assert fn.cfg.exit_id in fn.cfg.reachable_ids()


def test_go_if_else_branch_and_continue():
    fn = _first(
        "go",
        """
        package main
        func f(items []int, x int) int {
            total := 0
            for _, it := range items {
                if it > x {
                    total += it
                } else {
                    total -= it
                }
                if it == 0 {
                    continue
                }
            }
            return total
        }
        """,
    )
    branches = [b for b in fn.cfg.blocks if b.kind == "branch"]
    assert len(branches) == 2
    # The else arm and a join both exist; the loop has a back-edge.
    assert [b for b in fn.cfg.blocks if b.kind == "join"]
    assert fn.cfg.back_edges()


# A long Go function whose compute-average tail is a clean extraction.
_GO_PROCESS = """
package main
func process(records []int, threshold int) (int, int) {
    results := []int{}
    errors := 0
    for _, r := range records {
        if r < 0 {
            errors++
            continue
        }
        results = append(results, r)
    }
    total := 0
    count := 0
    for _, v := range results {
        if v > threshold {
            total += v
            count++
        } else {
            total -= v
        }
    }
    average := 0
    if count > 0 {
        average = total / count
    }
    return average, errors
}
"""


def test_go_extract_method_fires():
    lmap = get_language_map("go")
    extractions = find_extractions(_first("go", _GO_PROCESS), lmap)
    assert extractions, "expected at least one Go extraction"
    best = extractions[0]
    # The strongest extraction is the compute-average tail.
    assert "average" in best.returns
    assert "results" in best.params and "threshold" in best.params
    assert len(best.returns) <= 1
    assert best.ccn_removed >= 1
    assert best.slice_nloc >= 6
    # The whole function body is never offered as an extraction.
    assert not (best.start_line <= 4 and best.end_line >= 27)


def test_go_extractions_are_deterministic():
    lmap = get_language_map("go")
    fn = _first("go", _GO_PROCESS)

    def serialize():
        return [
            (e.start_line, e.end_line, e.params, e.returns, e.ccn_removed)
            for e in find_extractions(fn, lmap)
        ]

    first = serialize()
    for _ in range(3):
        assert serialize() == first


# == TypeScript / JavaScript ====================================================

_TS_SHAPES = """
function shapes(input: number[], base: number): number {
    let total = 0;
    const scale = 2;
    var legacy = 1;
    for (const v of input) {
        total += v * scale;
        legacy++;
    }
    const [a, b] = pair(total);
    const {p, q} = obj;
    obj.attr = base;
    arr[0] = total;
    return a + b + p + q + legacy;
}
"""


def test_ts_def_use_classification():
    fn = _first("typescript", _TS_SHAPES)
    defs = _def_names(fn)
    # let / const / var / array-destructure / object-destructure are all locals.
    assert {"total", "scale", "legacy", "v", "a", "b", "p", "q"} <= defs
    assert {"input", "base"} <= defs  # parameters
    # Member / subscript targets bind no local; their bases are reads.
    assert "obj" not in defs
    assert "attr" not in defs
    assert "arr" not in defs
    uses = _use_names(fn)
    assert {"scale", "v", "obj", "base", "total"} <= uses


def test_js_def_use_destructuring():
    # Plain JS (no type annotations) exercises the shared dialect on the JS pack.
    fn = _first(
        "javascript",
        """
        function f(items, factor) {
            let acc = 0;
            for (const x of items) {
                acc += x * factor;
            }
            const [head, ...tail] = items;
            return acc + head + tail.length;
        }
        """,
    )
    defs = _def_names(fn)
    assert {"acc", "x", "head", "tail"} <= defs
    assert {"items", "factor"} <= defs


def test_ts_shorthand_object_literal_reads():
    # ``{ days, limit }`` in a return reads both locals; ``shorthand_property_
    # identifier`` must count as a use even though its ``_pattern`` twin is a
    # destructuring def.
    fn = _first(
        "typescript",
        """
        function build(days: number, limit: number) {
            const payload = compute(days);
            return { days, limit, payload };
        }
        """,
    )
    uses = _use_names(fn)
    assert {"days", "limit", "payload"} <= uses
    # Reads never become defs.
    defs = _def_names(fn)
    assert defs == {"days", "limit", "payload"}  # params + the one declaration


def test_ts_shorthand_mixed_with_destructuring_same_statement():
    # LHS shorthand is a destructuring def; RHS shorthand is a read -- both on
    # one statement.
    fn = _first(
        "typescript",
        """
        function f(source: { a: number }, b: number) {
            const { a } = { ...source, b };
            return a;
        }
        """,
    )
    assert "a" in _def_names(fn)
    # On the destructuring line itself only ``a`` binds; the RHS shorthand
    # ``b`` stays a read (its sole def is the parameter seed).
    destructure_defs = {d.var for d in fn.def_use.definitions if d.line == 3}
    assert destructure_defs == {"a"}
    uses = _use_names(fn)
    assert {"source", "b"} <= uses


def test_js_shorthand_and_spread_reads():
    # Plain JS pack: spread reads its identifier, shorthand reads its local.
    fn = _first(
        "javascript",
        """
        function merge(base, extra) {
            const combined = { ...base, extra };
            return combined;
        }
        """,
    )
    uses = _use_names(fn)
    assert {"base", "extra"} <= uses
    assert "extra" not in {d.var for d in fn.def_use.definitions if d.line > fn.start_line}


_TS_PROCESS = """
function process(records: number[], threshold: number): [number, number] {
    const results: number[] = [];
    let errors = 0;
    for (const r of records) {
        if (r < 0) {
            errors += 1;
            continue;
        }
        results.push(r);
    }
    let total = 0;
    let count = 0;
    for (const v of results) {
        if (v > threshold) {
            total += v;
            count += 1;
        } else {
            total -= v;
        }
    }
    let average = 0;
    if (count > 0) {
        average = total / count;
    }
    return [average, errors];
}
"""


def test_ts_extract_method_fires():
    lmap = get_language_map("typescript")
    extractions = find_extractions(_first("typescript", _TS_PROCESS), lmap)
    assert extractions, "expected at least one TS extraction"
    best = extractions[0]
    assert "average" in best.returns
    assert "results" in best.params and "threshold" in best.params
    assert len(best.returns) <= 1
    assert best.ccn_removed >= 1
    assert best.slice_nloc >= 6


def test_ts_guard_cascade_has_no_extraction():
    # Every span contains a return -> no single-exit slice.
    lmap = get_language_map("typescript")
    src = """
        function classify(x: number): string {
            if (x < 0) {
                return "neg";
            }
            if (x === 0) {
                return "zero";
            }
            if (x < 10) {
                return "small";
            }
            return "large";
        }
        """
    assert find_extractions(_first("typescript", src), lmap) == []


def test_ts_extractions_are_deterministic():
    lmap = get_language_map("typescript")
    fn = _first("typescript", _TS_PROCESS)

    def serialize():
        return [
            (e.start_line, e.end_line, e.params, e.returns, e.ccn_removed)
            for e in find_extractions(fn, lmap)
        ]

    first = serialize()
    for _ in range(3):
        assert serialize() == first


# == flagged-only gate (the per-language budget contract) =======================


def test_go_flagged_only_gate_skips_small_functions():
    _require("go")
    lines = ["package main", "func big(x int) int {", "    y := 0"]
    for i in range(12):
        lines += [f"    if x == {i} {{", f"        y = {i}", "    }"]
    lines += ["    return y", "}", "", "func tiny() int {", "    return 1", "}", ""]
    result = build_cfgs_for_file("m.go", "go", "\n".join(lines).encode())
    if result.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for go")
    assert result.stats.functions_seen == 2
    assert result.stats.functions_built == 1  # only the flagged big function
    assert [fc.name for fc in result.functions] == ["big"]


def test_ts_flagged_only_gate_skips_small_functions():
    _require("typescript")
    lines = ["function big(x: number): number {", "    let y = 0;"]
    for i in range(12):
        lines += [f"    if (x === {i}) {{", f"        y = {i};", "    }"]
    lines += ["    return y;", "}", "", "function tiny(): number {", "    return 1;", "}", ""]
    result = build_cfgs_for_file("m.ts", "typescript", "\n".join(lines).encode())
    if result.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for typescript")
    assert result.stats.functions_seen == 2
    assert result.stats.functions_built == 1
    assert [fc.name for fc in result.functions] == ["big"]


# == the loop-carried-dependence proof, cross-language ==========================


def _independent(language: str, src: str, marker: str) -> bool:
    """Run the promotion proof at *marker*'s line in *src*'s first function."""
    _require(language)
    from tree_sitter import Parser

    from repowise.core.ingestion.parser import _get_language

    body = textwrap.dedent(src)
    lmap = get_language_map(language)
    parser = Parser(_get_language(language))
    tree = parser.parse(body.encode())
    nodes = _collect_function_nodes(tree.root_node, lmap)
    assert nodes, "no function node parsed"
    analyzed = analyze_function(nodes[0], language, lmap)
    assert analyzed is not None, "analysis returned None"
    cfg, def_use, reaching = analyzed
    line = next(i for i, ln in enumerate(body.splitlines(), start=1) if marker in ln)
    return _loop_iterations_independent(cfg, def_use, reaching, line)


# == Java =======================================================================

# Every Java write shape in one method: multi-declarator decl, ``=`` vs ``+=``
# (operator-sniffed), ``x++`` / ``--x``, field / array targets (not locals),
# an enhanced-for binder, a C-style for initializer, and try-with-resources.
_JAVA_SHAPES = """
class Demo {
    int shapes(int[] input, int base, String... rest) {
        int total = 0;
        int a = 1, b = 2;
        total += base;
        a++;
        --b;
        this.field = total;
        seen[a] = b;
        for (int i = 0; i < input.length; i++) {
            total += input[i];
        }
        for (int v : input) {
            total -= v;
        }
        try (var res = open()) {
            total = res.hashCode();
        } catch (RuntimeException e) {
            total = 0;
        }
        return total + a + b;
    }
}
"""


def test_java_def_use_classification():
    fn = _first("java", _JAVA_SHAPES)
    defs = _def_names(fn)
    # Declarations, for-init, enhanced-for binder, and try resources are locals.
    assert {"total", "a", "b", "i", "v", "res"} <= defs
    assert {"input", "base", "rest"} <= defs  # parameters (varargs included)
    # Field (``this.field = ...``) and array (``seen[a] = ...``) targets bind
    # no local: their bases are reads, never defs.
    assert "field" not in defs
    assert "seen" not in defs
    uses = _use_names(fn)
    assert {"base", "input", "seen", "a", "b", "total", "res"} <= uses
    # Method names are never variable reads.
    assert "hashCode" not in uses
    assert "open" not in uses


def test_java_compound_assign_reads_target():
    fn = _first(
        "java",
        """
        class Demo {
            int f(int x) {
                int acc = 0;
                acc += x;
                return acc;
            }
        }
        """,
    )
    # ``acc += x`` is both a write and a read of ``acc``.
    assert "acc" in _def_names(fn)
    assert "acc" in _use_names(fn)


def test_java_for_and_while_heads():
    fn = _first(
        "java",
        """
        class Demo {
            int loops(int n) {
                int sum = 0;
                for (int j = 0; j < n; j++) {
                    sum += j;
                }
                while (sum < 100) {
                    sum *= 2;
                }
                return sum;
            }
        }
        """,
    )
    assert "j" in _def_names(fn)  # bound by the C-style for initializer
    headers = [b for b in fn.cfg.blocks if b.kind == "loop_header"]
    assert len(headers) == 2
    assert fn.cfg.back_edges()


def test_java_else_if_chain_branches():
    fn = _first(
        "java",
        """
        class Demo {
            int grade(int x) {
                int y = 0;
                if (x == 1) {
                    y = 1;
                } else if (x == 2) {
                    y = 2;
                } else if (x == 3) {
                    y = 3;
                } else {
                    y = 4;
                }
                return y;
            }
        }
        """,
    )
    branches = [b for b in fn.cfg.blocks if b.kind == "branch"]
    assert len(branches) == 3
    assert fn.cfg.exit_id in fn.cfg.reachable_ids()


def test_java_switch_arm_writes_are_may_defs():
    # A ``switch`` stays one CFG statement, so an arm's write is conditional:
    # it must register as BOTH a def and a use (the may-def convention that
    # keeps the promotion proof conservative).
    fn = _first(
        "java",
        """
        class Demo {
            int f(int x) {
                int y = 0;
                switch (x) {
                    case 1:
                        y = 10;
                        break;
                    default:
                        y = 20;
                }
                return y;
            }
        }
        """,
    )
    decl_line = min(d.line for d in fn.def_use.definitions if d.var == "y")
    switch_defs = [d for d in fn.def_use.definitions if d.var == "y" and d.line > decl_line]
    assert switch_defs, "expected the arm writes to register as defs"
    use_lines = {u.line for b in fn.def_use.blocks.values() for u in b.uses if u.name == "y"}
    assert {d.line for d in switch_defs} <= use_lines  # ...and as paired uses


# A long Java method whose compute-average tail is a clean extraction.
_JAVA_PROCESS = """
class Demo {
    int process(int[] records, int threshold) {
        int errors = 0;
        java.util.List<Integer> results = new java.util.ArrayList<>();
        for (int r : records) {
            if (r < 0) {
                errors++;
                continue;
            }
            results.add(r);
        }
        int total = 0;
        int count = 0;
        for (int v : results) {
            if (v > threshold) {
                total += v;
                count++;
            } else {
                total -= v;
            }
        }
        int average = 0;
        if (count > 0) {
            average = total / count;
        }
        return average + errors;
    }
}
"""


def test_java_extract_method_fires():
    lmap = get_language_map("java")
    extractions = find_extractions(_first("java", _JAVA_PROCESS), lmap)
    assert extractions, "expected at least one Java extraction"
    best = extractions[0]
    assert "average" in best.returns
    assert "results" in best.params and "threshold" in best.params
    assert len(best.returns) <= 1
    assert best.ccn_removed >= 1
    assert best.slice_nloc >= 6


def test_java_extractions_are_deterministic():
    lmap = get_language_map("java")
    fn = _first("java", _JAVA_PROCESS)

    def serialize():
        return [
            (e.start_line, e.end_line, e.params, e.returns, e.ccn_removed)
            for e in find_extractions(fn, lmap)
        ]

    first = serialize()
    for _ in range(3):
        assert serialize() == first


def test_java_flagged_only_gate_skips_small_functions():
    _require("java")
    lines = ["class Demo {", "    int big(int x) {", "        int y = 0;"]
    for i in range(12):
        lines += [f"        if (x == {i}) {{", f"            y = {i};", "        }"]
    lines += ["        return y;", "    }", "    int tiny() {", "        return 1;", "    }", "}"]
    result = build_cfgs_for_file("m.java", "java", "\n".join(lines).encode())
    if result.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for java")
    assert result.stats.functions_seen == 2
    assert result.stats.functions_built == 1
    assert [fc.name for fc in result.functions] == ["big"]


def test_java_append_loop_is_independent():
    assert _independent(
        "java",
        """
        class Demo {
            void f(int[] items) {
                for (int item : items) {
                    int r = fetch(item);  // HIT
                    store(r);
                }
            }
        }
        """,
        "HIT",
    )


def test_java_accumulator_is_carried():
    assert not _independent(
        "java",
        """
        class Demo {
            int f(int[] items) {
                int acc = 0;
                for (int item : items) {
                    acc = acc + fetch(item);  // HIT
                }
                return acc;
            }
        }
        """,
        "HIT",
    )


def test_java_switch_conditional_write_refuses_promotion():
    # ``flag`` is written only on one switch arm, so it may carry across
    # iterations; the may-def convention must keep this refused.
    assert not _independent(
        "java",
        """
        class Demo {
            void f(int[] items) {
                int flag = 0;
                for (int item : items) {
                    switch (item) {
                        case 1:
                            flag = 1;
                            break;
                        default:
                            break;
                    }
                    int r = fetch(flag);  // HIT
                    store(r);
                }
            }
        }
        """,
        "HIT",
    )


# == Rust =======================================================================

# Every Rust write shape in one function: ``let`` (plain / mut / tuple / struct
# / slice patterns), ``=`` vs ``+=`` (distinct node kinds), field / index /
# deref targets (not locals), a ``for`` binder, and paths (``Vec::new``).
_RUST_SHAPES = """
struct S { count: i64 }

impl S {
    fn shapes(&mut self, input: &[i64], base: i64) -> i64 {
        let total = 0;
        let mut acc = base;
        let (a, b) = (1, 2);
        let Wrapper { x, y } = wrapper;
        let [head, tail] = pair;
        acc += a;
        acc = acc * 2;
        self.count += 1;
        arr[0] = acc;
        obj.field = b;
        *ptr = x;
        let v = Vec::new();
        for item in input {
            acc += item + y + head + tail + total;
        }
        acc
    }
}
"""


def test_rust_def_use_classification():
    fn = _first("rust", _RUST_SHAPES)
    defs = _def_names(fn)
    # ``let`` binders across pattern shapes, plus the for binder, are locals.
    assert {"total", "acc", "a", "b", "x", "y", "head", "tail", "v", "item"} <= defs
    assert {"input", "base", "self"} <= defs  # params + the self receiver
    # Field / index / deref targets bind no local: bases are reads, never defs.
    assert "count" not in defs
    assert "arr" not in defs
    assert "obj" not in defs
    assert "ptr" not in defs
    # The tuple-struct/struct pattern *type* side is not a binder.
    assert "Wrapper" not in defs
    uses = _use_names(fn)
    assert {"base", "acc", "self", "arr", "obj", "ptr", "item", "wrapper"} <= uses
    # Path components (``Vec::new``) are never variable reads.
    assert "Vec" not in uses
    assert "new" not in uses


def test_rust_while_let_binder_is_a_may_def():
    fn = _first(
        "rust",
        """
        fn drain(iter: &mut I) -> i64 {
            let mut n = 0;
            while let Some(item) = iter.next() {
                n += item;
            }
            n
        }
        """,
    )
    # The pattern binds ``item`` only when it matches: def AND paired use.
    assert "item" in _def_names(fn)
    assert "item" in _use_names(fn)
    assert [b for b in fn.cfg.blocks if b.kind == "loop_header"]


def test_rust_else_if_chain_branches():
    fn = _first(
        "rust",
        """
        fn grade(x: i64) -> i64 {
            let mut y = 0;
            if x == 1 {
                y = 1;
            } else if x == 2 {
                y = 2;
            } else if x == 3 {
                y = 3;
            } else {
                y = 4;
            }
            y
        }
        """,
    )
    branches = [b for b in fn.cfg.blocks if b.kind == "branch"]
    assert len(branches) == 3
    assert fn.cfg.exit_id in fn.cfg.reachable_ids()


def test_rust_statement_position_control_flow_is_unwrapped():
    # Rust parses statement-position loops / returns inside an
    # ``expression_statement``; the CFG builder must classify the real node.
    fn = _first(
        "rust",
        """
        fn f(items: &[i64], x: i64) -> i64 {
            let mut total = 0;
            for it in items {
                if *it > x {
                    total += it;
                } else {
                    total -= it;
                }
                if *it == 0 {
                    continue;
                }
            }
            return total;
        }
        """,
    )
    assert [b for b in fn.cfg.blocks if b.kind == "loop_header"]
    assert len([b for b in fn.cfg.blocks if b.kind == "branch"]) == 2
    assert fn.cfg.back_edges()


# A long Rust function whose compute-average tail is a clean extraction.
_RUST_PROCESS = """
fn process(records: &[i64], threshold: i64) -> (i64, i64) {
    let mut results = Vec::new();
    let mut errors = 0;
    for r in records {
        if *r < 0 {
            errors += 1;
            continue;
        }
        results.push(*r);
    }
    let mut total = 0;
    let mut count = 0;
    for v in &results {
        if *v > threshold {
            total += v;
            count += 1;
        } else {
            total -= v;
        }
    }
    let mut average = 0;
    if count > 0 {
        average = total / count;
    }
    (average, errors)
}
"""


def test_rust_extract_method_fires():
    lmap = get_language_map("rust")
    extractions = find_extractions(_first("rust", _RUST_PROCESS), lmap)
    assert extractions, "expected at least one Rust extraction"
    best = extractions[0]
    assert "average" in best.returns
    assert "results" in best.params and "threshold" in best.params
    assert len(best.returns) <= 1
    assert best.ccn_removed >= 1
    assert best.slice_nloc >= 6


def test_rust_extractions_never_cover_the_tail_expression():
    # The final ``(average, errors)`` tail is the function's value; a span
    # ending on it would silently drop that value, so none is ever offered.
    fn = _first("rust", _RUST_PROCESS)
    lmap = get_language_map("rust")
    tail_line = fn.end_line - 1  # the tuple expression before the closing brace
    for e in find_extractions(fn, lmap):
        assert e.end_line < tail_line


def test_rust_extractions_are_deterministic():
    lmap = get_language_map("rust")
    fn = _first("rust", _RUST_PROCESS)

    def serialize():
        return [
            (e.start_line, e.end_line, e.params, e.returns, e.ccn_removed)
            for e in find_extractions(fn, lmap)
        ]

    first = serialize()
    for _ in range(3):
        assert serialize() == first


def test_rust_question_mark_span_has_no_extraction():
    # ``?`` propagates an error out of the function -- an early exit that makes
    # any span containing it unsafe to lift; every candidate here carries one.
    lmap = get_language_map("rust")
    src = """
        fn load(paths: &[String], threshold: i64) -> Result<i64, E> {
            let mut total = 0;
            let mut count = 0;
            for p in paths {
                let data = read(p)?;
                if data.len() > threshold {
                    total += parse(&data)?;
                    count += 1;
                }
            }
            let mut average = 0;
            if count > 0 {
                average = total / count;
                check(average)?;
            }
            Ok(average)
        }
        """
    assert find_extractions(_first("rust", src), lmap) == []


def test_rust_flagged_only_gate_skips_small_functions():
    _require("rust")
    lines = ["fn big(x: i64) -> i64 {", "    let mut y = 0;"]
    for i in range(12):
        lines += [f"    if x == {i} {{", f"        y = {i};", "    }"]
    lines += ["    y", "}", "", "fn tiny() -> i64 {", "    1", "}", ""]
    result = build_cfgs_for_file("m.rs", "rust", "\n".join(lines).encode())
    if result.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for rust")
    assert result.stats.functions_seen == 2
    assert result.stats.functions_built == 1
    assert [fc.name for fc in result.functions] == ["big"]


def test_rust_push_loop_is_independent():
    assert _independent(
        "rust",
        """
        fn f(items: &[i64]) -> Vec<i64> {
            let mut out = Vec::new();
            for item in items {
                let r = fetch(item);  // HIT
                out.push(r);
            }
            out
        }
        """,
        "HIT",
    )


def test_rust_accumulator_is_carried():
    assert not _independent(
        "rust",
        """
        fn f(items: &[i64]) -> i64 {
            let mut acc = 0;
            for item in items {
                acc = acc + fetch(item);  // HIT
            }
            acc
        }
        """,
        "HIT",
    )


def test_rust_match_conditional_write_refuses_promotion():
    # ``flag`` is written only on one match arm (a may-def inside the mega-
    # statement), so it may carry across iterations: must stay refused.
    assert not _independent(
        "rust",
        """
        fn f(items: &[i64]) {
            let mut flag = 0;
            for item in items {
                match item {
                    1 => flag = 1,
                    _ => {}
                }
                let r = fetch(flag);  // HIT
                store(r);
            }
        }
        """,
        "HIT",
    )


# == C++ ========================================================================

# Every C++ write shape in one function: declaration (with and without an
# initialiser, through pointer / reference / array declarators), plain and
# compound assignment, ``++``, the range-``for`` binder, the C-style ``for``
# initializer -- plus the target shapes that bind NO local.
_CPP_SHAPES = """
    int total(const std::vector<int>& input, int base, Config* cfg) {
        int acc = base;
        int a = 1, b = 2;
        int uninit;
        int* ptr = nullptr;
        int& ref = acc;
        int arr[8];
        for (const auto& item : input) {
            acc += item;
        }
        for (int i = 0; i < 10; i++) {
            acc = acc + i;
        }
        cfg->count = acc;
        obj.field = acc;
        arr[a] = b;
        *ptr = acc;
        acc++;
        auto lam = [&](int z) { return z + hidden; };
        return acc;
    }
"""


def test_cpp_def_use_classification():
    fn = _first("cpp", _CPP_SHAPES)
    defs = _def_names(fn)
    # Declarations (initialised or not), through every declarator wrapper, plus
    # the two loop binders, are locals.
    assert {"acc", "a", "b", "uninit", "ptr", "ref", "arr", "item", "i", "lam"} <= defs
    assert {"input", "base", "cfg"} <= defs  # parameters seeded as entry defs
    # Field / subscript / deref targets bind no local: their bases are reads.
    assert "count" not in defs
    assert "obj" not in defs
    assert "field" not in defs
    uses = _use_names(fn)
    assert {"base", "acc", "cfg", "obj", "item", "input", "i"} <= uses
    # A type is never a variable read, and neither is a called function's name.
    assert "vector" not in uses
    assert "Config" not in uses


def test_cpp_reads_a_member_callee_receiver_but_not_the_method():
    fn = _first(
        "cpp",
        """
        int run(Database& db, int id) {
            int n = 0;
            n += db.execute(id);
            n += helper(id);
            return n;
        }
        """,
    )
    uses = _use_names(fn)
    assert "db" in uses  # the receiver is a real read
    assert "execute" not in uses  # the method name is not a variable
    assert "helper" not in uses  # nor is a free function's name


def test_cpp_lambda_body_is_a_separate_scope():
    fn = _first(
        "cpp",
        """
        int outer(int seed) {
            int kept = seed;
            auto lam = [&](int z) { return z + inner_only; };
            return kept;
        }
        """,
    )
    assert "inner_only" not in _use_names(fn)
    assert "z" not in _def_names(fn)


def test_cpp_range_for_and_c_for_build_loop_headers():
    fn = _first(
        "cpp",
        """
        int walk(const std::vector<int>& xs) {
            int n = 0;
            for (auto x : xs) { n += x; }
            for (int i = 0; i < 4; i++) { n += i; }
            while (n > 100) { n--; }
            return n;
        }
        """,
    )
    kinds = [b.kind for b in fn.cfg.blocks]
    assert kinds.count("loop_header") == 3
    assert "loop_exit" in kinds


def test_cpp_else_if_chain_branches():
    fn = _first(
        "cpp",
        """
        int pick(int x) {
            int r = 0;
            if (x > 10) { r = 1; }
            else if (x > 5) { r = 2; }
            else { r = 3; }
            return r;
        }
        """,
    )
    assert [b.kind for b in fn.cfg.blocks].count("branch") == 2


def test_cpp_try_catch_builds_a_handler_block():
    fn = _first(
        "cpp",
        """
        int guarded(int x) {
            int r = 0;
            try { r = risky(x); }
            catch (const std::exception& e) { r = -1; }
            return r;
        }
        """,
    )
    assert any(b.kind == "handler" for b in fn.cfg.blocks)


def test_cpp_switch_writes_are_may_defs():
    # A switch is one CFG statement, so an arm's write is recorded as a def AND
    # a use -- keeping the must-def reasoning conservative.
    fn = _first(
        "cpp",
        """
        int classify(int x) {
            int r = 0;
            switch (x) {
                case 1: r = 10; break;
                default: r = 20;
            }
            return r;
        }
        """,
    )
    assert "r" in _def_names(fn)
    assert "r" in _use_names(fn)


def test_cpp_extract_method_fires():
    fn = _first(
        "cpp",
        """
        int report(const std::vector<int>& xs, int base) {
            int sum = base;
            int count = 0;
            for (auto& item : xs) {
                if (item > 0) { sum += item; count++; }
                else if (item < -5) { sum -= item; }
                else { count--; }
            }
            int avg = count > 0 ? sum / count : 0;
            int scaled = avg * 2;
            int shifted = scaled + base;
            int clamped = shifted > 100 ? 100 : shifted;
            if (clamped < 0) { clamped = 0; }
            return clamped;
        }
        """,
    )
    lmap = get_language_map("cpp")
    assert lmap is not None
    extractions = find_extractions(fn, lmap)
    assert extractions
    # Deterministic ordering: re-running yields the identical best candidate.
    assert [(e.start_line, e.end_line) for e in find_extractions(fn, lmap)] == [
        (e.start_line, e.end_line) for e in extractions
    ]


def test_cpp_extraction_never_spans_a_jump():
    # ``break`` / ``continue`` / ``return`` / ``throw`` all have dedicated node
    # types in tree-sitter-cpp, so the slicer's single-exit gate sees them.
    lmap = get_language_map("cpp")
    assert lmap is not None
    assert {"break_statement", "continue_statement"} <= lmap.break_kinds | lmap.continue_kinds
    assert "return_statement" in lmap.return_kinds
    assert "throw_statement" in lmap.raise_kinds


# == C# =========================================================================

# Every C# write shape in one method: declarations (int a = 1, b = 2), compound
# and plain assignment, ++/--, tuple deconstruction, using declaration and
# statement, is-pattern, switch pattern, foreach binder, C-style for, indexer and
# member targets (not locals), out/ref args, lambda/local func, and try/catch.
_CSHARP_SHAPES = """
class Demo {
    int Shapes(int[] input, int baseVal, out int outResult, ref int refCount) {
        int total = 0;
        int a = 1, b = 2;
        int acc = baseVal;
        acc += a;
        acc++;
        --acc;
        (int p, int q) = (1, 2);
        (a, b) = (3, 4);
        using var x = open();
        using (var y = open()) {
            acc += 1;
        }
        if (obj is string s) {
            acc += s.Length;
        }
        switch (input) {
            case int[] arr:
                acc += arr.Length;
                break;
            default:
                break;
        }
        foreach (int r in input) {
            acc += r;
        }
        for (int i = 0; i < 10; i++) {
            acc += i;
        }
        int[] seen = new int[10];
        seen[a] = b;
        this.field = acc;
        obj.field = acc;
        DoOut(out var outLocal);
        DoOutT(out int outTLocal);
        DoOut(out outResult);
        DoRef(ref refCount);
        var lam = (int z) => z + acc;
        int LocalFunc(int w) => w + 1;
        try {
            acc += 1;
        } catch (Exception e) {
            acc -= 1;
        }
        return acc;
    }
}
"""


def test_csharp_def_use_classification():
    fn = _first("csharp", _CSHARP_SHAPES)
    defs = _def_names(fn)
    # Declarations, for/foreach binders, tuple deconstruction, using, out/ref args, pattern variables, and local function are locals.
    assert {
        "total",
        "a",
        "b",
        "acc",
        "p",
        "q",
        "x",
        "y",
        "s",
        "arr",
        "r",
        "i",
        "seen",
        "outLocal",
        "outTLocal",
        "outResult",
        "refCount",
        "lam",
        "LocalFunc",
    } <= defs
    assert {"input", "baseVal", "outResult", "refCount"} <= defs  # parameters
    # Field (``this.field = ...``) and indexer (``seen[a] = ...``) targets bind no local: bases are reads.
    assert "field" not in defs
    # Lambda parameter z and catch binder e are not defs of enclosing method.
    assert "z" not in defs
    uses = _use_names(fn)
    assert {"baseVal", "input", "seen", "a", "b", "acc", "obj", "refCount", "r", "s"} <= uses
    # Member names (Length) are never variable reads; bare callee identifiers (open, DoOut, DoRef)
    # are recorded as uses so delegate invocations (cb(n)) are captured as reads.
    assert "Length" not in uses
    assert {"open", "DoOut", "DoRef"} <= uses


def test_csharp_compound_assign_reads_target():
    fn = _first(
        "csharp",
        """
        class Demo {
            int f(int x) {
                int acc = 0;
                acc += x;
                return acc;
            }
        }
        """,
    )
    assert "acc" in _def_names(fn)
    assert "acc" in _use_names(fn)


def test_csharp_for_and_while_heads():
    fn = _first(
        "csharp",
        """
        class Demo {
            int loops(int n) {
                int sum = 0;
                for (int j = 0; j < n; j++) {
                    sum += j;
                }
                while (sum < 100) {
                    sum *= 2;
                }
                return sum;
            }
        }
        """,
    )
    assert "j" in _def_names(fn)
    headers = [b for b in fn.cfg.blocks if b.kind == "loop_header"]
    assert len(headers) == 2
    assert fn.cfg.back_edges()


def test_csharp_else_if_chain_branches():
    fn = _first(
        "csharp",
        """
        class Demo {
            int grade(int x) {
                int y = 0;
                if (x == 1) {
                    y = 1;
                } else if (x == 2) {
                    y = 2;
                } else if (x == 3) {
                    y = 3;
                } else {
                    y = 4;
                }
                return y;
            }
        }
        """,
    )
    branches = [b for b in fn.cfg.blocks if b.kind == "branch"]
    assert len(branches) == 3
    assert fn.cfg.exit_id in fn.cfg.reachable_ids()


def test_csharp_switch_arm_writes_are_may_defs():
    fn = _first(
        "csharp",
        """
        class Demo {
            int f(int x) {
                int y = 0;
                switch (x) {
                    case 1:
                        y = 10;
                        break;
                    default:
                        y = 20;
                        break;
                }
                return y;
            }
        }
        """,
    )
    decl_line = min(d.line for d in fn.def_use.definitions if d.var == "y")
    switch_defs = [d for d in fn.def_use.definitions if d.var == "y" and d.line > decl_line]
    assert switch_defs, "expected the arm writes to register as defs"
    use_lines = {u.line for b in fn.def_use.blocks.values() for u in b.uses if u.name == "y"}
    assert {d.line for d in switch_defs} <= use_lines


_CSHARP_PROCESS = """
class Demo {
    int Process(int[] records, int threshold) {
        int errors = 0;
        var results = new System.Collections.Generic.List<int>();
        foreach (int r in records) {
            if (r < 0) {
                errors++;
                continue;
            }
            results.Add(r);
        }
        int total = 0;
        int count = 0;
        foreach (int v in results) {
            if (v > threshold) {
                total += v;
                count++;
            } else {
                total -= v;
            }
        }
        int average = 0;
        if (count > 0) {
            average = total / count;
        }
        return average + errors;
    }
}
"""


def test_csharp_extract_method_fires():
    lmap = get_language_map("csharp")
    extractions = find_extractions(_first("csharp", _CSHARP_PROCESS), lmap)
    assert extractions, "expected at least one C# extraction"
    best = extractions[0]
    assert "average" in best.returns
    assert "results" in best.params and "threshold" in best.params
    assert len(best.returns) <= 1
    assert best.ccn_removed >= 1
    assert best.slice_nloc >= 6


def test_csharp_extractions_are_deterministic():
    lmap = get_language_map("csharp")
    fn = _first("csharp", _CSHARP_PROCESS)

    def serialize():
        return [
            (e.start_line, e.end_line, e.params, e.returns, e.ccn_removed)
            for e in find_extractions(fn, lmap)
        ]

    first = serialize()
    for _ in range(3):
        assert serialize() == first


def test_csharp_flagged_only_gate_skips_small_functions():
    _require("csharp")
    lines = ["class Demo {", "    int big(int x) {", "        int y = 0;"]
    for i in range(12):
        lines += [f"        if (x == {i}) {{", f"            y = {i};", "        }"]
    lines += ["        return y;", "    }", "    int tiny() {", "        return 1;", "    }", "}"]
    result = build_cfgs_for_file("m.cs", "csharp", "\n".join(lines).encode())
    if result.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for csharp")
    assert result.stats.functions_seen == 2
    assert result.stats.functions_built == 1
    assert [fc.name for fc in result.functions] == ["big"]


def test_csharp_append_loop_is_independent():
    assert _independent(
        "csharp",
        """
        class Demo {
            void f(int[] items) {
                foreach (int item in items) {
                    int r = fetch(item);  // HIT
                    store(r);
                }
            }
        }
        """,
        "HIT",
    )


def test_csharp_accumulator_is_carried():
    assert not _independent(
        "csharp",
        """
        class Demo {
            int f(int[] items) {
                int acc = 0;
                foreach (int item in items) {
                    acc = acc + fetch(item);  // HIT
                }
                return acc;
            }
        }
        """,
        "HIT",
    )


def test_csharp_switch_conditional_write_refuses_promotion():
    assert not _independent(
        "csharp",
        """
        class Demo {
            void f(int[] items) {
                int flag = 0;
                foreach (int item in items) {
                    switch (item) {
                        case 1:
                            flag = 1;
                            break;
                        default:
                            break;
                    }
                    int r = fetch(flag);  // HIT
                    store(r);
                }
            }
        }
        """,
        "HIT",
    )


# ---------------------------------------------------------------------------
# Nested scopes that bind a name in the enclosing scope (and the ones that do not)
# ---------------------------------------------------------------------------


def _defs(analysis) -> set[tuple[str, int]]:
    return {
        (definition.var, definition.line)
        for block in analysis.def_use.blocks.values()
        for definition in block.defs
    }


def test_python_nested_def_binds_its_name_in_the_enclosing_scope() -> None:
    """Without this a span calling a sibling closure omitted it from its IN set."""
    (analysis,) = [
        item
        for item in _analyses(
            "python",
            """
            def outer(rows):
                def helper(value):
                    return value + 1
                total = 0
                for row in rows:
                    total += helper(row)
                return total
            """,
        )
        if item.name == "outer"
    ]
    assert ("helper", 3) in _defs(analysis)


def test_a_named_function_expression_binds_nothing_outside_itself() -> None:
    """JS scopes a function expression's own name to its body.

    Recording it as an enclosing definition would shadow an unrelated variable
    of the same name - the ``var fib = function fib(n) {...}`` idiom - and the
    slicer would then believe the span had already redefined it and drop it
    from the IN set.
    """
    (analysis,) = [
        item
        for item in _analyses(
            "typescript",
            """
            function outer(rows) {
              let bar = 5;
              var f = function bar() { return 1; };
              console.log(bar);
              return f;
            }
            """,
        )
        if item.name == "outer"
    ]
    defined = _defs(analysis)
    assert ("bar", 3) in defined, "the real let-binding must still be a definition"
    assert ("bar", 4) not in defined, "the function expression's own name is not ours"


def test_a_function_declaration_does_bind_in_the_enclosing_scope() -> None:
    (analysis,) = [
        item
        for item in _analyses(
            "typescript",
            """
            function outer(rows) {
              function helper(v) { return v + 1; }
              let total = 0;
              for (const row of rows) { total += helper(row); }
              return total;
            }
            """,
        )
        if item.name == "outer"
    ]
    assert ("helper", 3) in _defs(analysis)


def test_anonymous_scope_dialects_declare_no_enclosing_binders() -> None:
    """Go, Java and C++ have no nested construct that binds a name here."""
    from repowise.core.analysis.health.dataflow.dialects import get_defuse_dialect

    for language in ("go", "java", "cpp"):
        dialect = get_defuse_dialect(language)
        if dialect is None:
            continue
        assert not dialect.enclosing_binder_kinds, language


def test_every_declared_binder_is_actually_a_scope_boundary() -> None:
    """A binder kind the walk never reaches would be silently dead."""
    from repowise.core.analysis.health.dataflow.dialects import get_defuse_dialect

    for language in ("python", "typescript", "go", "java", "rust", "cpp"):
        dialect = get_defuse_dialect(language)
        if dialect is None:
            continue
        for kind in dialect.enclosing_binder_kinds:
            node = type("N", (), {"type": kind})()
            assert dialect._is_scope_boundary(node), f"{language}: {kind}"


def test_hoisted_binding_precompute_matches_the_direct_test() -> None:
    """The per-function precompute must decide exactly what a per-span scan would.

    ``_hoisted_bindings`` exists to keep the check off the candidate loop, where
    scanning every variable per span cost the analyzer real time. An optimisation
    that also changed the answer would be a silent behaviour change, so the two
    formulations are compared over every span shape the corpus produces.
    """
    from repowise.core.analysis.health.dataflow import slice as slicing
    from repowise.core.analysis.health.dataflow.dialects import get_defuse_dialect

    def directly(def_lines, use_lines, s, e) -> bool:
        for var, defs in def_lines.items():
            if not any(s <= ln <= e for ln in defs):
                continue
            if any(ln < s for ln in defs):
                continue
            if any(ln < s for ln in use_lines.get(var, [])):
                return True
        return False

    from .refactoring_corpus_fixture import CASE_FILES, source_for

    compared = 0
    for language, filename in CASE_FILES:
        if get_defuse_dialect(language) is None:
            continue
        result = analyze_file(filename, language, source_for(filename), flagged_only=False)
        for analysis in result.functions:
            def_lines, use_lines = slicing._var_lines(analysis.def_use)
            hoisted = slicing._hoisted_bindings(def_lines, use_lines)
            for low in range(analysis.start_line, analysis.end_line + 1):
                for high in range(low, analysis.end_line + 1):
                    compared += 1
                    precomputed = any(low <= d <= high and u < low for d, u in hoisted)
                    assert precomputed == directly(def_lines, use_lines, low, high)
    assert compared, "the corpus produced no spans to compare"


def test_tsx_parses_without_error_recovery() -> None:
    """A .tsx file is tagged ``typescript`` and needs the JSX grammar here too.

    Read without it, every element lands in ERROR recovery and the CFG is built
    over a tree that does not describe the file.
    """
    from repowise.core.analysis.health.dataflow.parsing import parse_source

    source = b"export function C({ n }: P) {\n  return <div id={n}>{n + 1}</div>;\n}\n"
    parsed = parse_source("src/C.tsx", "typescript", source)
    if parsed is None:
        pytest.skip("tree-sitter typescript pack missing")
    root, _ = parsed
    assert not root.has_error
    # The same bytes under a .ts path keep the grammar the tag alone picks.
    as_ts = parse_source("src/C.ts", "typescript", source)
    assert as_ts is not None
    assert as_ts[0].has_error


# == A name the span itself declares is not a parameter =========================

# Two loops reuse the counter ``i`` and the span holds the second. Its header
# declares ``i`` and reads it on one line, and the first loop's ``i`` is a
# definition of the same name before the span, so the counter read as an input
# of the helper although the call site has no such variable.
_TWO_LOOPS = {
    "java": """
        class Demo {
            int run(int[] a, int n) {
                int sum = 0;
                for (int i = 0; i < n; i++) {
                    sum += a[i];
                }
                int total = 0;
                for (int i = 0; i < n; i++) {
                    if (a[i] > 0) {
                        total += a[i];
                    } else {
                        total -= a[i];
                    }
                }
                return sum + total;
            }
        }
        """,
    "typescript": """
        function run(a: number[], n: number): number {
            let sum = 0;
            for (let i = 0; i < n; i++) {
                sum += a[i];
            }
            let total = 0;
            for (let i = 0; i < n; i++) {
                if (a[i] > 0) {
                    total += a[i];
                } else {
                    total -= a[i];
                }
            }
            return sum + total;
        }
        """,
    "cpp": """
        int run(int* a, int n) {
            int sum = 0;
            for (int i = 0; i < n; i++) {
                sum += a[i];
            }
            int total = 0;
            for (int i = 0; i < n; i++) {
                if (a[i] > 0) {
                    total += a[i];
                } else {
                    total -= a[i];
                }
            }
            return sum + total;
        }
        """,
    "go": """
        package main
        func run(a []int, n int) int {
            sum := 0
            for i := 0; i < n; i++ {
                sum += a[i]
            }
            total := 0
            for i := 0; i < n; i++ {
                if a[i] > 0 {
                    total += a[i]
                } else {
                    total -= a[i]
                }
            }
            return sum + total
        }
        """,
    "csharp": """
        class Demo {
            int Run(int[] a, int n) {
                int sum = 0;
                for (int i = 0; i < n; i++) {
                    sum += a[i];
                }
                int total = 0;
                for (int i = 0; i < n; i++) {
                    if (a[i] > 0) {
                        total += a[i];
                    } else {
                        total -= a[i];
                    }
                }
                return sum + total;
            }
        }
        """,
}


@pytest.mark.parametrize("language", sorted(_TWO_LOOPS))
def test_a_counter_declared_by_the_spans_own_loop_header_is_not_a_parameter(language):
    lmap = get_language_map(language)
    extractions = find_extractions(_first(language, _TWO_LOOPS[language]), lmap)

    assert extractions, f"expected the second loop as a {language} extraction"
    assert [(x.params, x.returns) for x in extractions] == [(("a", "n"), ("total",))]


# The case the fix must not disturb: ``total`` is written and read on one line
# too, but there the read comes first, so the helper does need it handed in.
_READ_THEN_WRITE = {
    "java": """
        class Demo {
            int run(int[] a, int n, int total) {
                int sum = 0;
                for (int j = 0; j < n; j++) {
                    sum += a[j];
                }
                total = total + a[0];
                for (int k = 1; k < n; k++) {
                    if (a[k] > 0) {
                        total += a[k];
                    } else {
                        total -= a[k];
                    }
                }
                return sum + total;
            }
        }
        """,
    "typescript": """
        function run(a: number[], n: number, total: number): number {
            let sum = 0;
            for (let j = 0; j < n; j++) {
                sum += a[j];
            }
            total = total + a[0];
            for (let k = 1; k < n; k++) {
                if (a[k] > 0) {
                    total += a[k];
                } else {
                    total -= a[k];
                }
            }
            return sum + total;
        }
        """,
    "cpp": """
        int run(int* a, int n, int total) {
            int sum = 0;
            for (int j = 0; j < n; j++) {
                sum += a[j];
            }
            total = total + a[0];
            for (int k = 1; k < n; k++) {
                if (a[k] > 0) {
                    total += a[k];
                } else {
                    total -= a[k];
                }
            }
            return sum + total;
        }
        """,
    "go": """
        package main
        func run(a []int, n int, total int) int {
            sum := 0
            for j := 0; j < n; j++ {
                sum += a[j]
            }
            total = total + a[0]
            for k := 1; k < n; k++ {
                if a[k] > 0 {
                    total += a[k]
                } else {
                    total -= a[k]
                }
            }
            return sum + total
        }
        """,
    "csharp": """
        class Demo {
            int Run(int[] a, int n, int total) {
                int sum = 0;
                for (int j = 0; j < n; j++) {
                    sum += a[j];
                }
                total = total + a[0];
                for (int k = 1; k < n; k++) {
                    if (a[k] > 0) {
                        total += a[k];
                    } else {
                        total -= a[k];
                    }
                }
                return sum + total;
            }
        }
        """,
}


@pytest.mark.parametrize("language", sorted(_READ_THEN_WRITE))
def test_a_variable_read_before_its_same_line_write_stays_a_parameter(language):
    lmap = get_language_map(language)
    extractions = find_extractions(_first(language, _READ_THEN_WRITE[language]), lmap)

    assert [(x.params, x.returns) for x in extractions] == [(("a", "n", "total"), ("total",))]


def test_go_redeclaration_that_reads_the_outer_name_keeps_it_as_a_parameter():
    """``x := x + v`` in an inner scope declares a new ``x`` from the outer one:
    the read sits in the declaration's own initializer, before the new name
    exists, so the outer ``x`` is still an input."""
    fn = _first(
        "go",
        """
        package main
        func run(a []int, x int) int {
            sum := 0
            for j := 0; j < x; j++ {
                sum += a[j]
            }
            total := 0
            for _, v := range a {
                x := x + v
                if x > 0 {
                    total += x
                } else {
                    total -= x
                }
            }
            return sum + total
        }
        """,
    )

    extractions = find_extractions(fn, get_language_map("go"))

    assert [(x.params, x.returns) for x in extractions] == [(("a", "x"), ("total",))]


def test_a_local_declared_and_read_on_one_line_in_the_span_is_not_a_parameter():
    """The same shape without a loop header: ``t`` is declared and then read on
    one line inside the span, and an earlier block has its own ``t``."""
    fn = _first(
        "java",
        """
        class Demo {
            int run(int[] a, int n) {
                int sum = 0;
                for (int j = 0; j < n; j++) {
                    int t = a[j];
                    sum += t;
                }
                int total = 0;
                for (int k = 0; k < n; k++) {
                    int t = a[k]; total += t;
                    if (t > 0) {
                        total += 1;
                    } else {
                        total -= 1;
                    }
                }
                return sum + total;
            }
        }
        """,
    )

    extractions = find_extractions(fn, get_language_map("java"))

    assert extractions, "expected the second loop as an extraction"
    assert all("t" not in x.params for x in extractions)
    assert (("a", "n"), ("total",)) in [(x.params, x.returns) for x in extractions]


# == Reads inside nested closures ===============================================


def _covering(fn, lmap, first: int, last: int):
    return [e for e in find_extractions(fn, lmap) if e.start_line <= first and e.end_line >= last]


def test_go_closure_read_of_a_parameter_makes_it_a_param():
    # ``ps`` is read only inside the func literal; the span still needs it.
    src = """
        package main

        func load(m *Map, ps *Page) []int {
            key := ps.Path()
            if key == "/" {
                key = ""
            }
            v, err := m.cache.GetOrCreate(key, func(string) ([]int, error) {
                res := m.find(ps)
                if len(res) > 2 {
                    res = res[:2]
                }
                return res, nil
            })
            if err != nil {
                panic(err)
            }
            m.count++
            m.last = key
            m.seen = true
            return v
        }
        """
    lmap = get_language_map("go")
    spans = _covering(_first("go", src), lmap, 9, 18)
    assert spans
    assert all("ps" in e.params for e in spans)


def test_ts_closure_read_after_the_span_makes_a_return():
    # ``seen`` is read after the span only inside the arrow function.
    src = """
        function prune(messages: Msg[], limit: number): Msg[] {
            const seen = new Set<string>()
            for (const msg of messages) {
                if (msg.id && msg.size < limit) {
                    seen.add(msg.id)
                }
            }
            log(messages.length)
            log(limit)
            log(seen.size)
            return messages.filter((m) => seen.has(m.parent))
        }
        """
    lmap = get_language_map("typescript")
    spans = _covering(_first("typescript", src), lmap, 3, 8)
    assert spans
    assert all("seen" in e.returns for e in spans)


def test_ts_closure_parameter_is_not_a_read_of_the_outer_name():
    src = """
        function scale(items: number[], x: number): number[] {
            let out: number[] = []
            if (x > 1) {
                out = items.map((x) => x * 2)
                out.push(0)
            } else {
                out = items.slice()
            }
            log(out.length)
            log(items.length)
            log(out.length)
            return out
        }
        """
    lmap = get_language_map("typescript")
    spans = _covering(_first("typescript", src), lmap, 4, 9)
    assert spans
    assert all("x" in e.params for e in spans)  # the condition reads the outer x
    fn = _first("typescript", src)
    captured = {u.name for u in fn.def_use.captured}
    assert "x" not in captured and "items" not in captured


@pytest.mark.parametrize(
    ("language", "src", "captured", "not_captured"),
    [
        (
            "python",
            """
            def f(rows, k):
                total = 0
                def inner(v):
                    return v + total + k
                return list(map(lambda total: total * 2, rows)), inner
            """,
            {"total", "k"},
            {"v", "inner"},
        ),
        (
            "java",
            """
            class A {
                int f(java.util.List<Integer> rows, int k) {
                    int total = 1;
                    rows.forEach(v -> System.out.println(v + total + k));
                    return total;
                }
            }
            """,
            {"total", "k"},
            {"v"},
        ),
        (
            "rust",
            """
            fn f(rows: Vec<i32>, k: i32) -> i32 {
                let total = 1;
                let s: i32 = rows.iter().map(|v| v + total + k).sum();
                s
            }
            """,
            {"total", "k"},
            {"v"},
        ),
        (
            "typescript",
            """
            function f(rows: number[], k: number) {
                const total = 1
                return rows.map((v) => rows.filter((w) => w + v + total > k))
            }
            """,
            {"total", "k", "rows"},
            {"v", "w"},
        ),
    ],
)
def test_closure_reads_are_captured_per_language(language, src, captured, not_captured):
    fn = _first(language, src)
    names = {u.name for u in fn.def_use.captured}
    assert captured <= names
    assert not (not_captured & names)


def test_a_closure_local_shadowing_an_outer_name_is_not_a_capture():
    src = """
        function f(items: number[], t: number): number[] {
            let t2 = 0
            if (t > 1) {
                t2 = t * 3
                log(t2)
            }
            log(t)
            return items.map((v) => {
                const t2 = v * 2
                return t2 + t
            })
        }
        """
    fn = _first("typescript", src)
    assert "t2" not in {u.name for u in fn.def_use.captured}


# == Spans a helper cannot carry =================================================


def test_python_span_holding_a_yield_is_not_offered():
    src = """
        def rows(items, limit):
            seen = 0
            for item in items:
                if item > limit:
                    seen += 1
                    yield item
                else:
                    seen -= 1
            print(seen)
            print(limit)
            return seen
        """
    lmap = get_language_map("python")
    fn = _first("python", src)
    assert all(not (e.start_line <= 7 <= e.end_line) for e in find_extractions(fn, lmap))


def test_ts_span_holding_a_yield_is_not_offered():
    src = """
        function* chunks(items: string[], limit: number) {
            let seen = 0
            for (const item of items) {
                if (item.length > limit) {
                    seen += 1
                    yield item
                } else {
                    seen -= 1
                }
            }
            log(seen)
            log(limit)
            return seen
        }
        """
    lmap = get_language_map("typescript")
    fn = _first("typescript", src)
    assert all(not (e.start_line <= 7 <= e.end_line) for e in find_extractions(fn, lmap))


def test_rust_span_holding_an_exit_macro_is_not_offered():
    src = """
        fn paths(low: &mut Low, state: &State) -> anyhow::Result<Vec<String>> {
            let mut paths = Vec::new();
            for arg in low.positional.drain(..) {
                if state.stdin_consumed && arg == "-" {
                    anyhow::bail!("cannot read stdin twice");
                }
                paths.push(arg);
            }
            log::debug!("{}", paths.len());
            log::debug!("{}", state.stdin_consumed);
            Ok(paths)
        }
        """
    lmap = get_language_map("rust")
    fn = _first("rust", src)
    assert all(not (e.start_line <= 6 <= e.end_line) for e in find_extractions(fn, lmap))


# == May-def bookkeeping is not a read ===========================================


def test_rust_if_let_binder_inside_the_span_is_not_a_param():
    # Both ``if let`` arms bind their own ``var``; the first one's binding is
    # out of scope by the time the span runs, so it cannot be passed in.
    src = """
        fn flag_doc(flag: &Flag, out: &mut String) {
            if let Some(var) = flag.doc_variable() {
                out.push_str(var);
            }
            let name = flag.name_long();
            out.push_str(name);
            if let Some(var) = flag.doc_variable() {
                if var.len() > 3 {
                    out.push_str(var);
                }
            }
            out.push_str("\n");
            let doc = flag.doc_long();
            if doc.len() > 10 {
                out.push_str(doc);
            }
            out.push_str("\n");
            out.push_str("\n");
        }
        """
    lmap = get_language_map("rust")
    spans = [e for e in find_extractions(_first("rust", src), lmap) if e.start_line <= 8 <= e.end_line]
    assert spans
    assert all("var" not in e.params for e in spans)


def test_rust_let_inside_a_match_arm_is_not_a_param():
    src = """
        fn analyse(kind: &Kind, limit: usize) -> usize {
            match kind {
                Kind::One(items) => {
                    let mut total = items.len();
                    total
                }
                Kind::Many(items) => {
                    let mut total = 0;
                    for item in items.iter() {
                        if item.len() > limit {
                            total += limit;
                        } else {
                            total += item.len();
                        }
                    }
                    total
                }
            }
        }
        """
    lmap = get_language_map("rust")
    fn = _first("rust", src)
    spans = [e for e in find_extractions(fn, lmap) if e.start_line == 9]
    assert spans
    assert all("total" not in e.params for e in spans)


def test_rust_span_ending_on_a_consumed_tail_if_is_not_offered():
    # The ``else`` block's last ``if`` is its value, read by the ``let``.
    src = """
        fn matcher(names: &[String], dir: &str, case: bool) -> usize {
            let found = if names.is_empty() {
                0
            } else {
                let wanted: Vec<&String> = names.iter().filter(|n| n.len() > 2).collect();
                log(dir);
                log(case);
                if wanted.is_empty() {
                    0
                } else {
                    let m = wanted.len();
                    m + 1
                }
            };
            found
        }
        """
    lmap = get_language_map("rust")
    fn = _first("rust", src)
    assert all(e.end_line != 14 for e in find_extractions(fn, lmap))


def test_rust_span_ending_on_an_if_in_a_loop_body_is_still_offered():
    src = """
        fn tally(items: &[usize], limit: usize, out: &mut Vec<usize>) -> usize {
            let count = items.len();
            for item in items {
                let doubled = item * 2;
                log(doubled);
                log(limit);
                if doubled > limit {
                    out.push(limit);
                } else {
                    out.push(doubled);
                }
            }
            count
        }
        """
    lmap = get_language_map("rust")
    fn = _first("rust", src)
    assert any(e.end_line == 12 for e in find_extractions(fn, lmap))


def test_rust_match_arm_binders_are_params_of_a_span_in_the_arm():
    src = """
        fn class_regex(re: &mut String, tokens: &[Token]) {
            for tok in tokens.iter() {
                match *tok {
                    Token::Class { negated, ref ranges } => {
                        re.push('[');
                        if negated {
                            re.push('^');
                        }
                        for r in ranges {
                            if r.0 == r.1 {
                                re.push(r.0);
                            } else {
                                re.push(r.1);
                            }
                        }
                        re.push(']');
                    }
                    None => {}
                    std::i32::MAX => {}
                    consts::PI => {}
                    y if y > 2 => {}
                }
            }
        }
        """
    fn = _first("rust", src)
    defs = {(d.var, d.line) for d in fn.def_use.definitions}
    assert ("negated", 5) in defs and ("ranges", 5) in defs
    bogus = {"None", "Token", "Class", "std", "i32", "MAX", "consts", "PI"}
    assert not any(var in bogus for var, _ in defs)
    spans = [e for e in find_extractions(fn, get_language_map("rust")) if e.start_line <= 7]
    assert spans
    assert all({"negated", "ranges"} <= set(e.params) for e in spans)


# == A declaration the code after the span still needs ==========================


def test_go_span_declaring_a_name_assigned_after_it_is_not_offered():
    # ``var out []int`` moves with the span, but ``out = formats`` and the
    # return after it still name it; liveness alone saw no OUT.
    src = """
        package main

        func paths(formats []int, link bool) ([]int, map[int]int) {
            targets := make(map[int]int)
            for i, f := range formats {
                if f > 2 {
                    targets[f] = i
                } else {
                    targets[f] = -i
                }
            }
            var out []int
            if link {
                out = formats
            }
            return out, targets
        }
        """
    lmap = get_language_map("go")
    spans = find_extractions(_first("go", src), lmap)
    # Holding ``var out`` is fine only when the span returns ``out``.
    assert all("out" in e.returns for e in spans if e.start_line <= 13 <= e.end_line)
    assert any(e.end_line == 12 for e in spans)  # the loop alone is still offered


def test_go_name_declared_afresh_after_the_span_does_not_refuse_it():
    src = """
        package main

        func load(items []string) int {
            total := 0
            for _, it := range items {
                n, err := parse(it)
                if err == nil {
                    total += n
                }
                log(it)
                log(n)
            }
            for i := 0; i < total; i++ {
                if i%2 == 0 {
                    total--
                }
            }
            m, err := finish(total)
            if err != nil {
                return 0
            }
            return m
        }
        """
    lmap = get_language_map("go")
    fn = _first("go", src)
    assert any(e.start_line <= 7 and e.end_line >= 12 for e in find_extractions(fn, lmap))


def test_csharp_local_declared_and_read_on_one_line_in_the_span_is_not_a_parameter():
    fn = _first(
        "csharp",
        """
        class Demo {
            int Run(int[] a, int n) {
                int sum = 0;
                for (int j = 0; j < n; j++) {
                    int t = a[j];
                    sum += t;
                }
                int total = 0;
                for (int k = 0; k < n; k++) {
                    int t = a[k]; total += t;
                    if (t > 0) {
                        total += 1;
                    } else {
                        total -= 1;
                    }
                }
                return sum + total;
            }
        }
        """,
    )

    extractions = find_extractions(fn, get_language_map("csharp"))

    assert extractions, "expected the second loop as an extraction"
    assert all("t" not in x.params for x in extractions)
    assert (("a", "n"), ("total",)) in [(x.params, x.returns) for x in extractions]


def test_csharp_switch_arm_binder_inside_the_span_is_not_a_param():
    src = """
        class Demo {
            int Process(object obj, int limit) {
                switch (obj) {
                    case int i:
                        int armVal = i * 2;
                        log(armVal);
                        break;
                    default:
                        break;
                }
                int total = 0;
                for (int j = 0; j < limit; j++) {
                    total += j;
                }
                for (int k = 0; k < limit; k++) {
                    if (k > 0) {
                        total += k;
                    } else {
                        total -= k;
                    }
                }
                return total;
            }
        }
        """
    lmap = get_language_map("csharp")
    fn = _first("csharp", src)
    spans = find_extractions(fn, lmap)
    assert spans
    for s in spans:
        assert "armVal" not in s.params


def test_csharp_delegate_call_records_callee_as_read():
    src = """
        class Demo {
            int M(Func<int, int> cb, int n) {
                var r = 0;
                if (n > 0) {
                    r = cb(n);
                    r += 1;
                }
                return r;
            }
        }
        """
    fn = _first("csharp", src)
    from repowise.core.analysis.health.dataflow.slice import _infer_in_out, _var_lines

    dl, ul = _var_lines(fn.def_use)
    params, returns = _infer_in_out(dl, ul, 5, 8)
    assert "cb" in params
    assert "n" in params
    assert returns == ("r",)


def test_csharp_update_expression_inside_if_records_def():
    src = """
        class Demo {
            int M(int a) {
                int tryCount = 0;
                int waitDuration = 0;
                if (a > 0) {
                    tryCount++;
                    waitDuration = 5;
                }
                return tryCount + waitDuration;
            }
        }
        """
    fn = _first("csharp", src)
    lmap = get_language_map("csharp")
    from repowise.core.analysis.health.dataflow.slice import _infer_in_out, _var_lines

    dl, ul = _var_lines(fn.def_use)
    _, returns = _infer_in_out(dl, ul, 5, 8)
    assert "tryCount" in returns
    assert "waitDuration" in returns
    extractions = find_extractions(fn, lmap)
    assert not any(x.start_line <= 5 and x.end_line >= 8 for x in extractions)


def test_csharp_lambda_writing_outer_local_records_def():
    src = """
        class Demo {
            int M(int a) {
                int tryCount = 0;
                Action act = () => { tryCount++; };
                act();
                return tryCount;
            }
        }
        """
    fn = _first("csharp", src)
    lmap = get_language_map("csharp")
    from repowise.core.analysis.health.dataflow.slice import _infer_in_out, _var_lines

    dl, ul = _var_lines(fn.def_use)
    _, returns = _infer_in_out(dl, ul, 4, 5)
    assert "tryCount" in returns
    extractions = find_extractions(fn, lmap)
    assert not any(x.start_line <= 4 and x.end_line >= 5 for x in extractions)


