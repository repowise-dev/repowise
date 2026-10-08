"""Golden tests for index-backed skeletonization.

Fixtures are real files from this repo pinned at the commit the index was
built from (``*.src.txt`` content + ``*.symbols.json`` WikiSymbol rows with
symbol-node PageRank), so the tests never depend on the live working tree
or on a parser running at test time.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.distill.skeleton import (
    SkeletonResult,
    SkeletonSymbol,
    build_skeleton,
)


def _load_case(load_fixture, stem: str) -> tuple[str, list[SkeletonSymbol]]:
    source = load_fixture(f"{stem}.src.txt")
    payload = json.loads(load_fixture(f"{stem}.symbols.json"))
    symbols = [
        SkeletonSymbol(
            name=s["name"],
            kind=s["kind"],
            start_line=s["start_line"],
            end_line=s["end_line"],
            signature=s["signature"],
            importance=s["pagerank"],
        )
        for s in payload["symbols"]
    ]
    return source, symbols


@pytest.fixture(scope="module")
def python_case(load_fixture):
    return _load_case(load_fixture, "skeleton_traverser")


@pytest.fixture(scope="module")
def tsx_case(load_fixture):
    return _load_case(load_fixture, "skeleton_artifacts")


class TestSignaturesMode:
    def test_size_and_completeness_python(self, python_case) -> None:
        source, symbols = python_case
        result = build_skeleton(source, symbols, mode="signatures")
        assert result.mode == "signatures"
        # Exit criterion: a ~600-line file compresses to <=~15% of full tokens.
        assert result.skeleton_tokens <= result.full_tokens * 0.15, (
            f"{result.skeleton_tokens}/{result.full_tokens} = {result.pct_of_full:.1f}%"
        )
        # Every signature line is present verbatim.
        lines = source.splitlines()
        for sym in symbols:
            if sym.name == "__module__":
                continue
            assert lines[sym.start_line - 1] in result.text, sym.name

    def test_size_and_completeness_tsx(self, tsx_case) -> None:
        source, symbols = tsx_case
        result = build_skeleton(source, symbols, mode="signatures")
        assert result.skeleton_tokens <= result.full_tokens * 0.35
        lines = source.splitlines()
        for sym in symbols:
            assert lines[sym.start_line - 1] in result.text, sym.name

    def test_imports_and_module_docstring_kept(self, python_case) -> None:
        source, symbols = python_case
        result = build_skeleton(source, symbols, mode="signatures")
        for line in source.splitlines():
            if line.startswith(("import ", "from ")):
                assert line in result.text, line
        # Module docstring opener is the first line of the file.
        assert source.splitlines()[0] in result.text

    def test_elision_markers_carry_line_ranges(self, python_case) -> None:
        source, symbols = python_case
        result = build_skeleton(source, symbols, mode="signatures")
        import re

        markers = re.findall(r"\.\.\. (\d+) lines \((\d+)-(\d+)\)", result.text)
        assert markers, "expected at least one elision marker"
        for n, a, b in markers:
            assert int(b) - int(a) + 1 == int(n)
            assert 1 <= int(a) <= int(b) <= len(source.splitlines())

    def test_no_bodies_in_signatures_mode(self, python_case) -> None:
        source, symbols = python_case
        result = build_skeleton(source, symbols, mode="signatures")
        assert result.bodies_kept == ()


class TestSmartMode:
    def test_keeps_high_pagerank_bodies(self, python_case) -> None:
        source, symbols = python_case
        result = build_skeleton(source, symbols, mode="smart", token_budget=2500)
        assert result.bodies_kept, "smart mode kept no bodies"
        # The kept bodies are the top-importance leaf symbols, in rank order.
        ranked = sorted(symbols, key=lambda s: -s.importance)
        top_names = [s.name for s in ranked[:10]]
        assert result.bodies_kept[0] in top_names
        # And the skeleton is strictly larger than signatures-only.
        sig_only = build_skeleton(source, symbols, mode="signatures")
        assert result.skeleton_tokens > sig_only.skeleton_tokens

    def test_respects_token_budget(self, python_case) -> None:
        source, symbols = python_case
        small = build_skeleton(source, symbols, mode="smart", token_budget=900)
        large = build_skeleton(source, symbols, mode="smart", token_budget=3000)
        assert small.skeleton_tokens <= large.skeleton_tokens
        # Budget bounds the output with slack for the signature floor.
        assert small.skeleton_tokens <= 900 * 1.3

    def test_query_match_boosts_symbol(self, python_case) -> None:
        source, symbols = python_case
        # Pick a low-importance leaf symbol and query for it by name.
        leaf = min(
            (s for s in symbols if s.end_line - s.start_line > 8),
            key=lambda s: s.importance,
        )
        result = build_skeleton(source, symbols, mode="smart", token_budget=2500, query=leaf.name)
        assert leaf.name in result.bodies_kept

    def test_hotspot_widens_budget(self, python_case) -> None:
        source, symbols = python_case
        cold = build_skeleton(source, symbols, mode="smart", token_budget=1500)
        hot = build_skeleton(source, symbols, mode="smart", token_budget=1500, hotspot=True)
        assert hot.skeleton_tokens >= cold.skeleton_tokens


class TestDegradation:
    def test_no_symbols_returns_raw(self) -> None:
        source = "line one\nline two\nline three\n"
        result = build_skeleton(source, [])
        assert result.mode == "raw"
        assert result.text == source
        assert result.skeleton_tokens == result.full_tokens

    def test_out_of_range_symbols_returns_raw(self) -> None:
        source = "x = 1\n"
        bad = [SkeletonSymbol(name="ghost", kind="function", start_line=50, end_line=80)]
        result = build_skeleton(source, bad)
        assert result.mode == "raw"
        assert result.text == source

    def test_synthetic_module_symbol_ignored(self) -> None:
        source = "x = 1\ny = 2\n"
        syn = [SkeletonSymbol(name="__module__", kind="module", start_line=0, end_line=0)]
        result = build_skeleton(source, syn)
        assert result.mode == "raw"

    def test_end_line_clamped_to_file(self) -> None:
        source = "def f():\n    return 1\n"
        sym = [SkeletonSymbol(name="f", kind="function", start_line=1, end_line=999)]
        result = build_skeleton(source, sym, mode="signatures")
        assert "def f():" in result.text

    def test_unknown_mode_falls_back_to_signatures(self, python_case) -> None:
        source, symbols = python_case
        result = build_skeleton(source, symbols, mode="banana")
        assert result.mode == "signatures"
        assert result.bodies_kept == ()


class TestSignatureEndHeuristics:
    def test_multiline_python_signature(self) -> None:
        source = (
            "def f(\n"
            "    a: int,\n"
            "    b: str,\n"
            ") -> bool:\n"
            "    x = 1\n"
            "    y = 2\n"
            "    z = 3\n"
            "    w = 4\n"
            "    return True\n"
        )
        sym = [SkeletonSymbol(name="f", kind="function", start_line=1, end_line=9)]
        result = build_skeleton(source, sym, mode="signatures")
        assert ") -> bool:" in result.text
        assert "return True" not in result.text

    def test_brace_language_signature(self) -> None:
        source = (
            "func Process(items []string) error {\n" + "\tstep := 0\n" * 8 + "\treturn nil\n}\n"
        )
        sym = [SkeletonSymbol(name="Process", kind="function", start_line=1, end_line=11)]
        result = build_skeleton(source, sym, mode="signatures")
        assert "func Process(items []string) error {" in result.text
        assert "step := 0" not in result.text

    def test_docstring_summary_kept_in_smart_mode_only(self) -> None:
        body = (
            '    """Summary line.\n\n    Detail one.\n    Detail two.\n    Detail three.\n    Detail four.\n    """\n'
            + "    x = 1\n" * 6
        )
        source = "def f():\n" + body + "    return x\n"
        total = source.count("\n")
        sym = [SkeletonSymbol(name="f", kind="function", start_line=1, end_line=total)]
        smart = build_skeleton(source, sym, mode="smart", token_budget=1)
        assert '"""Summary line.' in smart.text
        sigs = build_skeleton(source, sym, mode="signatures")
        assert "Summary line." not in sigs.text
        assert "x = 1" not in sigs.text


