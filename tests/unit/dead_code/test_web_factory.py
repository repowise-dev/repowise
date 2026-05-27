"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

import pytest

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from tests.unit.dead_code._helpers import _build_graph


@pytest.mark.parametrize(
    "factory_name",
    [
        "create_app",
        "make_app",
        "application",
        "asgi_app",
        "wsgi_app",
        "get_asgi_application",
        "get_wsgi_application",
    ],
)
def test_python_web_factory_not_flagged(factory_name):
    """FastAPI / Flask / Tornado / aiohttp / Django entry symbols are
    loaded by an external server via dotted-path string
    (``module:create_app`` / ``module:application``) — no graph edge
    exists from the launching server. These conventional names must be
    in the entry-point allowlist so the unused-export pass skips them."""
    g = _build_graph(
        nodes={
            "myapp/server.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": factory_name,
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 20,
                        "complexity_estimate": 1,
                    },
                ],
            },
        },
        edges=[],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze({"detect_unreachable_files": False, "detect_zombie_packages": False})
    names = {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}
    assert factory_name not in names
