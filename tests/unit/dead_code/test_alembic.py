"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from tests.unit.dead_code._helpers import _build_graph


def test_alembic_versions_never_flagged_as_unreachable():
    """Files under <root>/alembic/versions/*.py are loaded reflectively by
    Alembic at runtime — they have no static importer and must not be
    surfaced as unreachable on any Alembic-using repo (generic Python
    convention)."""
    g = _build_graph(
        nodes={
            "myapp/alembic/versions/0001_init.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "symbols": [],
            },
            "myapp/alembic/versions/0002_add_users.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "symbols": [],
            },
        },
        edges=[],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze({"detect_unused_exports": False, "detect_zombie_packages": False})
    paths = {f.file_path for f in report.findings}
    assert "myapp/alembic/versions/0001_init.py" not in paths
    assert "myapp/alembic/versions/0002_add_users.py" not in paths


def test_alembic_upgrade_downgrade_never_flagged_as_unused():
    """``upgrade()`` / ``downgrade()`` in Alembic migration scripts are
    called via reflection by the Alembic runner — the file pattern
    exemption (``*/alembic/versions/*.py``) already covers them, so the
    symbols inside must not surface as unused exports either."""
    g = _build_graph(
        nodes={
            "myapp/alembic/versions/0001_init.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "symbols": [
                    {
                        "name": "upgrade",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 5,
                        "complexity_estimate": 1,
                    },
                    {
                        "name": "downgrade",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 7,
                        "end_line": 11,
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
    assert "upgrade" not in names
    assert "downgrade" not in names
