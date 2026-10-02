from __future__ import annotations

import ast
import re
from pathlib import Path, PurePosixPath

from ..models import DynamicKind
from .base import DynamicEdge, DynamicHintExtractor

#: Modules Django loads from every installed app by convention, as paths
#: inside the app package (``*`` is one module name). No source file imports
#: them: the app registry imports ``apps`` and ``models``, the admin
#: autodiscovers ``admin``, the template engine loads every ``templatetags``
#: library and ``manage.py`` finds each ``management/commands`` module whose
#: name does not start with ``_``. ``dynamic_uses`` where Django also consumes
#: the module's classes (the ``AppConfig`` subclass, the registered
#: ``ModelAdmin`` classes and their inner ``Media``), ``dynamic_imports`` where
#: it only loads the module. ``signals``, ``urls`` and ``views`` are not here:
#: Django never loads those by name, an app imports them itself.
_APP_CONVENTION_MODULES: tuple[tuple[str, DynamicKind], ...] = (
    ("apps.py", "dynamic_uses"),
    ("admin.py", "dynamic_uses"),
    ("models.py", "dynamic_imports"),
    ("templatetags/*.py", "dynamic_imports"),
    ("management/commands/[!_]*.py", "dynamic_imports"),
)


def _convention_kind(rel_in_app: PurePosixPath) -> DynamicKind | None:
    """How Django loads the app module at *rel_in_app*, or ``None`` if it does not."""
    for pattern, kind in _APP_CONVENTION_MODULES:
        # ``match`` anchors on the right only; equal depth anchors both ends.
        if len(rel_in_app.parts) == pattern.count("/") + 1 and rel_in_app.match(pattern):
            return kind
    return None


def _app_to_path(app: str, repo_root: Path) -> str | None:
    """Attempt to resolve a dotted app name to an __init__.py under repo_root."""
    # Try direct directory: myapp/__init__.py
    direct = repo_root / app / "__init__.py"
    if direct.exists():
        return str(direct.relative_to(repo_root).as_posix())
    # Try dotted path: myapp.sub → myapp/sub/__init__.py
    dotted = app.replace(".", "/") + "/__init__.py"
    dotted_path = repo_root / dotted
    if dotted_path.exists():
        return str(dotted_path.relative_to(repo_root).as_posix())
    return None


def _module_to_path(module: str, repo_root: Path) -> str | None:
    """Attempt to resolve a dotted module string to a .py file under repo_root."""
    as_path = module.replace(".", "/")
    # Try as a .py file directly
    candidate = repo_root / (as_path + ".py")
    if candidate.exists():
        return str(candidate.relative_to(repo_root).as_posix())
    # Try as __init__.py inside a package
    candidate = repo_root / as_path / "__init__.py"
    if candidate.exists():
        return str(candidate.relative_to(repo_root).as_posix())
    return None


def _installed_app_init(entry: str, repo_root: Path) -> str | None:
    """The ``__init__.py`` of the app an ``INSTALLED_APPS`` entry names.

    An entry is an app package (``polls``) or an ``AppConfig`` path
    (``polls.apps.PollsConfig``), whose app is the package holding the module.
    """
    init = _app_to_path(entry, repo_root)
    if init is not None or "." not in entry:
        return init
    module = _module_to_path(entry.rsplit(".", 1)[0], repo_root)
    if module is None or PurePosixPath(module).name == "__init__.py":
        return module
    package_init = (PurePosixPath(module).parent / "__init__.py").as_posix()
    return package_init if (repo_root / package_init).exists() else None


def _extract_string_list(node: ast.expr) -> list[str]:
    """Extract string literals from an ast.List or ast.Tuple node."""
    results: list[str] = []
    if not isinstance(node, (ast.List, ast.Tuple)):
        return results
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            results.append(elt.value)
    return results


