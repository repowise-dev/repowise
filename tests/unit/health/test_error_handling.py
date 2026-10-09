"""Unit tests for the error-handling anti-pattern detection.

The 24 fixtures below are ported verbatim from the bench-validated
detector (11 languages, precision-first). Each asserts the per-kind hit
counts the walker's whole-tree pass must produce. Like the other walker
tests, language-pack availability is best-effort: a missing tree-sitter
grammar skips rather than fails.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.error_handling import ErrorHandlingDetector
from repowise.core.analysis.health.complexity import ErrorHandlingHit, walk_file
from repowise.core.analysis.health.models import Severity

# (language, source, expected per-kind counts, note)
_FIXTURES = [
    (
        "python",
        b"try:\n    x()\nexcept Exception:\n    pass\n",
        {"swallowed_catch": 1, "broad_except": 1},
        "empty Exception catch -> swallowed + broad (cannot catch KeyboardInterrupt)",
    ),
    (
        "python",
        b"try:\n    x()\nexcept ValueError:\n    pass\n",
        {"swallowed_catch": 1},
        "empty specific catch -> swallowed, not bare",
    ),
    (
        "python",
        b"try:\n    x()\nexcept:\n    ...\n",
        {"swallowed_catch": 1, "bare_except": 1},
        "bare except + ellipsis body",
    ),
    (
        "python",
        b"try:\n    x()\nexcept ValueError as e:\n    logger.error(e)\n    raise\n",
        {},
        "handled + re-raised -> clean",
    ),
    (
        "python",
        b"try:\n    x()\nexcept ValueError:\n    return None\n",
        {},
        "specific catch with real handling -> clean",
    ),
    ("python", b"def f():\n    return 1\n", {}, "no try -> clean"),
    ("javascript", b"try { go(); } catch (e) {}\n", {"swallowed_catch": 1}, "empty JS catch"),
    (
        "javascript",
        b"try { go(); } catch (e) { console.error(e); }\n",
        {},
        "handled JS catch",
    ),
    (
        "typescript",
        b"try { go(); } catch (e) { /* ignore */ }\n",
        {"swallowed_catch": 1},
        "comment-only TS catch",
    ),
    (
        "java",
        b"class A { void m(){ try { go(); } catch (Exception e) {} } }\n",
        {"swallowed_catch": 1},
        "empty Java catch",
    ),
    (
        "java",
        b"class A { void m(){ try { go(); } catch (Exception e) { log(e); } } }\n",
        {},
        "handled Java catch",
    ),
    (
        "kotlin",
        b"fun f(){ try { go() } catch (e: Exception) {} }\n",
        {"swallowed_catch": 1},
        "empty Kotlin catch",
    ),
    (
        "kotlin",
        b"fun f(){ try { go() } catch (e: Exception) { log(e) } }\n",
        {},
        "handled Kotlin catch",
    ),
    (
        "csharp",
        b"class A{ void M(){ try { Go(); } catch (Exception e) {} } }\n",
        {"swallowed_catch": 1},
        "empty C# catch",
    ),
    (
        "csharp",
        b"class A{ void M(){ try { Go(); } catch (Exception e) { Log(e); } } }\n",
        {},
        "handled C# catch",
    ),
    (
        "cpp",
        b"void f(){ try { go(); } catch (std::exception& e) {} }\n",
        {"swallowed_catch": 1},
        "empty C++ catch",
    ),
    (
        "cpp",
        b"void f(){ try { go(); } catch (std::exception& e) { log(e); } }\n",
        {},
        "handled C++ catch",
    ),
    ("rust", b"fn f() { let x = g().unwrap(); }\n", {"unsafe_unwrap": 1}, "unwrap"),
    (
        "rust",
        b'fn f() { let x = g().expect("boom"); panic!("x"); }\n',
        {"unsafe_unwrap": 1, "panic_macro": 1},
        "expect -> unsafe_unwrap, panic! -> panic_macro",
    ),
    (
        "rust",
        b"fn f() -> Result<i32,E> { let x = g()?; Ok(x) }\n",
        {},
        "? operator -> clean",
    ),
    (
        "go",
        b"func f() { v, _ := g(); _ = v }\n",
        {"go_swallow": 1},
        "blank discard of call return",
    ),
    ("go", b"func f() { if err != nil {} }\n", {"go_swallow": 1}, "empty if-err block"),
    ("go", b"func f() { if err != nil { return err } }\n", {}, "handled if-err"),
    (
        "go",
        b"func f() { x := g(); _ = x }\n",
        {},
        "single assign, no multi-return discard",
    ),
]


def _grammar_available(language: str) -> bool:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        return False
    try:
        return _get_language(language) is not None
    except Exception:
        return False


def _hits(language: str, source: bytes) -> list[ErrorHandlingHit]:
    if not _grammar_available(language):
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return walk_file("<fixture>", language, source).error_handling_hits


@pytest.mark.parametrize(
    ("language", "source", "expected", "note"),
    _FIXTURES,
    ids=[f"{lang}-{note}" for lang, _, _, note in _FIXTURES],
)
def test_detector_fixture(language: str, source: bytes, expected: dict, note: str):
    hits = _hits(language, source)
    by_kind: dict[str, int] = {}
    for h in hits:
        by_kind[h.kind] = by_kind.get(h.kind, 0) + 1
    assert by_kind == expected, f"{note}: expected {expected}, got {by_kind}"


def test_hits_carry_line_anchors():
    hits = _hits("python", b"x = 1\ntry:\n    x()\nexcept Exception:\n    pass\n")
    assert hits, "expected hits on the swallowed bare-Exception catch"
    assert all(h.line == 4 for h in hits)


def test_biomarker_emits_one_low_finding_per_hit():
    ctx = FileContext(
        file_path="a.py",
        language="python",
        nloc=20,
        has_test_file=False,
        module=None,
        error_handling_hits=[
            ErrorHandlingHit("swallowed_catch", 4),
            ErrorHandlingHit("bare_except", 4),
        ],
    )
    findings = ErrorHandlingDetector().detect(ctx)
    assert len(findings) == 2
    assert all(f.biomarker_type == "error_handling" for f in findings)
    assert all(f.severity == Severity.LOW for f in findings)
    assert findings[0].line_start == findings[0].line_end == 4
    assert {f.details["kind"] for f in findings} == {"swallowed_catch", "bare_except"}


def test_biomarker_no_hits_no_findings():
    ctx = FileContext(
        file_path="a.py",
        language="python",
        nloc=20,
        has_test_file=False,
        module=None,
    )
    assert ErrorHandlingDetector().detect(ctx) == []


# Rust unwraps that provably cannot panic are no finding; the controls beside
# each stay findings.
_RUST_CANNOT_PANIC = [
    (b"fn f(x: Option<u8>) { if x.is_some() { g(x.unwrap()); } }\n", 0, "is_some guard"),
    (b"fn f(x: Option<u8>) { if x.is_some() && h() { g(x.unwrap()); } }\n", 0, "guard conjunct"),
    (b"fn f(x: Option<u8>) { if x.is_some() || h() { g(x.unwrap()); } }\n", 1, "|| is no guard"),
    (b"fn f(x: Option<u8>) { if !x.is_some() { g(x.unwrap()); } }\n", 1, "negated guard"),
    (b"fn f(x: Option<u8>) { if x.is_some() { } else { g(x.unwrap()); } }\n", 1, "else branch"),
    (b"fn f(x: Option<u8>) { if y.is_some() { g(x.unwrap()); } }\n", 1, "other receiver"),
    (b"fn f(v: Vec<u8>) { if !v.is_empty() && v.last().unwrap() == &0 { } }\n", 0, "non-empty"),
    (b"fn f(v: Vec<u8>) { if v.is_empty() && v.last().unwrap() == &0 { } }\n", 1, "empty check"),
    (
        b"fn f(s: &S) { if s.r.is_some() { let r = (*s.r).as_ref().map(|r| r).unwrap(); } }\n",
        0,
        "guard through as_ref/map/deref",
    ),
    (
        b'fn f() -> String { let mut s = String::new(); writeln!(s, "a").unwrap(); s }\n',
        0,
        "write into a String",
    ),
    (
        b'fn f(s: &mut String) { write!(&mut s, "a").unwrap(); }\n',
        0,
        "write into a &mut String parameter",
    ),
    (
        b'fn f() { let mut o = io::stdout(); writeln!(o, "a").unwrap(); }\n',
        1,
        "write into stdout can fail",
    ),
    (
        b"fn f() { let mut w = String::new(); { let mut w = io::stdout(); "
        b'writeln!(w, "a").unwrap(); } }\n',
        1,
        "a shadowing rebind of the String",
    ),
    (
        b'fn f(o: &mut File) { let c = |o: String| 1; writeln!(o, "a").unwrap(); }\n',
        1,
        "a closure parameter of the same name",
    ),
    (b"fn f(x: Option<u8>) { if x.is_some() { let x = h(); g(x.unwrap()); } }\n", 1, "rebind"),
    (b"fn f(mut x: Option<u8>) { if x.is_some() { x = h(); g(x.unwrap()); } }\n", 1, "assign"),
    (
        b"fn f(s: &mut S) { if s.v.is_some() { s.v = None; g(s.v.unwrap()); } }\n",
        1,
        "field assigned after the guard",
    ),
]


@pytest.mark.parametrize(
    ("source", "expected", "note"),
    _RUST_CANNOT_PANIC,
    ids=[note for _, _, note in _RUST_CANNOT_PANIC],
)
def test_rust_unwrap_that_cannot_panic_is_no_hit(source: bytes, expected: int, note: str):
    hits = [h for h in _hits("rust", source) if h.kind == "unsafe_unwrap"]
    assert len(hits) == expected, note


_RUST_IDIOMS = [
    (b"fn f(m: &Mutex<u8>) { let g = m.lock().unwrap(); }\n", "lock_poison"),
    (b"fn f(m: &RwLock<u8>) { let g = m.write().unwrap(); }\n", "lock_poison"),
    (b"fn f(h: JoinHandle<()>) { h.join().unwrap(); }\n", "thread_join"),
    (b'fn f() { g().expect("config is validated at load"); }\n', "invariant_expect"),
    (b"fn f() { g().expect(MSG); }\n", "invariant_expect"),
    (b'fn f(x: u8) { match x { 0 => {}, _ => unreachable!("x is 0") } }\n', "unreachable"),
    (b"fn f() { g().unwrap(); }\n", None),
    (b"fn f(r: &mut File) { r.read(&mut buf).unwrap(); }\n", None),
    (b'fn f() { g().expect(&format!("bad {}", 1)); }\n', None),
    (b'fn f() { panic!("boom"); }\n', None),
]


@pytest.mark.parametrize(("source", "idiom"), _RUST_IDIOMS)
def test_rust_idiomatic_assertions_are_labelled(source: bytes, idiom: str | None):
    hits = _hits("rust", source)
    assert len(hits) == 1
    assert hits[0].idiom == idiom


def test_rust_unwrap_anchors_on_the_unwrap_line_of_a_chain():
    hits = _hits("rust", b"fn f() {\n    let x = g()\n        .h()\n        .unwrap();\n}\n")
    assert [h.line for h in hits] == [4]


def test_idiomatic_hit_is_labelled_and_not_scored():
    ctx = FileContext(
        file_path="a.rs",
        language="rust",
        nloc=20,
        has_test_file=False,
        module=None,
        error_handling_hits=[
            ErrorHandlingHit("unsafe_unwrap", 3, "lock_poison"),
            ErrorHandlingHit("unsafe_unwrap", 5),
        ],
    )
    idiomatic, plain = ErrorHandlingDetector().detect(ctx)
    assert idiomatic.details == {"kind": "unsafe_unwrap", "idiom": "lock_poison", "deduction": 0.0}
    assert idiomatic.deduction == 0.0
    assert "idiomatic" in idiomatic.reason
    assert plain.details == {"kind": "unsafe_unwrap"}
    assert plain.deduction is None
    assert plain.reason == "unwrap/expect turns a recoverable error into a crash"
