"""Dynamic-hint extractor for Go reflect/plugin patterns."""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {"vendor", "node_modules", ".git", "bin"}

# reflect.TypeOf(Foo{}) / reflect.New(reflect.TypeOf(Foo{}))
_REFLECT_TYPEOF_RE = re.compile(r"reflect\.TypeOf\s*\(\s*([A-Za-z_]\w*)")
# plugin.Open("./plugin.so")
_PLUGIN_OPEN_RE = re.compile(r"plugin\.Open\s*\(\s*[\"']([^\"']+)[\"']")
# plugin.Lookup("Foo")
_PLUGIN_LOOKUP_RE = re.compile(r"\.Lookup\s*\(\s*[\"']([A-Za-z_]\w*)[\"']")

_TYPE_DECL_RE = re.compile(r"^\s*type\s+([A-Z]\w*)\s+", re.MULTILINE)
_FUNC_DECL_RE = re.compile(r"^\s*func\s+(?:\(\s*\w+\s+\*?\w+\s*\)\s*)?([A-Z]\w*)", re.MULTILINE)


class GoDynamicHints(DynamicHintExtractor):
    """Discover Go reflect.TypeOf / plugin.Open / plugin.Lookup."""

    name = "go"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        type_to_file: dict[str, str] = {}
        func_to_file: dict[str, str] = {}
        sources: list[tuple[Path, str]] = []
        repo_root_resolved = repo_root.resolve()
        for src in repo_root.rglob("*.go"):
            try:
                rel_path = src.resolve().relative_to(repo_root_resolved)
            except ValueError:
                continue
            if any(part in _SKIP_DIRS for part in rel_path.parts):
                continue
            try:
                text = src.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = rel_path.as_posix()
            sources.append((src, text))
            for match in _TYPE_DECL_RE.finditer(text):
                type_to_file.setdefault(match.group(1), rel)
            for match in _FUNC_DECL_RE.finditer(text):
                func_to_file.setdefault(match.group(1), rel)

        for src, text in sources:
            try:
                rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue

            for match in _REFLECT_TYPEOF_RE.finditer(text):
                target = type_to_file.get(match.group(1))
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:reflect_typeof",
                    ))

            for match in _PLUGIN_OPEN_RE.finditer(text):
                edges.append(DynamicEdge(
                    source=rel,
                    target=f"external:go_plugin:{match.group(1)}",
                    edge_type="dynamic_imports",
                    hint_source=f"{self.name}:plugin_open",
                ))

            for match in _PLUGIN_LOOKUP_RE.finditer(text):
                target = func_to_file.get(match.group(1)) or type_to_file.get(match.group(1))
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:plugin_lookup",
                    ))

        return edges
