"""Dynamic-hint extractor for PHP reflective / container patterns."""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {"vendor", "node_modules", ".git", "storage", "bootstrap"}

# call_user_func(['Foo', 'method']) / call_user_func('Foo::method')
_CALL_USER_FUNC_RE = re.compile(
    r"call_user_func(?:_array)?\s*\(\s*(?:\[\s*[\"']([A-Z]\w*)[\"']\s*,\s*[\"']\w+[\"']\s*\]|[\"']([A-Z]\w*)::\w+[\"'])"
)
# new ReflectionClass(Foo::class) / new ReflectionClass('Foo')
_REFLECTION_CLASS_RE = re.compile(
    r"new\s+ReflectionClass\s*\(\s*(?:([A-Z]\w*)::class|[\"']([A-Z]\w*)[\"'])"
)
# $container->get(Foo::class) / app(Foo::class) / resolve(Foo::class)
_CONTAINER_GET_RE = re.compile(
    r"(?:->\s*get|\bapp|\bresolve|\bmake)\s*\(\s*([A-Z]\w*)::class"
)
# new $varname(...)  — pure variable instantiation, no static target
_NEW_DOLLAR_RE = re.compile(r"new\s+\$\w+\s*\(")

_CLASS_DECL_RE = re.compile(r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait|enum)\s+([A-Z]\w*)", re.MULTILINE)


class PhpDynamicHints(DynamicHintExtractor):
    """Discover PHP reflection, container, and dynamic-instantiation patterns."""

    name = "php"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        type_to_file: dict[str, str] = {}
        php_files: list[tuple[Path, str]] = []
        for src in repo_root.rglob("*.php"):
            if any(part in _SKIP_DIRS for part in src.parts):
                continue
            try:
                text = src.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            try:
                rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue
            php_files.append((src, text))
            for match in _CLASS_DECL_RE.finditer(text):
                type_to_file.setdefault(match.group(1), rel)

        for src, text in php_files:
            try:
                rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue

            for match in _CALL_USER_FUNC_RE.finditer(text):
                name = match.group(1) or match.group(2)
                target = type_to_file.get(name)
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:call_user_func",
                    ))

            for match in _REFLECTION_CLASS_RE.finditer(text):
                name = match.group(1) or match.group(2)
                target = type_to_file.get(name)
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:reflection_class",
                    ))

            for match in _CONTAINER_GET_RE.finditer(text):
                target = type_to_file.get(match.group(1))
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:container_get",
                    ))

            if _NEW_DOLLAR_RE.search(text):
                edges.append(DynamicEdge(
                    source=rel,
                    target=f"external:php_dynamic:new_var",
                    edge_type="dynamic_uses",
                    hint_source=f"{self.name}:new_var",
                ))

        return edges
