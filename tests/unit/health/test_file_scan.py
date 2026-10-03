"""The walker's single file descent answers what the per-node walks did.

``CodeLineIndex.count`` replaces a subtree walk per function and class, so it
is checked against that walk (``_count_nloc``, kept as the reference) on every
named node of every fixture, including nodes inside comments and docstrings,
which take the fallback.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.file_scan import scan_file
from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.complexity.nloc import (
    _code_line_numbers,
    _count_nloc,
    _source_lines,
)
from repowise.core.ingestion.models import EXTENSION_TO_LANGUAGE

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples"

_SNIPPETS = {
    "python": (
        b'"""Module docstring."""\n'
        b"def outer(a):  # trailing comment on the def line\n"
        b'    """Docstring\n\n    spanning lines."""\n'
        b"    # a comment-only line\n"
        b"\n"
        b"    def inner():\n"
        b'        "only a docstring"\n'
        b"    x = '''multi\n\n    line'''\n"
        b"    if a:\n"
        b'        "a bare string opening a block"\n'
        b"        return lambda: (yield)\n"
        b"    return x\r\n"
        b"class K:\n"
        b'    """Class doc."""\n'
        b"    def m(self): return 1\n"
    ),
    "typescript": (
        b"/** header */\n"
        b"export function f(a: number) { // note\n"
        b"  const g = () => {\n"
        b"    /* block\n       comment */\n"
        b"    return a;\n"
        b"  };\n"
        b"  return `tmpl\n\n  ${a}`;\n"
        b"}\n"
        b"class C { m() { return 1; } }\n"
    ),
}


def _parse(path: str, language: str, source: bytes):
    from tree_sitter import Parser

    from repowise.core.ingestion.parser import _get_language, grammar_tag_for
    from repowise.core.ingestion.sfc_source import prepare_source

    grammar = _get_language(grammar_tag_for(language, path))
    if grammar is None:
        pytest.skip(f"{language} tree-sitter pack missing")
    return Parser(grammar).parse(prepare_source(language, source, path=path))


def _cases():
    for path in sorted(FIXTURES.rglob("*")):
        language = EXTENSION_TO_LANGUAGE.get(path.suffix.lower())
        if path.is_file() and language and get_language_map(language) is not None:
            yield pytest.param(str(path), language, path.read_bytes(), id=path.name)
    for language, source in _SNIPPETS.items():
        suffix = ".py" if language == "python" else ".ts"
        yield pytest.param(f"snippet{suffix}", language, source, id=f"snippet-{language}")


@pytest.mark.parametrize(("path", "language", "source"), list(_cases()))
def test_line_index_matches_the_subtree_walk_on_every_node(path, language, source):
    tree = _parse(path, language, source)
    scan = scan_file(tree.root_node, language, get_language_map(language), source, io_names=False)
    root = tree.root_node
    assert scan.lines.file_nloc == len(
        _code_line_numbers(root, _source_lines(source), drop_docstrings=False)
    )
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.is_named:
            assert scan.lines.count(node, source) == _count_nloc(node, source), (
                node.type,
                node.start_point,
            )


def test_rust_test_spans_come_back_in_source_order():
    source = (
        b"#[cfg(test)]\nmod a {\n    #[test]\n    fn t() {}\n}\n"
        b"fn prod() {}\n"
        b"#[test]\nfn b() {}\n"
    )
    fcx = walk_file("t.rs", "rust", source)
    if not fcx.functions:
        pytest.skip("rust tree-sitter pack missing")
    # The nested ``#[test] fn t`` sits inside ``mod a``'s span and is not
    # recorded again.
    assert fcx.rust_test_line_ranges == ((2, 5), (8, 8))
