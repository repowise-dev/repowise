"""Unit tests for the native Rabin-Karp duplication detector."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.duplication import (
    DEFAULT_MIN_LINES,
    DEFAULT_WINDOW_TOKENS,
    DuplicationLimits,
    detect_clones,
    looks_minified,
    tokenize_file,
)
from repowise.core.analysis.health.duplication.detector import (
    ClonePair,
    _aggregate,
    union_line_count,
)
from repowise.core.analysis.health.duplication.rabin_karp import (
    index_by_hash,
    rolling_hashes,
)
from repowise.core.analysis.health.duplication.tokenizer import Token


def _pf(path: str, abs_path: str, language: str = "python") -> SimpleNamespace:
    file_info = SimpleNamespace(path=path, abs_path=abs_path, language=language)
    return SimpleNamespace(file_info=file_info, symbols=[])


def _tok(kind: str, line: int = 1) -> Token:
    return Token(kind=kind, start_line=line, end_line=line, start_byte=0, end_byte=0)


def test_rolling_hash_matches_identical_streams():
    a = [_tok(k) for k in ["ID", "(", "ID", ",", "ID", ")", "ID"]]
    b = list(a)
    ha = rolling_hashes("a.py", a, window=4)
    hb = rolling_hashes("b.py", b, window=4)
    assert len(ha) == len(a) - 4 + 1
    assert {w.hash_value for w in ha} == {w.hash_value for w in hb}


def test_rolling_hash_window_too_large_returns_empty():
    a = [_tok("ID")] * 3
    assert rolling_hashes("a.py", a, window=10) == []


def test_index_by_hash_groups_collisions():
    a = [_tok(k) for k in ["ID"] * 10]
    h = rolling_hashes("a.py", a, window=4)
    bucket = index_by_hash(h)
    # All windows are identical → one bucket, multiple entries.
    assert len(bucket) == 1
    assert sum(len(v) for v in bucket.values()) == len(h)


def test_tokenize_file_drops_comments_and_normalizes_identifiers():
    source = b"def add(a, b):\n    # comment\n    return a + b + 42\n"
    toks = tokenize_file("python", source)
    kinds = [t.kind for t in toks]
    assert "ID" in kinds  # identifier collapsed
    assert "LIT" in kinds  # literal 42 collapsed
    # The comment text never appears.
    assert not any("comment" in k for k in kinds)


def test_tokenize_file_reads_tsx_with_the_jsx_grammar():
    # A .tsx file is tagged ``typescript``, and the grammar that cannot read
    # JSX recovers from every element into ERROR nodes, so the token stream the
    # clone detector hashes is partly invented. The path settles it.
    source = b"export const C = () => <div className=\"x\">{label}</div>;\n"
    as_tsx = [t.kind for t in tokenize_file("typescript", source, "src/C.tsx")]
    as_ts = [t.kind for t in tokenize_file("typescript", source, "src/C.ts")]
    assert as_tsx == [t.kind for t in tokenize_file("tsx", source, "src/C.tsx")]
    assert as_tsx != as_ts
    # Nothing else moves: the path only settles the grammar for .tsx.
    plain = b"export const add = (a: number, b: number): number => a + b;\n"
    assert [t.kind for t in tokenize_file("typescript", plain, "src/a.ts")] == [
        t.kind for t in tokenize_file("typescript", plain, "src/a.other")
    ]


def test_tokenize_file_pascal_normalizes_identifiers_and_literals():
    # Calls tokenize_file() directly with an inline byte literal, so it
    # needs its own best-effort guard rather than inheriting one from a
    # fixture-driven helper.
    pytest.importorskip("tree_sitter_pascal", reason="run `uv sync --all-packages`")
    source = (
        b"unit U;\n interface\n implementation\n"
        b" procedure Foo;\n begin\n   WriteLn('hello', 42);\n end;\n end.\n"
    )
    toks = tokenize_file("pascal", source)
    kinds = [t.kind for t in toks]
    assert "ID" in kinds  # WriteLn / Foo collapsed
    assert "LIT" in kinds  # 'hello' / 42 collapsed
    assert not any("literalString" in k or "literalNumber" in k for k in kinds)


def _write(tmp_path: Path, name: str, src: str) -> Path:
    p = tmp_path / name
    p.write_text(src)
    return p


def test_detect_clones_finds_duplicate_function(tmp_path: Path):
    body = "\n".join(
        [
            "def doit(x, y, z):",
            "    if x:",
            "        a = x + y",
            "    else:",
            "        a = x - y",
            "    if z:",
            "        b = a * 2",
            "    else:",
            "        b = a - 1",
            "    return a + b + x + y + z",
            "",
        ]
    )
    src_a = body
    # Identical structure, renamed identifiers → must still match.
    src_b = body.replace("doit", "renamed").replace("x", "p").replace("y", "q")
    a = _write(tmp_path, "a.py", src_a)
    b = _write(tmp_path, "b.py", src_b)
    parsed = [
        _pf("a.py", str(a)),
        _pf("b.py", str(b)),
    ]
    report = detect_clones(parsed, window_tokens=20, min_lines=4)
    assert report.pairs, "expected at least one clone pair"
    pair = report.pairs[0]
    assert {pair.file_a, pair.file_b} == {"a.py", "b.py"}
    assert pair.a_line_count >= 4
    # No co-change history was provided → score stays 0.
    assert pair.co_change_count == 0
    # Duplication percentage should be populated for both files.
    assert "a.py" in report.duplication_pct
    assert "b.py" in report.duplication_pct


def test_detect_clones_attaches_co_change_count(tmp_path: Path):
    body = "\n".join(
        [
            "def doit(x, y, z):",
            "    total = 0",
            "    for i in range(10):",
            "        if i % 2:",
            "            total += i + x",
            "        else:",
            "            total -= i - y",
            "    return total + z",
            "",
        ]
    )
    a = _write(tmp_path, "a.py", body)
    b = _write(tmp_path, "b.py", body.replace("doit", "twin"))
    parsed = [_pf("a.py", str(a)), _pf("b.py", str(b))]
    git_meta_map = {
        "a.py": {
            "co_change_partners_json": json.dumps([{"file_path": "b.py", "co_change_count": 7, "frequency": 7}])
        },
        "b.py": {
            "co_change_partners_json": json.dumps([{"file_path": "a.py", "co_change_count": 5, "frequency": 5}])
        },
    }
    report = detect_clones(parsed, git_meta_map, window_tokens=20, min_lines=4)
    assert report.pairs
    # Max of the two reported directions wins.
    assert report.pairs[0].co_change_count == 7


def test_detect_clones_skips_files_without_duplicates(tmp_path: Path):
    a = _write(tmp_path, "a.py", "def f():\n    return 1\n")
    b = _write(tmp_path, "b.py", "def g():\n    return 2\n")
    parsed = [_pf("a.py", str(a)), _pf("b.py", str(b))]
    report = detect_clones(parsed, window_tokens=DEFAULT_WINDOW_TOKENS, min_lines=DEFAULT_MIN_LINES)
    assert report.pairs == []
    assert report.duplication_pct == {}


@pytest.mark.parametrize("language", ["python"])
def test_tokenize_file_returns_empty_for_unsupported_language(language: str):
    # An obviously invalid language code yields an empty stream rather
    # than raising.
    assert tokenize_file("not-a-language", b"x = 1\n") == []


# ---------------------------------------------------------------------------
# Resource guards (issue #341 — minified bundles wedged the health phase)
# ---------------------------------------------------------------------------


def test_looks_minified_flags_long_average_line():
    limits = DuplicationLimits()
    # 10 lines, each ~300 bytes → average well over the threshold.
    minified = b"\n".join(b"a" * 300 for _ in range(10))
    assert looks_minified(minified, limits) is True


def test_looks_minified_flags_single_giant_line():
    limits = DuplicationLimits()
    # Many short lines but one monster line (a bundled IIFE) trips it.
    src = b"const x = 1\n" * 50 + b"y" * 5000 + b"\n"
    assert looks_minified(src, limits) is True


def test_looks_minified_passes_normal_source():
    limits = DuplicationLimits()
    src = b"\n".join(b"    return a + b + c" for _ in range(40))
    assert looks_minified(src, limits) is False


def test_looks_minified_handles_empty_source():
    assert looks_minified(b"", DuplicationLimits()) is False


def test_detect_clones_skips_minified_file_but_keeps_real_clones(tmp_path: Path):
    """A checked-in minified bundle must be skipped without preventing
    genuine clone detection between the other files."""
    body = "\n".join(
        [
            "def doit(x, y, z):",
            "    if x:",
            "        a = x + y",
            "    else:",
            "        a = x - y",
            "    return a + x + y + z",
            "",
        ]
    )
    a = _write(tmp_path, "a.py", body)
    b = _write(tmp_path, "b.py", body.replace("doit", "twin"))
    # One giant single-line "bundle" that previously blew up the detector.
    bundle = _write(tmp_path, "bundle.min.js", "var a=1;" * 50_000 + "\n")
    parsed = [
        _pf("a.py", str(a)),
        _pf("b.py", str(b)),
        _pf("bundle.min.js", str(bundle), language="javascript"),
    ]
    report = detect_clones(parsed, window_tokens=20, min_lines=4)
    # Genuine clone between a.py and b.py still found.
    assert report.pairs
    assert {report.pairs[0].file_a, report.pairs[0].file_b} == {"a.py", "b.py"}
    # The bundle was skipped, not tokenized.
    assert report.diagnostics["skipped_minified"] >= 1
    assert "bundle.min.js" not in report.duplication_pct


def test_detect_clones_caps_degenerate_bucket(tmp_path: Path):
    """Many identical files produce one oversized hash bucket; it must be
    dropped instead of triggering the O(k^2) all-pairs explosion."""
    snippet = "def f():\n    return 1 + 2 + 3 + 4 + 5\n"
    parsed = []
    for i in range(12):
        p = _write(tmp_path, f"f{i}.py", snippet)
        parsed.append(_pf(f"f{i}.py", str(p)))
    limits = DuplicationLimits(max_bucket_windows=4, time_budget_secs=0)
    report = detect_clones(parsed, window_tokens=4, min_lines=1, limits=limits)
    assert report.diagnostics["degenerate_buckets"] >= 1
    # Oversized buckets are skipped wholesale → no pairs emitted.
    assert report.pairs == []


def test_detect_clones_respects_per_file_token_cap(tmp_path: Path):
    body = "def doit(x, y, z):\n    return x + y + z + x + y + z\n"
    a = _write(tmp_path, "a.py", body)
    b = _write(tmp_path, "b.py", body.replace("doit", "twin"))
    parsed = [_pf("a.py", str(a)), _pf("b.py", str(b))]
    # Cap below the token count of these small files → both skipped.
    limits = DuplicationLimits(max_tokens_per_file=5)
    report = detect_clones(parsed, window_tokens=4, min_lines=1, limits=limits)
    assert report.diagnostics["skipped_token_cap"] == 2
    assert report.pairs == []


def test_detect_clones_window_budget_stops_collection(tmp_path: Path):
    body = "def doit(x, y, z):\n    return x + y + z + x + y + z\n"
    parsed = []
    for i in range(5):
        p = _write(tmp_path, f"f{i}.py", body)
        parsed.append(_pf(f"f{i}.py", str(p)))
    limits = DuplicationLimits(max_total_windows=3)
    report = detect_clones(parsed, window_tokens=4, min_lines=1, limits=limits)
    assert report.diagnostics["window_budget_hit"] is True


def test_detect_clones_reports_diagnostics_on_normal_run(tmp_path: Path):
    a = _write(tmp_path, "a.py", "def f():\n    return 1\n")
    parsed = [_pf("a.py", str(a))]
    report = detect_clones(parsed, window_tokens=DEFAULT_WINDOW_TOKENS, min_lines=DEFAULT_MIN_LINES)
    # Diagnostics are always populated, even when nothing tripped.
    assert report.diagnostics["files_considered"] == 1
    assert report.diagnostics["degenerate_buckets"] == 0
    assert report.diagnostics["timed_out"] is False


# --- duplication_pct aggregation (union, not sum — #377) ----------------


def _pair(
    file_a: str,
    a_start: int,
    a_end: int,
    file_b: str,
    b_start: int,
    b_end: int,
) -> ClonePair:
    return ClonePair(
        file_a=file_a,
        file_b=file_b,
        a_start_line=a_start,
        a_end_line=a_end,
        b_start_line=b_start,
        b_end_line=b_end,
        token_count=50,
    )


@pytest.mark.parametrize(
    ("ranges", "expected"),
    [
        ([(1, 5)], 5),
        ([(1, 5), (10, 12)], 8),  # disjoint
        ([(1, 10), (5, 15)], 15),  # overlapping
        ([(1, 20), (5, 8)], 20),  # nested
        ([(1, 5), (6, 10)], 10),  # adjacent
        ([(10, 12), (1, 5)], 8),  # unsorted input
        ([(1, 10), (1, 10), (1, 10)], 10),  # identical repeats
    ],
)
def testunion_line_count(ranges: list[tuple[int, int]], expected: int):
    assert union_line_count(ranges) == expected


def test_aggregate_overlapping_pairs_do_not_double_count():
    # The #377 shape: many clone pairs over the SAME physical lines.
    # Summing per-pair counts gives 10 * 20 = 200 lines over nloc 100
    # (200% → capped 100%); the union is just 20 lines → 20%.
    pairs = [_pair("a.py", 1, 20, f"other{i}.py", 1, 20) for i in range(10)]
    nloc = {"a.py": 100, **{f"other{i}.py": 100 for i in range(10)}}
    _, pct = _aggregate(pairs, nloc)
    assert pct["a.py"] == 20.0


def test_aggregate_intra_file_pair_counts_both_regions():
    # Both regions of an intra-file clone are duplicated coverage.
    pairs = [_pair("a.py", 1, 10, "a.py", 21, 30)]
    _, pct = _aggregate(pairs, {"a.py": 100})
    assert pct["a.py"] == 20.0


def test_aggregate_distinct_regions_still_add_up():
    pairs = [
        _pair("a.py", 1, 10, "b.py", 1, 10),
        _pair("a.py", 31, 40, "c.py", 1, 10),
    ]
    _, pct = _aggregate(pairs, {"a.py": 50, "b.py": 50, "c.py": 50})
    assert pct["a.py"] == 40.0
    assert pct["b.py"] == 20.0
    assert pct["c.py"] == 20.0


def test_aggregate_caps_at_100_when_ranges_exceed_nloc():
    # Covered ranges count physical lines while nloc excludes blanks, so
    # the ratio can still exceed 100 — the cap stays as a safety net.
    pairs = [_pair("a.py", 1, 60, "b.py", 1, 60)]
    _, pct = _aggregate(pairs, {"a.py": 40, "b.py": 40})
    assert pct["a.py"] == 100.0


def test_aggregate_skips_files_without_nloc():
    pairs = [_pair("a.py", 1, 10, "b.py", 1, 10)]
    _, pct = _aggregate(pairs, {"a.py": 50})
    assert "b.py" not in pct


# ---- identifier-level verification ----------------------------------------

_LOOP_BODY = "\n".join(
    [
        "def {fn}({a}, {b}, {c}):",
        "    {t} = 0",
        "    for {i} in range({a}):",
        "        if {i} > {b}:",
        "            {t} = {t} + {i} * {c}",
        "        else:",
        "            {t} = {t} - {b}",
        "    return {t} + {a} + {b} + {c}",
        "",
    ]
)


def _loop(**names: str) -> str:
    return _LOOP_BODY.format(**names)


def test_tokenize_file_drops_python_imports():
    toks = tokenize_file("python", b"import os\nfrom a.b import c, d\nx = 1\n")
    assert [t.kind for t in toks] == ["ID", "=", "LIT"]


def test_tokenize_file_drops_the_java_package_declaration():
    toks = tokenize_file("java", b"package a.b;\nimport x.Y;\nclass C {}\n", "C.java")
    assert "package" not in [t.kind for t in toks]
    assert toks[0].kind == "class"
    assert toks[0].start_line == 3


def test_tokenize_file_drops_ts_imports_and_reexports_but_keeps_local_exports():
    source = (
        b'import { a, b } from "./x";\n'
        b'export { c } from "./y";\n'
        b'export * from "./z";\n'
        b"export const n = 1;\n"
    )
    toks = tokenize_file("typescript", source, "src/i.ts")
    assert [t.kind for t in toks] == ["export", "const", "ID", "=", "LIT", ";"]


@pytest.mark.parametrize("language", ["ruby", "elixir", "shell", "luau", "gdscript"])
def test_import_node_kinds_never_skip_calls_or_superclass_clauses(language: str):
    from repowise.core.analysis.health.duplication.tokenizer import import_node_kinds

    assert import_node_kinds(language) == frozenset()


def test_tokenize_file_keeps_raw_identifier_names():
    toks = tokenize_file("python", b"total = count + 1\n")
    assert [t.name for t in toks] == ["total", "", "count", "", ""]


def test_tokenize_file_lines_match_tree_sitter_points():
    from tree_sitter import Parser

    from repowise.core.ingestion.parser import _get_language

    source = b'x = 1\r\n\r\ns = """a\r\nb\n"""\nif x:\n    y = (s,\n         x)\n'
    tree = Parser(_get_language("python")).parse(source)
    leaves = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.child_count == 0 and source[node.start_byte : node.end_byte].strip():
            leaves.append((node.start_point[0] + 1, node.end_point[0] + 1))
        stack.extend(reversed(node.children))

    toks = tokenize_file("python", source)
    assert [(t.start_line, t.end_line) for t in toks] == leaves
    assert (3, 5) in [(t.start_line, t.end_line) for t in toks]  # the multi-line string


def test_detect_clones_ignores_identical_import_blocks(tmp_path: Path):
    imports = "".join(f"from pkg.mod{i} import name{i}, other{i}\n" for i in range(12))
    a = _write(tmp_path, "a.py", imports + "def f():\n    return 1\n")
    b = _write(tmp_path, "b.py", imports + "def g(x):\n    return x * 2\n")
    report = detect_clones([_pf("a.py", str(a)), _pf("b.py", str(b))], window_tokens=20, min_lines=4)
    assert report.pairs == []


def test_a_java_clone_starts_at_the_shared_code_not_the_package_line(tmp_path: Path):
    header = "/* License header */\npackage com.acme.core.util;\n\n" + "".join(
        f"import com.acme.core.mod{i}.Thing{i};\n" for i in range(12)
    ) + "\n"
    method = (
        "    int total(int a, int b) {\n        int sum = a + b;\n        if (sum > 10) {\n"
        "            return sum * 2;\n        }\n        for (int i = 0; i < b; i++) {\n"
        "            sum += i;\n        }\n        return sum;\n    }\n"
    )
    a = _write(tmp_path, "A.java", header + "public class Alpha {\n" + method + "}\n")
    b = _write(tmp_path, "B.java", header + "public class Beta {\n" + method + "}\n")
    report = detect_clones(
        [_pf("A.java", str(a), language="java"), _pf("B.java", str(b), language="java")]
    )
    # The pair is still found, from the class line (17) on: the package line
    # is line 2 and the shared method is lines 18-27.
    assert len(report.pairs) == 1
    pair = report.pairs[0]
    assert pair.a_start_line >= 17 and pair.b_start_line >= 17
    assert pair.a_end_line >= 27 and pair.b_end_line >= 27


def test_detect_clones_ignores_matching_data_literals(tmp_path: Path):
    table = "TABLE = [\n" + "".join(f"    ({i}, '{i}', {i}.5),\n" for i in range(20)) + "]\n"
    a = _write(tmp_path, "a.py", table)
    b = _write(tmp_path, "b.py", table.replace("TABLE", "OTHER"))
    report = detect_clones([_pf("a.py", str(a)), _pf("b.py", str(b))], window_tokens=20, min_lines=4)
    assert report.pairs == []


def test_detect_clones_needs_shared_identifier_names(tmp_path: Path):
    same = dict(fn="f", a="a", b="b", c="c", t="total", i="i")
    renamed = dict(fn="g", a="rows", b="limit", c="scale", t="acc", i="k")
    a = _write(tmp_path, "a.py", _loop(**same))
    b = _write(tmp_path, "b.py", _loop(**renamed))
    c = _write(tmp_path, "c.py", _loop(**{**same, "fn": "f_copy"}))
    parsed = [_pf("a.py", str(a)), _pf("b.py", str(b)), _pf("c.py", str(c))]
    report = detect_clones(parsed, window_tokens=20, min_lines=4)
    # Same shape with every name changed is not a copy; a real copy still is.
    assert {(p.file_a, p.file_b) for p in report.pairs} == {("a.py", "c.py")}