class TestResultShape:
    def test_pct_of_full(self) -> None:
        r = SkeletonResult(
            text="x", mode="signatures", full_tokens=200, skeleton_tokens=30, symbol_count=3
        )
        assert r.pct_of_full == 15.0


# Bounds that start on an annotation line, as some parsers persist them.
_ANNOTATED = [
    (
        "@decorator\ndef run(a, b):\n    x = a\n    y = b\n    z = x + y\n    return z\n",
        "def run(a, b):",
    ),
    (
        "@Override\npublic String toString() {\n    String s = \"x\";\n    s = s + 1;\n"
        "    s = s + 2;\n    return s;\n}\n",
        "public String toString() {",
    ),
    (
        "#[inline]\nfn top(a: u32) -> u32 {\n    let s = a;\n    let t = s * 2;\n"
        "    let u = t + 1;\n    u\n}\n",
        "fn top(a: u32) -> u32 {",
    ),
]


@pytest.mark.parametrize("mode", ["smart", "signatures"])
@pytest.mark.parametrize(("source", "signature"), _ANNOTATED)
def test_a_signature_after_an_annotation_line_is_kept(mode, source, signature) -> None:
    total = len(source.splitlines())
    sym = SkeletonSymbol(name="f", kind="function", start_line=1, end_line=total)
    result = build_skeleton(source, [sym], mode=mode)
    assert result.text.splitlines()[:2] == source.splitlines()[:2]
    assert signature in result.text
    assert "... " in result.text  # the body itself is still elided


_FOCUS_SOURCE = '''"""Display helpers."""
from datetime import datetime


def format_timestamp(value: str) -> str:
    """Format a timestamp."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    out = dt.strftime("%b %d")
    return out


def format_file_size(size: float) -> str:
    """Format a file size."""
    # ----------
    if size < 1024:
        return f"{int(size)} B"
    size /= 1024
    return f"{size:.1f} KB"
'''


def _focus_symbols(focus: str | None) -> list[SkeletonSymbol]:
    bounds = {"format_timestamp": (5, 11, 1.0), "format_file_size": (14, 20, 0.1)}
    return [
        SkeletonSymbol(name, "function", start, end, importance=rank, focus=name == focus)
        for name, (start, end, rank) in bounds.items()
    ]


class TestFocus:
    @pytest.mark.parametrize("mode", ["smart", "plus"])
    def test_keeps_only_the_focused_body(self, mode) -> None:
        result = build_skeleton(_FOCUS_SOURCE, _focus_symbols("format_file_size"), mode=mode)
        lines = _FOCUS_SOURCE.splitlines()
        # The focused symbol is whole, decorative comment included.
        for line in lines[13:20]:
            assert line in result.text
        # The other symbol, though higher-ranked, is reduced to its signature.
        assert "def format_timestamp(value: str) -> str:" in result.text
        assert "dt.strftime" not in result.text
        assert result.bodies_kept == ("format_file_size",)
        assert "from datetime import datetime" in result.text

    def test_without_focus_smart_ranks_bodies(self) -> None:
        result = build_skeleton(_FOCUS_SOURCE, _focus_symbols(None), mode="smart")
        assert "format_timestamp" in result.bodies_kept
