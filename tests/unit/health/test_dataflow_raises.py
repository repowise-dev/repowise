"""Error exits on the dataflow pass, and the Extract Method precondition on them."""

from __future__ import annotations

import textwrap

import pytest

from repowise.core.analysis.health.dataflow import analyze_file
from repowise.core.analysis.health.dataflow.raises import (
    DEFER_MOVED,
    EXCEPTION_STATE,
    TRY_SPLIT,
    exception_flow_refusal,
    raises_in,
)


def _cfg(language: str, src: str):
    from repowise.core.ingestion.parser import _get_language

    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    res = analyze_file("m", language, textwrap.dedent(src).encode(), flagged_only=False)
    if not res.functions:
        pytest.skip(f"no dataflow for {language}")
    return res.functions[0].cfg


def _facts(language: str, src: str) -> list[tuple[int, str, str | None, int | None]]:
    return [(r.line, r.kind, r.exception, r.caught_by) for r in _cfg(language, src).raises]


def test_python_raises_rethrows_and_the_handlers_that_catch_them():
    src = """
    def f(x):
        try:
            if x:
                raise ValueError("a")
            raise KeyError
        except (ValueError, errors.Other) as e:
            raise
        except LookupError as d:
            raise d
        except Exception:
            raise RuntimeError() from None
        raise exc
    """
    assert _facts("python", src) == [
        (5, "raise", "ValueError", 7),
        # No subclass relations: KeyError reaches the root handler.
        (6, "raise", "KeyError", 11),
        (8, "rethrow", "ValueError | errors.Other", None),
        (10, "rethrow", "LookupError", None),
        (12, "raise", "RuntimeError", None),
        # A raised value has no known type; nothing encloses it anyway.
        (13, "raise", "exc", None),
    ]
    (region,) = _cfg("python", src).try_regions
    assert [(h.start_line, h.types, h.name, h.completes) for h in region.handlers] == [
        (7, ("ValueError", "errors.Other"), "e", False),
        (9, ("LookupError",), "d", False),
        (11, ("Exception",), None, False),
    ]


def test_python_exit_signals_pass_a_root_handler():
    src = """
    def f():
        try:
            raise SystemExit(1)
        except Exception:
            pass
    """
    assert _facts("python", src) == [(4, "raise", "SystemExit", None)]


def test_braceless_ts_throw_is_an_error_exit_a_catch_sees():
    src = """
    function f(x) {
      try {
        if (!x) throw new Error("a");
        g();
      } catch (err) {
        throw err;
      }
      throw new TypeError("b");
    }
    """
    assert _facts("typescript", src) == [
        (4, "raise", "Error", 6),
        (7, "rethrow", None, None),
        (9, "raise", "TypeError", None),
    ]


def test_a_raise_in_a_switch_arm_is_recorded_and_a_closure_raise_is_not():
    src = """
    function f(k) {
      try {
        switch (k) {
          case 1:
            throw new RangeError("a");
          default:
            g(() => { throw new Error("inner"); });
        }
      } catch (e) {}
    }
    """
    assert _facts("typescript", src) == [(6, "raise", "RangeError", 10)]


def test_try_inside_switch_arm_in_typescript_and_java():
    ts = """
    function f(k) {
      switch (k) {
        case 1:
          try {
            throw new RangeError("a");
          } catch (e) {
            throw e;
          }
        default:
          break;
      }
    }
    """
    assert _facts("typescript", ts) == [
        (6, "raise", "RangeError", 7),
        (8, "rethrow", None, None),
    ]
    (ts_region,) = _cfg("typescript", ts).try_regions
    assert ts_region.start_line == 5 and ts_region.end_line == 9

    java = """
    class A {
      void f(int k) {
        switch (k) {
          case 1:
            try {
              throw new IllegalArgumentException();
            } catch (IllegalArgumentException e) {
              throw e;
            }
        }
      }
    }
    """
    assert _facts("java", java) == [
        (7, "raise", "IllegalArgumentException", 8),
        (9, "rethrow", "IllegalArgumentException", None),
    ]
    (java_region,) = _cfg("java", java).try_regions
    assert java_region.start_line == 6 and java_region.end_line == 10


