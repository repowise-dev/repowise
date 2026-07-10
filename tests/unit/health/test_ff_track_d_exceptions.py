"""Regression tests: honest error-handling rationale / narrowed triggers.

These cover four fixed false-flags in the ``error_handling`` biomarker:

* ``except Exception`` no longer inherits the bare-``except:`` "hides
  KeyboardInterrupt" claim — it is its own ``broad_except`` kind.
* Go's blank-identifier discard only fires when ``_`` sits in the trailing
  (conventional error) slot, so a leading ``_`` value-discard is not called an
  error swallow.
* Rust panic-family macros get their own "aborts unconditionally" rationale
  instead of the unwrap/expect "recoverable error into a crash" claim.
* ``.unwrap()`` / panic macros inside ``#[test]`` / ``#[cfg(test)]`` are the
  intended failure signal, not a smell, so they are suppressed.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.error_handling import (
    _REASONS,
    ErrorHandlingDetector,
)
from repowise.core.analysis.health.complexity import walk_file


def _grammar_available(language: str) -> bool:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        return False
    try:
        return _get_language(language) is not None
    except Exception:
        return False


def _kinds(language: str, source: bytes) -> list[str]:
    if not _grammar_available(language):
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return [h.kind for h in walk_file("<fixture>", language, source).error_handling_hits]


# --- M1: except Exception (broad) vs except: / BaseException (truly catch-all) ---


@pytest.mark.parametrize(
    "source",
    [
        b"try:\n    x()\nexcept Exception:\n    pass\n",
        b"try:\n    x()\nexcept Exception as e:\n    log(e)\n",
    ],
    ids=["except-Exception", "except-Exception-as"],
)
def test_except_exception_is_broad_not_bare(source: bytes) -> None:
    kinds = _kinds("python", source)
    assert "broad_except" in kinds
    assert "bare_except" not in kinds
    # The false KeyboardInterrupt claim must be gone from the broad rationale.
    assert "KeyboardInterrupt" not in _REASONS["broad_except"]


@pytest.mark.parametrize(
    "source",
    [
        b"try:\n    x()\nexcept:\n    pass\n",
        b"try:\n    x()\nexcept BaseException:\n    pass\n",
    ],
    ids=["bare-except", "except-BaseException"],
)
def test_bare_and_baseexception_stay_bare(source: bytes) -> None:
    kinds = _kinds("python", source)
    assert "bare_except" in kinds
    assert "broad_except" not in kinds
    # These genuinely swallow KeyboardInterrupt/SystemExit — the claim is true here.
    assert "KeyboardInterrupt" in _REASONS["bare_except"]


# --- R3: Go blank-identifier discard only in the trailing (error) slot ---


def test_go_leading_blank_value_discard_not_flagged() -> None:
    # ``_, err := f()`` keeps the error and discards the value — a leading ``_``
    # is not the error position, so it must not be called an error swallow.
    assert "go_swallow" not in _kinds("go", b"func f() { _, err := g(); _ = err }\n")


def test_go_comma_ok_lookup_not_flagged() -> None:
    # Go's comma-ok on a (T, bool) return: ``_, ok := os.LookupEnv(...)`` keeps
    # the bool and discards the value — no error exists to swallow.
    src = b'package p\nimport "os"\nfunc f() { _, ok := os.LookupEnv("PATH"); _ = ok }\n'
    assert "go_swallow" not in _kinds("go", src)


def test_go_trailing_blank_error_discard_still_flagged() -> None:
    # ``v, _ := f()`` discards the trailing (error) slot — a genuine swallow.
    assert "go_swallow" in _kinds("go", b"func f() { v, _ := g(); _ = v }\n")


# --- M7: Rust panic-family macros get their own honest rationale ---


def test_rust_panic_macro_is_its_own_kind() -> None:
    src = b'fn handle(x: i32) -> i32 { match x { 1 => 10, _ => unreachable!("inv") } }\n'
    kinds = _kinds("rust", src)
    assert "panic_macro" in kinds
    assert "unsafe_unwrap" not in kinds
    # The macro does not consume a Result/Option, so no "recoverable error" claim.
    assert "recoverable error" not in _REASONS["panic_macro"]


def test_rust_unwrap_still_unsafe_unwrap() -> None:
    kinds = _kinds("rust", b"fn f() { let x = g().unwrap(); }\n")
    assert "unsafe_unwrap" in kinds
    assert "panic_macro" not in kinds
    assert "recoverable error" in _REASONS["unsafe_unwrap"]


# --- M8: suppress unwrap / panic macros inside Rust tests ---


def test_rust_unwrap_in_test_fn_not_flagged() -> None:
    src = b'#[test]\nfn parses() { let cfg = parse("v.toml").unwrap(); }\n'
    assert _kinds("rust", src) == []


def test_rust_unwrap_in_cfg_test_mod_not_flagged() -> None:
    src = (
        b"#[cfg(test)]\nmod tests {\n"
        b'    #[test]\n    fn parses() { let cfg = parse("v.toml").unwrap(); }\n}\n'
    )
    assert _kinds("rust", src) == []


def test_rust_panic_macro_in_test_not_flagged() -> None:
    src = b'#[test]\nfn t() { if bad() { panic!("boom"); } }\n'
    assert _kinds("rust", src) == []


def test_rust_unwrap_in_tokio_test_not_flagged() -> None:
    # ``#[tokio::test]`` is a test-runner attribute even though ``#[test]`` is
    # not a literal substring of it.
    src = b'#[tokio::test]\nasync fn t() { let cfg = parse("v.toml").unwrap(); }\n'
    assert _kinds("rust", src) == []


def test_rust_unwrap_in_cfg_all_test_mod_not_flagged() -> None:
    src = (
        b'#[cfg(all(test, feature = "x"))]\nmod tests {\n'
        b'    fn parses() { let cfg = parse("v.toml").unwrap(); }\n}\n'
    )
    assert _kinds("rust", src) == []


def test_rust_unwrap_in_production_fn_still_flagged() -> None:
    assert _kinds("rust", b"fn prod() { let x = g().unwrap(); }\n") == ["unsafe_unwrap"]


def test_rust_unwrap_under_cfg_not_test_still_flagged() -> None:
    # ``#[cfg(not(test))]`` gates the non-test build — the unwrap is still a smell.
    src = b"#[cfg(not(test))]\nfn prod() { let x = g().unwrap(); }\n"
    assert _kinds("rust", src) == ["unsafe_unwrap"]


# --- new kinds flow through the detector without dropping / crashing ---


def test_detector_emits_reasons_for_new_kinds() -> None:
    from repowise.core.analysis.health.complexity import ErrorHandlingHit

    ctx = FileContext(
        file_path="a.py",
        language="python",
        nloc=20,
        has_test_file=False,
        module=None,
        error_handling_hits=[
            ErrorHandlingHit("broad_except", 3),
            ErrorHandlingHit("panic_macro", 5),
        ],
    )
    findings = ErrorHandlingDetector().detect(ctx)
    reasons = {f.details["kind"]: f.reason for f in findings}
    assert reasons["broad_except"] == _REASONS["broad_except"]
    assert reasons["panic_macro"] == _REASONS["panic_macro"]
    assert "KeyboardInterrupt" not in reasons["broad_except"]
