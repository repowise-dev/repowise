from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.walker import _count_file_nloc
from repowise.core.analysis.health.duplication import DuplicationReport
from repowise.core.analysis.health.engine import HealthAnalyzer
from repowise.core.analysis.health.models import HealthFileMetricData

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples"


def _non_blank(source: bytes) -> int:
    return sum(1 for line in source.decode("utf-8", errors="replace").splitlines() if line.strip())


def test_js_file_nloc_excludes_comment_only():
    source = b"const x = 1;\n// comment\nconst y = 2;\n"
    fcx = walk_file("/tmp/t.js", "javascript", source)
    if fcx.file_nloc == 0:
        pytest.skip("javascript tree-sitter pack missing")
    assert fcx.file_nloc == 2


def test_python_file_nloc_excludes_comment_only():
    source = b"x = 1\n# comment\ny = 2\n"
    fcx = walk_file("/tmp/t.py", "python", source)
    if fcx.file_nloc == 0:
        pytest.skip("python tree-sitter pack missing")
    assert fcx.file_nloc == 2


def test_trailing_comment_line_still_counts():
    source = b"x = 1  # trailing\ny = 2\n"
    fcx = walk_file("/tmp/t.py", "python", source)
    if fcx.file_nloc == 0:
        pytest.skip("python tree-sitter pack missing")
    assert fcx.file_nloc == 2


def test_multiline_string_blank_line_does_not_count():
    source = b'x = """a\n\n b"""\ny = 2\n'
    fcx = walk_file("/tmp/t.py", "python", source)
    if fcx.file_nloc == 0:
        pytest.skip("python tree-sitter pack missing")
    assert fcx.file_nloc == 3


def test_js_route_file_nloc_excludes_one_comment_line():
    p = FIXTURES / "javascript" / "route.js"
    if not p.exists():
        pytest.skip(f"fixture missing: {p}")
    source = p.read_bytes()
    fcx = walk_file(str(p), "javascript", source)
    if fcx.file_nloc == 0:
        pytest.skip("javascript tree-sitter pack missing")
    assert fcx.file_nloc == _non_blank(source) - 1


def test_js_route_function_sum_less_than_file_nloc():
    p = FIXTURES / "javascript" / "route.js"
    if not p.exists():
        pytest.skip(f"fixture missing: {p}")
    source = p.read_bytes()
    fcx = walk_file(str(p), "javascript", source)
    if not fcx.functions:
        pytest.skip("javascript tree-sitter pack missing or no functions detected")
    assert sum(fn.nloc for fn in fcx.functions) < fcx.file_nloc


def test_health_metric_nloc_uses_file_nloc():
    p = FIXTURES / "javascript" / "route.js"
    if not p.exists():
        pytest.skip(f"fixture missing: {p}")
    source = p.read_bytes()
    fcx = walk_file(str(p), "javascript", source)
    if fcx.file_nloc == 0:
        pytest.skip("javascript tree-sitter pack missing")

    pf = SimpleNamespace(
        file_info=SimpleNamespace(path="src/route.js", language="javascript", abs_path=str(p)),
        symbols=[],
    )
    metric, _, _ = HealthAnalyzer(graph=None)._evaluate_file(
        pf,
        fcx,
        path_basenames={"route.js"},
        disabled=[],
        dup_report=DuplicationReport(),
    )
    assert isinstance(metric, HealthFileMetricData)
    assert metric.nloc == fcx.file_nloc


def test_unsupported_language_fallback_nloc():
    source = b"foo bar baz\n\nqux quux\n# comment\n"
    fcx = walk_file("/tmp/x.klingon", "klingon", source)
    assert fcx.functions == []
    assert fcx.classes == []
    assert fcx.file_nloc == _non_blank(source)
    assert fcx.file_nloc > 0


def test_count_file_nloc_empty_source():
    assert _count_file_nloc(b"") == 0


def test_count_file_nloc_blank_only():
    assert _count_file_nloc(b"\n  \n\t\n") == 0


def test_count_file_nloc_mixed():
    source = b"a = 1\n\n  \nb = 2\n# comment\n"
    assert _count_file_nloc(source) == 3