def test_try_inside_switch_arm_handler_with_if_else_exits():
    ts = """
    function f(k, c) {
      switch (k) {
        case 1:
          try {
            throw new RangeError("a");
          } catch (e) {
            if (c) {
              return 1;
            } else {
              throw e;
            }
          }
        default:
          break;
      }
    }
    """
    cfg = _cfg("typescript", ts)
    (region,) = cfg.try_regions
    (handler,) = region.handlers
    assert handler.completes is False



def test_try_inside_match_arm_in_python_and_extract_method_refusal():
    src = """
    def f(k):
        x = 0
        match k:
            case 1:
                try:
                    x = g(k)
                    if x:
                        x = h(x)
                        note(x)
                    log(x)
                    send(k)
                except Exception:
                    return x
            case _:
                pass
        return x
    """
    cfg = _cfg("python", src)
    (region,) = cfg.try_regions
    assert (region.start_line, region.end_line) == (6, 14)
    assert _refusal(src, 7, 12) == EXCEPTION_STATE
    assert _refusal(src, 6, 7) == TRY_SPLIT


def test_java_union_catch_and_csharp_bare_rethrow():
    java = """
    class A { void f() {
      try { if (x) throw new IOException("a"); }
      catch (IOException | RuntimeException e) { throw e; }
    } }
    """
    assert _facts("java", java) == [
        (3, "raise", "IOException", 4),
        (4, "rethrow", "IOException | RuntimeException", None),
    ]
    csharp = """
    class A { void F() {
      try { throw new ArgumentException("x"); }
      catch (InvalidOperationException e) { throw; }
      catch { }
    } }
    """
    assert _facts("csharp", csharp) == [
        (3, "raise", "ArgumentException", 5),
        (4, "rethrow", "InvalidOperationException", None),
    ]


def test_go_panic_and_rust_error_returns():
    go = """
    package p
    func f(x bool) {
        defer g()
        if x {
            panic("x")
        }
    }
    """
    cfg = _cfg("go", go)
    assert [(r.line, r.kind) for r in cfg.raises] == [(6, "panic")]
    assert cfg.defers == (4,)
    rust = """
    fn f(x: bool) -> Result<(), E> {
        g()?;
        if x {
            return Err(E::new());
        }
        panic!("x");
    }
    """
    assert [(r.line, r.kind) for r in _cfg("rust", rust).raises] == [
        (3, "error_return"),
        (5, "error_return"),
        (7, "panic"),
    ]


_TRY = """
def f(a, b):
    x = 0
    try:
        x = b + 1
        log(a)
        log(b)
    except Exception:
        log(x)
        raise
    return x
"""


def _refusal(src: str, s: int, e: int) -> str | None:
    from repowise.core.analysis.health.dataflow.slice import _reads_observing, _var_lines

    res = analyze_file("m.py", "python", textwrap.dedent(src).encode(), flagged_only=False)
    fa = res.functions[0]
    def_lines, _ = _var_lines(fa.def_use)
    return exception_flow_refusal(fa.cfg, s, e, _reads_observing(fa, def_lines), def_lines)


def test_a_write_the_handler_reads_stays_under_the_try():
    # Lines 5-7: a raise from ``log`` would leave the handler the old ``x``.
    assert _refusal(_TRY, 5, 7) == EXCEPTION_STATE
    # The write as the span's last line: nothing after it in the span raises.
    assert _refusal(_TRY, 6, 7) is None
    # The whole try moves with its handler.
    assert _refusal(_TRY, 3, 10) is None


def test_a_write_last_on_its_path_is_not_refused():
    src = """
    def f(a, b):
        x = 0
        try:
            if a:
                x = g(a)
            else:
                log(b)
        except Exception:
            log(x)
            raise
        return x
    """
    assert _refusal(src, 5, 8) is None


def test_a_cut_through_a_try_or_a_rethrow_outside_its_handler_is_refused():
    assert _refusal(_TRY, 5, 9) == TRY_SPLIT
    assert _refusal(_TRY, 9, 10) == TRY_SPLIT


