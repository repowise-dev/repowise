"""Rust ``unsafe_unwrap`` / ``panic_macro``: where a panic is the intended exit.

A panic in a crate's top-level ``fn main``, or anywhere in code that does not
ship (``build.rs``, ``examples/``, ``benches/``, a test file; decided by
``code_origin``), ends that one run, so it is no finding. An ``unwrap`` on a
literal input (``Regex::new(r"\\d+")``, ``"127.0.0.1:80".parse()``,
``NonZeroU8::new(7)``) panics on the first run or never. Library code and
non-literal inputs stay findings.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.error_handling import ErrorHandlingDetector
from repowise.core.analysis.health.complexity import walk_file


def _kinds(source: bytes) -> list[str]:
    return [h.kind for h in walk_file("<fixture>", "rust", source).error_handling_hits]


def _finding_kinds(path: str, source: bytes, origin: str | None = None) -> list[str]:
    hits = walk_file(path, "rust", source).error_handling_hits
    ctx = FileContext(
        file_path=path,
        language="rust",
        nloc=10,
        has_test_file=False,
        module=None,
        code_origin=origin,
        error_handling_hits=hits,
    )
    return [f.details["kind"] for f in ErrorHandlingDetector().detect(ctx)]


_LIB = b'pub fn f(p: &str) -> u32 {\n    let n = p.parse::<u32>().unwrap();\n    panic!("x");\n}\n'


@pytest.mark.parametrize(
    "body",
    [
        'let re = Regex::new(r"^\\d+$").unwrap();',
        'let re = regex::bytes::Regex::new("a|b").expect("valid regex");',
        'let addr: SocketAddr = "127.0.0.1:8080".parse().unwrap();',
        'let uri = "http://x/".parse::<Uri>().unwrap();',
        'let u = Url::parse("https://example.com").unwrap();',
        "let n = NonZeroU8::new(7).unwrap();",
        "let n = std::num::NonZeroUsize::new(1_024usize).unwrap();",
    ],
)
def test_unwrap_on_a_literal_input_is_no_hit(body: str) -> None:
    assert _kinds(f"pub fn f() {{\n    {body}\n}}\n".encode()) == []


@pytest.mark.parametrize(
    "body",
    [
        "let re = Regex::new(pattern).unwrap();",
        'let re = Regex::new(&format!("{}x", p)).unwrap();',
        "let addr: SocketAddr = s.parse().unwrap();",
        'let n = "8".repeat(width).parse::<u8>().unwrap();',
        'let h = "host".replace(x, y).parse::<Uri>().unwrap();',
        "let n = NonZeroU8::new(0).unwrap();",
        "let n = NonZeroU8::new(k).unwrap();",
    ],
)
def test_unwrap_on_a_runtime_input_stays_a_hit(body: str) -> None:
    assert _kinds(f"pub fn f() {{\n    {body}\n}}\n".encode()) == ["unsafe_unwrap"]


def test_top_level_main_is_no_hit() -> None:
    src = (
        b"fn main() {\n    let cfg = load().unwrap();\n"
        b'    let f = |x: &str| x.parse::<u8>().unwrap();\n    panic!("bye");\n}\n'
    )
    assert _kinds(src) == []


def test_main_elsewhere_or_helpers_stay_hits() -> None:
    src = (
        b"fn helper() { load().unwrap(); }\n"
        b"fn main() { fn inner() { load().unwrap(); } }\n"
        b"impl App { fn main(&self) { load().unwrap(); } }\n"
        b"mod m { fn main() { load().unwrap(); } }\n"
    )
    assert _kinds(src) == ["unsafe_unwrap"] * 4


@pytest.mark.parametrize(
    ("path", "origin"),
    [
        ("build.rs", None),
        ("examples/client.rs", None),
        ("crates/grep/examples/simple.rs", None),
        ("benches/bench.rs", None),
        ("tests/integration.rs", None),
        ("src/lib.rs", "build"),
        ("src/lib.rs", "docs_example"),
    ],
)
def test_unshipped_rust_code_has_no_panic_findings(path: str, origin: str | None) -> None:
    assert _finding_kinds(path, _LIB, origin) == []


@pytest.mark.parametrize(
    ("path", "origin"),
    [("src/lib.rs", None), ("src/lib.rs", "production"), ("src/commands/build.rs", None)],
)
def test_shipped_rust_code_keeps_panic_findings(path: str, origin: str | None) -> None:
    assert _finding_kinds(path, _LIB, origin) == ["unsafe_unwrap", "panic_macro"]
