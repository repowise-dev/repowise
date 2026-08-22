"""Rust inline test code must not trigger the centrality-gated perf markers.

The Phase-7b gate (``perf.gated.collect_centrality_gated``) decides whether a
function is a finding using only two things: the walker's per-function facts
(``PerfFnFacts``) and ``ranker.is_hot(path, func_start)`` — neither of which
knows a function is test-only. Idiomatic Rust tests live in the same source
file (a ``#[cfg(test)] mod tests`` block, or a bare ``#[test]`` fn), so a test
helper that opens a file or nests two nearly-identical loops reads exactly
like production code doing the same thing, and a hot/churny file (which a
well-tested one usually is) gated every one of them in.

``FileComplexity.rust_test_line_ranges`` (computed by
``complexity.walker._rust_test_line_ranges`` from the same parsed tree, no
extra parse) fixes this at the single choke point both gated markers share:
a fact whose ``func_start`` falls in a test range is skipped before the
hotness check runs at all. Measured on a real corpus: hot_path_sync_io
122 -> 56, with all 66 test-code findings removed and all 56 production
findings retained.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.perf import PerfRanker, collect_centrality_gated


class _PF:
    """Minimal stand-in for a ParsedFile (the gate only reads file_info.path)."""

    class _FI:
        def __init__(self, path):
            self.path = path

    def __init__(self, path):
        self.file_info = _PF._FI(path)


def _walked(path: str, language: str, src: str):
    return [(_PF(path), walk_file(path, language, src.encode()))]


def _always_hot() -> PerfRanker:
    # No graph, but every file is a git hotspot -> churny -> hot everywhere,
    # so the ONLY thing that can suppress a hit is the test-range filter.
    return PerfRanker(None, {"t.rs": {"is_hotspot": True}})


# A bare sync filesystem sink (loop_depth 0 -> hot_path_sync_io candidate)
# plus an all-pairs nested loop over the same collection
# (-> nested_loop_quadratic candidate) — the identical shape
# test_perf_phase7b.py's `_HOT_SRC` uses to prove the gate fires at all.
_HOT_BODY = (
    'std::fs::read("config.toml").unwrap();\n'
    "    for x in items {\n"
    "        for y in items {\n"
    "            let _ = (x, y);\n"
    "        }\n"
    "    }\n"
)

_PRODUCTION_FN = f"fn production(items: &[i32]) {{\n    {_HOT_BODY}}}\n"

_CFG_TEST_MOD = (
    "#[cfg(test)]\n"
    "mod tests {\n"
    "    use super::*;\n"
    "\n"
    f"    fn helper_reads_a_fixture(items: &[i32]) {{\n        {_HOT_BODY}    }}\n"
    "\n"
    "    #[test]\n"
    "    fn it_calls_the_helper() {\n"
    "        helper_reads_a_fixture(&[1, 2, 3]);\n"
    "    }\n"
    "}\n"
)

_BARE_TEST_FN = f"#[test]\nfn standalone_test(items: &[i32]) {{\n    {_HOT_BODY}}}\n"


def test_production_code_alone_fires_both_markers():
    """Baseline: the shape the gate exists to catch, with no test code at all."""
    walked = _walked("t.rs", "rust", _PRODUCTION_FN)
    out = collect_centrality_gated(walked, _always_hot())
    kinds = sorted(h.kind for h in out.get("t.rs", []))
    assert kinds == ["hot_path_sync_io", "nested_loop_quadratic"]


def test_a_cfg_test_mod_helper_is_silenced():
    """The exact shape a Rust file mixes: production code untouched, an
    identical helper inside `#[cfg(test)] mod tests` silenced — even though
    that helper carries no attribute of its own."""
    src = _PRODUCTION_FN + "\n" + _CFG_TEST_MOD
    walked = _walked("t.rs", "rust", src)
    out = collect_centrality_gated(walked, _always_hot())

    hits = out.get("t.rs", [])
    functions_hit = {h.function for h in hits}
    assert functions_hit == {"production"}
    assert "helper_reads_a_fixture" not in functions_hit
    kinds = sorted(h.kind for h in hits)
    assert kinds == ["hot_path_sync_io", "nested_loop_quadratic"]


def test_a_bare_hash_test_fn_outside_any_mod_is_also_silenced():
    """Not every Rust test lives in a `mod tests` block — a directly
    `#[test]`-attributed top-level fn must be recognised too."""
    src = _PRODUCTION_FN + "\n" + _BARE_TEST_FN
    walked = _walked("t.rs", "rust", src)
    out = collect_centrality_gated(walked, _always_hot())

    functions_hit = {h.function for h in out.get("t.rs", [])}
    assert functions_hit == {"production"}
    assert "standalone_test" not in functions_hit


def test_test_only_file_produces_no_hits_at_all():
    """A file that is ENTIRELY test code (no production fn present) must
    ship nothing, not merely fewer hits."""
    walked = _walked("t.rs", "rust", _CFG_TEST_MOD)
    assert collect_centrality_gated(walked, _always_hot()) == {}


def test_rust_test_line_ranges_cover_the_whole_mod_span():
    """The range covers the mod item's whole span — from its own `mod tests {`
    line (the preceding `#[cfg(test)]` attribute is a sibling, not part of the
    mod_item node) through its closing brace — which is what lets an
    undecorated helper nested inside it be silenced."""
    fc = walk_file("t.rs", "rust", _CFG_TEST_MOD.encode())
    assert len(fc.rust_test_line_ranges) == 1
    start, end = fc.rust_test_line_ranges[0]
    lines = _CFG_TEST_MOD.splitlines()
    assert lines[start - 1].strip() == "mod tests {"
    assert lines[end - 1].strip() == "}"
    # The helper's own line sits inside the range, even though it carries no
    # attribute of its own — the whole reason a per-fn heuristic isn't enough.
    helper_line = next(i + 1 for i, line in enumerate(lines) if "fn helper_reads_a_fixture" in line)
    assert start <= helper_line <= end


@pytest.mark.parametrize("language", ["python", "typescript", "go", "java"])
def test_non_rust_languages_get_empty_ranges_and_are_unaffected(language):
    """The filter is Rust-only by construction: every other language's
    FileComplexity carries no ranges, so the gate's new check is a no-op —
    a production hit in another language must still fire even inside
    something that merely LOOKS test-shaped syntactically."""
    src_by_lang = {
        "python": (
            "def production(items):\n"
            "    open('config.toml').read()\n"
            "    for x in items:\n"
            "        for y in items:\n"
            "            use(x, y)\n"
        ),
        "typescript": (
            "function production(items){ require('fs').readFileSync('c.toml'); "
            "for (const x of items){ for (const y of items){ use(x, y); } } }"
        ),
        "go": (
            "func production(items []int) {\n"
            '\tos.ReadFile("config.toml")\n'
            "\tfor _, x := range items {\n"
            "\t\tfor _, y := range items {\n"
            "\t\t\tuse(x, y)\n"
            "\t\t}\n"
            "\t}\n"
            "}\n"
        ),
        "java": (
            "class T {\n"
            "  void production(int[] items) throws Exception {\n"
            '    java.nio.file.Files.readAllBytes(java.nio.file.Paths.get("c.toml"));\n'
            "    for (int x : items) {\n"
            "      for (int y : items) {\n"
            "        use(x, y);\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "}\n"
        ),
    }
    fc = walk_file(f"t.{language}", language, src_by_lang[language].encode())
    assert fc.rust_test_line_ranges == ()
