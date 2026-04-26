"""Dynamic-hint extractor for Scala reflection and implicit/given patterns."""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {"target", "project", ".bloop", ".metals", "node_modules", ".git"}

_CLASS_FORNAME_RE = re.compile(r"Class\.forName\s*\(\s*[\"']([\w.]+)[\"']")
_RUNTIME_MIRROR_RE = re.compile(r"runtimeMirror\s*\(")
_REFLECT_RUNTIME_RE = re.compile(r"reflect\.runtime\b")
# given Foo: Bar = ... / implicit val foo: Bar = ...
_GIVEN_DECL_RE = re.compile(r"\bgiven\s+(\w+)?\s*:\s*([A-Z]\w*)")
_IMPLICIT_VAL_RE = re.compile(r"\bimplicit\s+val\s+\w+\s*:\s*([A-Z]\w*)")

_TYPE_DECL_RE = re.compile(r"\b(?:class|trait|object|enum)\s+([A-Z]\w*)")


class ScalaDynamicHints(DynamicHintExtractor):
    """Discover Scala reflective class loads + implicit/given resolution markers."""

    name = "scala"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        type_to_file: dict[str, str] = {}
        sources: list[tuple[Path, str]] = []
        for src in repo_root.rglob("*.scala"):
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
            sources.append((src, text))
            for match in _TYPE_DECL_RE.finditer(text):
                type_to_file.setdefault(match.group(1), rel)

        def _short(name: str) -> str:
            return name.rsplit(".", 1)[-1]

        for src, text in sources:
            try:
                rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue

            for match in _CLASS_FORNAME_RE.finditer(text):
                target = type_to_file.get(_short(match.group(1)))
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:class_forname",
                    ))

            if _RUNTIME_MIRROR_RE.search(text) or _REFLECT_RUNTIME_RE.search(text):
                edges.append(DynamicEdge(
                    source=rel,
                    target=f"external:scala_reflect",
                    edge_type="dynamic_uses",
                    hint_source=f"{self.name}:runtime_mirror",
                ))

            for match in _GIVEN_DECL_RE.finditer(text):
                target = type_to_file.get(match.group(2))
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:given",
                    ))

            for match in _IMPLICIT_VAL_RE.finditer(text):
                target = type_to_file.get(match.group(1))
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:implicit_val",
                    ))

        return edges
