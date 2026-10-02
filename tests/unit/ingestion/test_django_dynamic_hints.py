"""Django loads an installed app's convention modules by name.

From healthchecks: ``hc/*/admin.py``, ``hc/front/models.py``, ``apps.py``,
``management/commands/*.py`` and ``templatetags/*.py`` have no importer, so
each was reported as an unreachable file, and the ``AppConfig`` classes and
the admin ``Media`` inner classes as unused exports.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion.dynamic_hints.django import DjangoDynamicHints
from tests.unit.dead_code._helpers import _build_graph

_APP_FILES = (
    "__init__.py",
    "admin.py",
    "apps.py",
    "models.py",
    "signals.py",
    "views.py",
    "helpers.py",
    "templatetags/__init__.py",
    "templatetags/extras.py",
    "templatetags/nested/deep.py",
    "management/__init__.py",
    "management/commands/__init__.py",
    "management/commands/prune.py",
    "management/commands/_shared.py",
)


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(root: Path, installed: str) -> None:
    for rel in _APP_FILES:
        _write(root, f"proj/shop/{rel}")
    _write(root, "proj/settings.py", f"INSTALLED_APPS = {installed}\n")


def _edges(root: Path) -> dict[str, str]:
    return {
        e.target: e.edge_type
        for e in DjangoDynamicHints().extract(root)
        if e.source == "proj/settings.py"
    }


def test_installed_app_convention_modules_get_edges(tmp_path: Path) -> None:
    _project(tmp_path, '("django.contrib.admin", "proj.shop")')
    assert _edges(tmp_path) == {
        "proj/shop/__init__.py": "dynamic_imports",
        "proj/shop/admin.py": "dynamic_uses",
        "proj/shop/apps.py": "dynamic_uses",
        "proj/shop/models.py": "dynamic_imports",
        "proj/shop/templatetags/extras.py": "dynamic_imports",
        "proj/shop/management/commands/prune.py": "dynamic_imports",
    }


def test_an_app_config_path_names_the_app_package(tmp_path: Path) -> None:
    _project(tmp_path, '["proj.shop.apps.ShopConfig"]')
    assert "proj/shop/admin.py" in _edges(tmp_path)


def test_an_app_not_installed_gets_no_edges(tmp_path: Path) -> None:
    _project(tmp_path, '["django.contrib.admin"]')
    assert _edges(tmp_path) == {}


def test_settings_edges_make_the_modules_live_for_dead_code(tmp_path: Path) -> None:
    _project(tmp_path, '["proj.shop"]')
    hints = DjangoDynamicHints().extract(tmp_path)
    nodes: dict[str, dict] = {
        f"proj/shop/{rel}": {"symbols": []} for rel in _APP_FILES if "__init__" not in rel
    }
    nodes["proj/settings.py"] = {"symbols": []}
    nodes["proj/shop/admin.py"] = {
        "symbols": [
            {"name": "Media", "kind": "class", "visibility": "public", "start_line": 3, "end_line": 4}
        ]
    }
    graph = _build_graph(nodes, [(e.source, e.target, {"edge_type": e.edge_type}) for e in hints])
    report = DeadCodeAnalyzer(graph, git_meta_map={}).analyze(
        {"detect_zombie_packages": False, "min_confidence": 0.0}
    )
    flagged = {
        (f.kind, f.file_path, f.symbol_name)
        for f in report.findings
        if f.file_path != "proj/settings.py"
    }
    unreachable = {path for kind, path, _ in flagged if kind is DeadCodeKind.UNREACHABLE_FILE}
    # Django never loads these by name; the app imports them itself.
    assert unreachable == {
        "proj/shop/signals.py",
        "proj/shop/views.py",
        "proj/shop/helpers.py",
        "proj/shop/templatetags/nested/deep.py",
        "proj/shop/management/commands/_shared.py",
    }
    assert (DeadCodeKind.UNUSED_EXPORT, "proj/shop/admin.py", "Media") not in flagged
