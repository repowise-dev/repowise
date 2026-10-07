from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from .base import DynamicEdge, DynamicHintExtractor

#: Reads a module's syntax tree and returns the class names Django uses there.
UsedNames = Callable[[ast.Module], set[str]]


def _last_segment(node: ast.expr) -> str:
    """``AppConfig`` for ``AppConfig``, ``apps.AppConfig`` and ``admin.register(...)``."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Subscript):  # ``ModelAdmin[Profile]``
        node = node.value
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ""


def _app_config_classes(tree: ast.Module) -> set[str]:
    """The ``AppConfig`` subclasses, which the app registry instantiates."""
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(_last_segment(base) == "AppConfig" for base in node.bases)
    }


def _names_passed_to_register(tree: ast.Module) -> set[str]:
    """Names given as an admin class to ``site.register(Model, Admin)``."""
    return {
        arg.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _last_segment(node.func) == "register"
        for arg in node.args[1:]
        if isinstance(arg, ast.Name)
    }


def _registered_admin_classes(tree: ast.Module) -> set[str]:
    """Admin classes registered by ``@admin.register`` or ``site.register``,
    and the classes nested in them (``Media``), which the admin reads."""
    passed = _names_passed_to_register(tree)
    registered = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and (node.name in passed or any(_last_segment(d) == "register" for d in node.decorator_list))
    ]
    return {node.name for node in registered} | {
        inner.name for node in registered for inner in node.body if isinstance(inner, ast.ClassDef)
    }


#: Modules Django loads from every installed app by convention, as paths
#: inside the app package (``*`` is one module name). No source file imports
#: them: the app registry imports ``apps`` and ``models``, the admin
#: autodiscovers ``admin``, the template engine loads every ``templatetags``
#: library and ``manage.py`` finds each ``management/commands`` module whose
#: name does not start with ``_``. Where Django also uses classes of the
#: module, the second item names them, so only those count as used and a dead
#: helper beside them is still reported. ``signals``, ``urls`` and ``views``
#: are not here: Django never loads those by name, an app imports them itself.
_APP_CONVENTION_MODULES: tuple[tuple[str, UsedNames | None], ...] = (
    ("apps.py", _app_config_classes),
    ("admin.py", _registered_admin_classes),
    ("models.py", None),
    ("templatetags/*.py", None),
    ("management/commands/[!_]*.py", None),
)


def _convention_match(rel_in_app: PurePosixPath) -> tuple[bool, UsedNames | None]:
    """Whether Django loads the app module at *rel_in_app*, and what names its used classes."""
    for pattern, used_names in _APP_CONVENTION_MODULES:
        # ``match`` anchors on the right only; equal depth anchors both ends.
        if len(rel_in_app.parts) == pattern.count("/") + 1 and rel_in_app.match(pattern):
            return True, used_names
    return False, None


def _used_names(path: Path, used_names: UsedNames | None) -> tuple[str, ...]:
    """The names *used_names* finds in the module at *path*, sorted; none if unreadable."""
    if used_names is None:
        return ()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return ()
    return tuple(sorted(used_names(tree)))


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
    module_name = entry[: entry.rindex(".")]
    module = _module_to_path(module_name, repo_root)
    if module is None:
        return None
    module_path = PurePosixPath(module)
    if module_path.name != "__init__.py":
        app_dir = module_path.parent
    elif module_path.parent.name == "apps":
        # An ``apps`` package inside the app (``polls/apps/__init__.py``).
        app_dir = module_path.parent.parent
    else:
        # The config sits in the app's own ``__init__`` (``polls.PollsConfig``).
        return module
    package_init = (app_dir / "__init__.py").as_posix()
    return package_init if (repo_root / package_init).exists() else None


def _extract_string_list(node: ast.expr) -> list[str]:
    """String literals of a list or tuple, or of a sum of them (``BASE + [...]``)."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _extract_string_list(node.left) + _extract_string_list(node.right)
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
        app_modules: dict[str, list[tuple[str, tuple[str, ...]]]] = {}

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
                # ``INSTALLED_APPS += [...]`` extends the setting in place.
                if not isinstance(node, (ast.Assign, ast.AugAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
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
        app_modules: dict[str, list[tuple[str, tuple[str, ...]]]],
    ) -> list[DynamicEdge]:
        """Edges from a settings file to an installed app and its convention modules.

        A module whose classes Django uses gets a ``dynamic_uses`` edge naming
        them; any other gets ``dynamic_imports``, which marks the module
        loaded and none of its symbols used.
        """
        init = _installed_app_init(app, repo_root)
        if init is None:
            return []
        if init not in app_modules:
            app_modules[init] = self._convention_modules(repo_root, init)
        return [
            DynamicEdge(
                source=rel_settings,
                target=target,
                edge_type="dynamic_uses" if names else "dynamic_imports",
                hint_source=self.name,
                imported_names=names,
            )
            for target, names in [(init, ()), *app_modules[init]]
        ]

    def _convention_modules(
        self, repo_root: Path, app_init: str
    ) -> list[tuple[str, tuple[str, ...]]]:
        """The modules under an app that Django loads by convention, with the names it uses."""
        app_dir = repo_root / PurePosixPath(app_init).parent
        found: list[tuple[str, tuple[str, ...]]] = []
        # Through ``_rglob`` so a file the index excludes gets no edge.
        for path in self._rglob(app_dir, "*.py"):
            # A package ``__init__`` is reached through its package already.
            if path.name == "__init__.py":
                continue
            rel_in_app = PurePosixPath(path.relative_to(app_dir).as_posix())
            loaded, used_names = _convention_match(rel_in_app)
            if loaded:
                rel = path.relative_to(repo_root).as_posix()
                found.append((rel, _used_names(path, used_names)))
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
