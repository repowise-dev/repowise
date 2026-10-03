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
    _looks_like_code_name,
    _mark_not_the_named_symbol,
    _names_a_path,
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


# A made-up repo's symbol table: what `_embedded_identifiers(query, names)`
# validates tokens against. The name set also answers for each lowered name,
# which is how a caller turns on the case-insensitive leg.
_INDEXED = ["executeWithTool", "proxyExecute", "OpenAIProvider", "Session", "refresh", "load_spec"]
_NAMES = {*_INDEXED, *(n.lower() for n in _INDEXED)}


@pytest.mark.parametrize(
    "query, expected",
    [
        # lowerCamel: the shape regex alone misses both.
        ("how does executeWithTool call proxyExecute", ["executeWithTool", "proxyExecute"]),
        # Acronym inside a CamelCase name.
        ("where is OpenAIProvider defined", ["OpenAIProvider"]),
        # Dotted: the chain counts when its last part names a symbol.
        ("what does client.proxyExecute return", ["client.proxyExecute"]),
        ("when is Session.refresh called", ["Session.refresh"]),
        ("how does load_spec work", ["load_spec"]),
        # Case-insensitive after case-sensitive, for mixed-case tokens.
        ("where is openAIProvider", ["openAIProvider"]),
        # Language and product words name no symbol, so they are not identifiers.
        ("how does the TypeScript SDK differ from the Python one", []),
        ("does JavaScript support this", []),
        # A capitalised sentence word never folds onto a lowercase symbol.
        ("Refresh the token", []),
    ],
)
def test_embedded_identifiers_validated_against_the_symbol_table(query, expected) -> None:
    assert _embedded_identifiers(query, _NAMES) == expected


def test_a_language_word_is_an_identifier_only_when_a_symbol_carries_it() -> None:
    assert _embedded_identifiers("how does TypeScript work", {"TypeScript"}) == ["TypeScript"]
    # Unvalidated, the shape regex keeps its old reading.
    assert _embedded_identifiers("how does TypeScript work") == ["TypeScript"]


def test_resolve_mode_routes_on_validated_identifiers() -> None:
    assert _resolve_mode("how does executeWithTool work", None, _NAMES) == "hybrid"
    assert _resolve_mode("how does the TypeScript client work", None, _NAMES) == "concept"


# Go exports plain English words as methods and types.
_ONE_HUMP = {"Add", "Fixes", "Client", "Do", "API", "HTTPClient", "TypeScript"}


@pytest.mark.parametrize(
    "query",
    [
        "Add support for retries in the transport",
        "Fixes a panic when the pool is closed twice",
        "Client should retry when the API returns 503",
    ],
)
def test_a_one_hump_word_in_prose_is_not_an_identifier(query) -> None:
    assert _embedded_identifiers(query, _ONE_HUMP) == []
    assert _resolve_mode(query, None, _ONE_HUMP) == "concept"


@pytest.mark.parametrize(
    "query, expected",
    [
        ("support for `Add` in the transport", ["Add"]),
        ("why does `the API` reject retries", ["API"]),
        ("calling Add() twice panics", ["Add"]),
        ("retries in Client.Do are lost", ["Client.Do"]),
        ("the transport calls x.Add on close", ["x.Add"]),
        # Multi-hump names are validated as before, in prose too.
        ("HTTPClient should retry on TypeScript errors", ["HTTPClient", "TypeScript"]),
    ],
)
def test_a_one_hump_word_in_code_context_is_an_identifier(query, expected) -> None:
    assert _embedded_identifiers(query, _ONE_HUMP) == expected
    assert _resolve_mode(query, None, _ONE_HUMP) == "hybrid"


@pytest.mark.parametrize(
    "token",
    [
        "executeToolWithRetryBackoff",
        "getToolkitMigrationPlan",
        "AnthropicStreamingAdapter",
        "validate_trigger_nonce",
        "OpenAIProvider",
        "MAX_RETRIES",
        "client.proxyExecute",
    ],
)
def test_code_shaped_names(token) -> None:
    assert _looks_like_code_name(token)


@pytest.mark.parametrize(
    "token",
    [
        "TypeScript",
        "JavaScript",
        "GitHub",
        "PostgreSQL",
        "iPhone",
        "macOS",
        "Python",
        "API",
        "AuthService login",
    ],
)
def test_product_and_language_words_are_not_code_shaped(token) -> None:
    assert not _looks_like_code_name(token)


def test_a_module_path_is_not_a_missing_symbol() -> None:
    paths = ["app/services/mcp_tools/_tools_search.py", "src/client.ts"]
    assert _names_a_path("app.services.mcp_tools", paths)
    assert _names_a_path("_tools_search", paths)
    assert not _names_a_path("validate_trigger_nonce", paths)


def test_pages_for_a_missing_symbol_are_labelled_and_capped() -> None:
    items = [{"confidence_score": 0.9}, {"confidence_score": 0.2}, {}]
    _mark_not_the_named_symbol(items)
    assert {item["relation"] for item in items} == {"related, not the named symbol"}
    assert [item.get("confidence_score") for item in items] == [0.45, 0.2, None]
