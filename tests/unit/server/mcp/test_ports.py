"""``mcp_server.ports`` gives each pure piece a host reuses a public name."""

from __future__ import annotations

import importlib

import pytest

from repowise.server.mcp_server import ports

# (public name, engine module, private name there)
_PORTS = [
    ("LINE_ID_RE", "repowise.server.mcp_server.tool_symbol.ranges", "_LINE_ID_RE"),
    ("RANGE_ID_RE", "repowise.server.mcp_server.tool_symbol.ranges", "_RANGE_ID_RE"),
    ("LINE_WINDOW", "repowise.server.mcp_server.tool_symbol.ranges", "_LINE_WINDOW"),
    ("SYMBOL_CAP", "repowise.server.mcp_server.tool_context.targets", "_SYMBOL_CAP"),
    ("normalize_scope", "repowise.server.mcp_server.tool_answer.synthesis", "_normalize_scope"),
    ("nearest", "repowise.server.mcp_server.tool_answer.scope", "_nearest"),
    ("outermost", "repowise.server.mcp_server.tool_answer.scope", "_outermost"),
    ("NEAREST_DIRS", "repowise.server.mcp_server.tool_answer.scope", "_NEAREST_DIRS"),
    ("by_alternative", "repowise.server.mcp_server.tool_search.structured", "_by_alternative"),
    ("MAX_ALTERNATIVES", "repowise.server.mcp_server._query_shape", "_MAX_ALTERNATIVES"),
    ("ENCLOSING_KINDS", "repowise.server.mcp_server._enclosing", "_ENCLOSING_KINDS"),
    ("Span", "repowise.server.mcp_server._enclosing", "_Span"),
    ("include_facets", "repowise.server.mcp_server._savings.wrapper", "_include_facets"),
]


@pytest.mark.parametrize(("public", "module", "private"), _PORTS)
def test_public_name_is_the_private_object(public, module, private):
    assert getattr(ports, public) is getattr(importlib.import_module(module), private)


def test_all_lists_exactly_the_ports():
    assert set(ports.__all__) == {public for public, _, _ in _PORTS}
