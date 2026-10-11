"""Django loads an installed app's convention modules by name.

From healthchecks: ``hc/*/admin.py``, ``hc/front/models.py``, ``apps.py``,
``management/commands/*.py`` and ``templatetags/*.py`` have no importer, so
each was reported as an unreachable file, and the ``AppConfig`` classes and
the admin ``Media`` inner classes as unused exports.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.dynamic_hints import HintRegistry
from repowise.core.ingestion.dynamic_hints.django import DjangoDynamicHints

_ADMIN = """from django.contrib import admin
from .models import Item, Order


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    class Media:
        css = {"all": ("item.css",)}


class OrderAdmin(admin.ModelAdmin):
    pass


admin.site.register(Order, OrderAdmin)


class UnregisteredAdmin(admin.ModelAdmin):
    pass


def dead_helper():
    return 1
"""

_APPS = """from django.apps import AppConfig


class ShopConfig(AppConfig):
    name = "proj.shop"


def unused_in_apps():
    return 2
"""

_APP_FILES = {
    "__init__.py": "",
    "admin.py": _ADMIN,
    "apps.py": _APPS,
    "models.py": "class Item:\n    pass\n\n\nclass Order:\n    pass\n",
    "signals.py": "def on_save():\n    return 3\n",
    "views.py": "def index():\n    return 4\n",
    "templatetags/__init__.py": "",
    "templatetags/extras.py": "X = 1\n",
    "templatetags/nested/deep.py": "Y = 1\n",
    "management/__init__.py": "",
    "management/commands/__init__.py": "",
    "management/commands/prune.py": "class Command:\n    pass\n",
    "management/commands/_shared.py": "Z = 1\n",
}


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(root: Path, settings: str, settings_path: str = "proj/settings.py") -> None:
    _write(root, "proj/__init__.py")
    for rel, text in _APP_FILES.items():
        _write(root, f"proj/shop/{rel}", text)
    _write(root, settings_path, settings)


def _edges(root: Path, source: str = "proj/settings.py") -> dict[str, tuple[str, tuple[str, ...]]]:
    return {
        e.target: (e.edge_type, e.imported_names)
        for e in DjangoDynamicHints().extract(root)
        if e.source == source
    }


_SHOP_EDGES = {
    "proj/shop/__init__.py": ("dynamic_imports", ()),
    "proj/shop/admin.py": ("dynamic_uses", ("ItemAdmin", "Media", "OrderAdmin")),
    "proj/shop/apps.py": ("dynamic_uses", ("ShopConfig",)),
    "proj/shop/models.py": ("dynamic_imports", ()),
    "proj/shop/templatetags/extras.py": ("dynamic_imports", ()),
    "proj/shop/management/commands/prune.py": ("dynamic_imports", ()),
}


def test_installed_app_convention_modules_get_edges(tmp_path: Path) -> None:
    _project(tmp_path, 'INSTALLED_APPS = ("django.contrib.admin", "proj.shop")\n')
    # No edge to signals, views, a nested templatetags module, a ``_`` command.
    assert _edges(tmp_path) == _SHOP_EDGES


def test_extended_and_summed_settings_are_read(tmp_path: Path) -> None:
    _project(tmp_path, 'BASE = ["django.contrib.admin"]\nINSTALLED_APPS = BASE + ["proj.shop"]\n')
    assert _edges(tmp_path) == _SHOP_EDGES
    _write(tmp_path, "proj/settings.py", 'INSTALLED_APPS = []\nINSTALLED_APPS += ["proj.shop"]\n')
    assert _edges(tmp_path) == _SHOP_EDGES


def test_split_settings_link_from_each_file_that_installs_the_app(tmp_path: Path) -> None:
    _project(tmp_path, 'INSTALLED_APPS = ["proj.shop"]\n', "proj/settings/base.py")
    _write(tmp_path, "proj/settings/__init__.py")
    _write(tmp_path, "proj/settings/dev.py", "DEBUG = True\n")
    assert _edges(tmp_path, "proj/settings/base.py") == _SHOP_EDGES
    assert _edges(tmp_path, "proj/settings/dev.py") == {}


def test_an_app_config_path_names_the_app_package(tmp_path: Path) -> None:
    _project(tmp_path, 'INSTALLED_APPS = ["proj.shop.apps.ShopConfig"]\n')
    assert "proj/shop/admin.py" in _edges(tmp_path)


def test_an_apps_package_config_path_names_the_app_package(tmp_path: Path) -> None:
    _project(tmp_path, 'INSTALLED_APPS = ["proj.shop.apps.ShopConfig"]\n')
    (tmp_path / "proj/shop/apps.py").unlink()
    _write(tmp_path, "proj/shop/apps/__init__.py", _APPS)
    _write(tmp_path, "proj/shop/apps/admin.py", "")
    edges = _edges(tmp_path)
    assert "proj/shop/admin.py" in edges
    assert "proj/shop/apps/admin.py" not in edges


def test_an_app_not_installed_gets_no_edges(tmp_path: Path) -> None:
    _project(tmp_path, 'INSTALLED_APPS = ["django.contrib.admin"]\n')
    assert _edges(tmp_path) == {}


def test_a_repo_without_django_settings_gets_no_edges(tmp_path: Path) -> None:
    for rel, text in _APP_FILES.items():
        _write(tmp_path, f"shop/{rel}", text)
    assert DjangoDynamicHints().extract(tmp_path) == []


def _dead_code(repo: Path) -> set[tuple[DeadCodeKind, str, str | None]]:
    builder = GraphBuilder(repo_path=repo)
    parser = ASTParser()
    paths = []
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
        paths.append(fi.path)
    builder.build()
    builder.add_dynamic_edges(HintRegistry().extract_all(repo, file_paths=paths))
    report = DeadCodeAnalyzer(builder.graph(), git_meta_map={}).analyze(
        {"detect_zombie_packages": False, "min_confidence": 0.0}
    )
    return {(f.kind, f.file_path, f.symbol_name) for f in report.findings}


def test_only_what_django_loads_counts_as_used(tmp_path: Path) -> None:
    _project(tmp_path, 'INSTALLED_APPS = ["proj.shop"]\n')
    found = _dead_code(tmp_path)
    unreachable = {path for kind, path, _ in found if kind is DeadCodeKind.UNREACHABLE_FILE}
    assert unreachable >= {
        "proj/shop/signals.py",
        "proj/shop/views.py",
        "proj/shop/templatetags/nested/deep.py",
        "proj/shop/management/commands/_shared.py",
    }
    assert not unreachable & {
        "proj/shop/admin.py",
        "proj/shop/apps.py",
        "proj/shop/models.py",
        "proj/shop/templatetags/extras.py",
        "proj/shop/management/commands/prune.py",
    }
    exports = {(path, name) for kind, path, name in found if kind is DeadCodeKind.UNUSED_EXPORT}
    # A dead helper beside the classes Django uses is still reported.
    assert {
        ("proj/shop/admin.py", "dead_helper"),
        ("proj/shop/admin.py", "UnregisteredAdmin"),
        ("proj/shop/apps.py", "unused_in_apps"),
    } <= exports
    assert not exports & {
        ("proj/shop/admin.py", "ItemAdmin"),
        ("proj/shop/admin.py", "Media"),
        ("proj/shop/admin.py", "OrderAdmin"),
        ("proj/shop/apps.py", "ShopConfig"),
    }