def test_a_raise_that_only_propagates_is_kept():
    src = """
    def f(a):
        x = g(a)
        if not x:
            raise ValueError(a)
        y = h(x)
        return y
    """
    assert _refusal(src, 3, 6) is None
    res = analyze_file("m.py", "python", textwrap.dedent(src).encode(), flagged_only=False)
    assert [r.kind for r in raises_in(res.functions[0].cfg, 3, 6)] == ["raise"]


def test_a_go_span_holding_defer_is_refused_and_counted():
    from repowise.core.analysis.health.complexity.languages import get_language_map
    from repowise.core.analysis.health.dataflow.slice import find_extractions

    src = """
    package p
    func f(a []int, b int) int {
        n := 0
        mu.Lock()
        defer mu.Unlock()
        for _, v := range a {
            if v > b {
                n += v
            }
        }
        if n > 10 {
            n = 10
        }
        log(n)
        return n
    }
    """
    cfg = _cfg("go", src)
    assert cfg.defers == (6,)
    res = analyze_file("m.go", "go", textwrap.dedent(src).encode(), flagged_only=False)
    refused: set[str] = set()
    spans = find_extractions(res.functions[0], get_language_map("go"), refused=refused)
    assert not [x for x in spans if x.start_line <= 6 <= x.end_line]
    assert refused == {DEFER_MOVED}


def test_python_handler_types_come_from_the_dialect():
    src = """
    def f():
        try:
            g()
        except Foo:
            pass
        except:
            pass
    """
    (region,) = _cfg("python", src).try_regions
    assert [h.types for h in region.handlers] == [("Foo",), None]


def test_a_csharp_filtered_catch_is_not_counted_as_catching():
    src = """
    class A { void F() {
      try { throw new InvalidOperationException("x"); }
      catch (InvalidOperationException e) when (e.Message == "y") { }
    } }
    """
    assert _facts("csharp", src) == [(3, "raise", "InvalidOperationException", None)]


@pytest.mark.parametrize("language", ["typescript", "java", "csharp", "cpp"])
def test_a_braceless_return_leaves_the_if_without_reaching_its_join(language):
    src = {
        "typescript": "function f(x) {\n  if (x) return;\n  g();\n}\n",
        "java": "class A { void f(int x) {\n  if (x > 0) return;\n  g();\n} }\n",
        "csharp": "class A { void F(int x) {\n  if (x > 0) return;\n  G();\n} }\n",
        "cpp": "void f(int x) {\n  if (x) return;\n  g();\n}\n",
    }[language]
    cfg = _cfg(language, src)
    (arm,) = [b for b in cfg.blocks if any(s.start_line == 2 and not s.is_head for s in b.statements)]
    assert arm.statements[-1].kind == "return_statement"
    assert arm.successors == [cfg.exit_id]


_NESTED = """
def f(a, b):
    x = 0
    try:
        try:
            g(a)
        except ValueError:
            x = b + 1
            log(a)
            log(b)
    finally:
        log(x)
    return x
"""


def test_a_span_in_an_inner_handler_keeps_a_write_the_outer_finally_reads():
    # The span sits in the inner handler, not a ``try`` body: a raise from
    # ``log`` after the write would leave the outer ``finally`` the old ``x``.
    assert _refusal(_NESTED, 8, 10) == EXCEPTION_STATE
    assert _refusal(_NESTED, 9, 10) is None


def test_a_span_in_an_else_clause_keeps_a_write_its_finally_reads():
    src = """
    def f(a, b):
        x = 0
        try:
            g(a)
        except ValueError:
            return 0
        else:
            x = b + 1
            log(a)
        finally:
            log(x)
        return x
    """
    assert _refusal(src, 9, 10) == EXCEPTION_STATE


def test_a_span_in_a_handler_nested_in_a_swallowing_try_keeps_a_write_read_after():
    src = """
    def f(a, b):
        x = 0
        try:
            try:
                g(a)
            except ValueError:
                x = b + 1
                log(a)
        except Exception:
            pass
        return x
    """
    assert _refusal(src, 8, 9) == EXCEPTION_STATE