def _extract_string_value(node: ast.expr) -> str | None:
    """Extract a string literal value from a node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class DjangoDynamicHints(DynamicHintExtractor):
    name = "django_settings"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []
        edges.extend(self._scan_settings(repo_root))
        edges.extend(self._scan_urls(repo_root))
        return edges

    def _scan_settings(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []
        # Convention modules per app ``__init__``, shared by every settings
        # file that installs the app (``base.py``, ``dev.py``, ``prod.py``).
        app_modules: dict[str, list[tuple[str, DynamicKind]]] = {}

        # Collect all settings files
        settings_files: list[Path] = list(self._rglob(repo_root, "settings.py"))
        for settings_dir in self._rglob(repo_root, "settings"):
            if settings_dir.is_dir():
                # Subtree query through _rglob, not a raw Path.glob: when the
                # shared snapshot is fed from the traverser's file list this
                # keeps gitignored files (local_settings.py) out of the scan.
                # The parent check preserves glob's single-level semantics.
                settings_files.extend(
                    p for p in self._rglob(settings_dir, "*.py") if p.parent == settings_dir
                )

        for settings_file in settings_files:
            try:
                source = settings_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(settings_file))
            except Exception:
                continue

            try:
                rel_settings = settings_file.relative_to(repo_root).as_posix()
            except ValueError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if not (isinstance(target, ast.Name)):
                        continue
                    name = target.id

                    if name == "INSTALLED_APPS":
                        for app in _extract_string_list(node.value):
                            edges.extend(
                                self._installed_app_edges(rel_settings, app, repo_root, app_modules)
                            )

                    elif name == "ROOT_URLCONF":
                        module = _extract_string_value(node.value)
                        if module:
                            resolved = _module_to_path(module, repo_root)
                            if resolved:
                                edges.append(
                                    DynamicEdge(
                                        source=rel_settings,
                                        target=resolved,
                                        edge_type="dynamic_imports",
                                        hint_source=self.name,
                                    )
                                )

                    elif name == "MIDDLEWARE":
                        for middleware in _extract_string_list(node.value):
                            resolved = _module_to_path(middleware, repo_root)
                            if resolved:
                                edges.append(
                                    DynamicEdge(
                                        source=rel_settings,
                                        target=resolved,
                                        edge_type="dynamic_imports",
                                        hint_source=self.name,
                                    )
                                )

        return edges

    def _installed_app_edges(
        self,
        rel_settings: str,
        app: str,
        repo_root: Path,
        app_modules: dict[str, list[tuple[str, DynamicKind]]],
    ) -> list[DynamicEdge]:
        """Edges from a settings file to an installed app and its convention modules."""
        init = _installed_app_init(app, repo_root)
        if init is None:
            return []
        if init not in app_modules:
            app_modules[init] = self._convention_modules(repo_root, init)
        targets = [(init, "dynamic_imports"), *app_modules[init]]
        return [
            DynamicEdge(source=rel_settings, target=target, edge_type=kind, hint_source=self.name)
            for target, kind in targets
        ]

    def _convention_modules(self, repo_root: Path, app_init: str) -> list[tuple[str, DynamicKind]]:
        """The modules under an app that Django loads by convention, with their edge kind."""
        app_dir = repo_root / PurePosixPath(app_init).parent
        found: list[tuple[str, DynamicKind]] = []
        # Through ``_rglob`` so a file the index excludes gets no edge.
        for path in self._rglob(app_dir, "*.py"):
            # A package ``__init__`` is reached through its package already.
            if path.name == "__init__.py":
                continue
            kind = _convention_kind(PurePosixPath(path.relative_to(app_dir).as_posix()))
            if kind is not None:
                found.append((path.relative_to(repo_root).as_posix(), kind))
        return found

    def _scan_urls(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []
        include_re = re.compile(r"""include\(\s*['\"]([\w\.]+)['\"]""")

        for urls_file in self._rglob(repo_root, "urls.py"):
            try:
                source = urls_file.read_text(encoding="utf-8", errors="ignore")
                rel_urls = urls_file.relative_to(repo_root).as_posix()
            except Exception:
                continue

            for match in include_re.finditer(source):
                module = match.group(1)
                resolved = _module_to_path(module, repo_root)
                if resolved:
                    edges.append(
                        DynamicEdge(
                            source=rel_urls,
                            target=resolved,
                            edge_type="url_route",
                            hint_source=self.name,
                        )
                    )

        return edges
