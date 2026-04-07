from __future__ import annotations

import ast
import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor


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


def _extract_string_list(node: ast.expr) -> list[str]:
    """Extract string literals from an ast.List node."""
    results: list[str] = []
    if not isinstance(node, ast.List):
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

        # Collect all settings files
        settings_files: list[Path] = list(repo_root.rglob("settings.py"))
        for settings_dir in repo_root.rglob("settings"):
            if settings_dir.is_dir():
                settings_files.extend(settings_dir.glob("*.py"))

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
                            resolved = _app_to_path(app, repo_root)
                            if resolved:
                                edges.append(DynamicEdge(
                                    source=rel_settings,
                                    target=resolved,
                                    edge_type="dynamic_imports",
                                    hint_source=self.name,
                                ))

                    elif name == "ROOT_URLCONF":
                        module = _extract_string_value(node.value)
                        if module:
                            resolved = _module_to_path(module, repo_root)
                            if resolved:
                                edges.append(DynamicEdge(
                                    source=rel_settings,
                                    target=resolved,
                                    edge_type="dynamic_imports",
                                    hint_source=self.name,
                                ))

                    elif name == "MIDDLEWARE":
                        for middleware in _extract_string_list(node.value):
                            resolved = _module_to_path(middleware, repo_root)
                            if resolved:
                                edges.append(DynamicEdge(
                                    source=rel_settings,
                                    target=resolved,
                                    edge_type="dynamic_imports",
                                    hint_source=self.name,
                                ))

        return edges

    def _scan_urls(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []
        include_re = re.compile(r"""include\(\s*['\"]([\w\.]+)['\"]""")

        for urls_file in repo_root.rglob("urls.py"):
            try:
                source = urls_file.read_text(encoding="utf-8", errors="ignore")
                rel_urls = urls_file.relative_to(repo_root).as_posix()
            except Exception:
                continue

            for match in include_re.finditer(source):
                module = match.group(1)
                resolved = _module_to_path(module, repo_root)
                if resolved:
                    edges.append(DynamicEdge(
                        source=rel_urls,
                        target=resolved,
                        edge_type="url_route",
                        hint_source=self.name,
                    ))

        return edges
