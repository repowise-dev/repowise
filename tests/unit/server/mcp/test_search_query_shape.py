"""How search_codebase reads a query's shape, pinned at the tool module."""

from __future__ import annotations

import subprocess
import sys

import pytest

from repowise.server.mcp_server.tool_search import (
    _DECISION_DOWNWEIGHT,
    _MIN_RELEVANCE_SCORE,
    _canonical_symbol_query,
    _embedded_identifiers,
    _fetch_limit_for,
    _has_exact_symbol,
    _is_why_shaped,
    _resolve_mode,
)


@pytest.mark.parametrize(
    "query, mode, expected",
    [
        ("src/app.py::Service", None, "symbol"),
        ("src\\app.py::Service", "auto", "symbol"),
        ("app.py::Service", None, "concept"),
        ("packages/core/src/x.py", None, "path"),
        ("setup.py", None, "path"),
        ("main.go", None, "path"),
        # Not a code extension, so it is not a path; it reads as an identifier.
        ("README.md", None, "symbol"),
        ("what does client/server boundary mean", None, "concept"),
        ("src/app.py?", None, "concept"),
        ("getCurrentUser", None, "symbol"),
        ("Foo.bar", None, "symbol"),
        ("how does parse_file handle errors", None, "hybrid"),
        ("where is ParserRegistry.resolve used", None, "hybrid"),
        ("how does caching work", None, "concept"),
        ("anything", "PATH", "path"),
        ("how it works", "bogus", "concept"),
        ("", None, "concept"),
    ],
)
def test_resolve_mode(query: str, mode: str | None, expected: str) -> None:
    assert _resolve_mode(query, mode) == expected


def test_identifier_shapes() -> None:
    assert _canonical_symbol_query(" src\\a.py::Foo ") == ("src/a.py", "Foo")
    assert _canonical_symbol_query("a.py::Foo") is None
    assert _embedded_identifiers("why does ParserRegistry.resolve call load_spec") == [
        "ParserRegistry.resolve",
        "load_spec",
    ]
    assert _embedded_identifiers("plain english words only") == []


def test_exact_symbol_and_relevance_constants() -> None:
    symbols = [{"name": "method", "qualified_name": "pkg::Class::method"}]
    assert _has_exact_symbol(["Class.method"], symbols)
    assert not _has_exact_symbol(["other"], symbols)
    assert (_MIN_RELEVANCE_SCORE, _DECISION_DOWNWEIGHT) == (0.03, 0.6)
    assert _fetch_limit_for(10, None) == 30
    assert _fetch_limit_for(10, "test") == 60
    assert _is_why_shaped("why is the cache here")
    assert not _is_why_shaped("cache eviction")


def test_the_shape_module_loads_no_registry_or_database() -> None:
    code = (
        "import sys; import repowise.server.mcp_server._query_shape; "
        "print(sorted(m for m in sys.modules if m == 'sqlalchemy' "
        "or m.startswith(('repowise.core.registry', 'repowise.core.persistence', "
        "'repowise.core.ingestion'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
