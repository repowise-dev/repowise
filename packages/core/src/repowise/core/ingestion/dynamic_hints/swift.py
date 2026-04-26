"""Dynamic-hint extractor for Swift reflective and selector patterns."""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {".build", "Pods", "DerivedData", ".git", "node_modules"}

_NSCLASS_FROM_STRING_RE = re.compile(r"NSClassFromString\s*\(\s*[\"']([A-Z]\w*)[\"']")
_NSSTRING_FROM_CLASS_RE = re.compile(r"NSStringFromClass\s*\(\s*([A-Z]\w*)")
_SELECTOR_INIT_RE = re.compile(r"Selector\s*\(\s*[\"']([A-Za-z_]\w*)")
_HASH_SELECTOR_RE = re.compile(r"#selector\s*\(\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)")
# value(forKey: "name") / setValue(_, forKey: "name")
_KVC_RE = re.compile(r"\bvalue\s*\(\s*forKey\s*:\s*[\"']([A-Za-z_]\w*)[\"']")

_TYPE_DECL_RE = re.compile(
    r"\b(?:class|struct|enum|actor|protocol)\s+([A-Z]\w*)"
)
_FUNC_DECL_RE = re.compile(r"\bfunc\s+([A-Za-z_]\w*)")


class SwiftDynamicHints(DynamicHintExtractor):
    """Discover Swift NSClassFromString / Selector / KVC dynamic dispatch."""

    name = "swift"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        type_to_file: dict[str, str] = {}
        func_to_files: dict[str, list[str]] = {}
        sources: list[tuple[Path, str]] = []
        for src in repo_root.rglob("*.swift"):
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
            for match in _FUNC_DECL_RE.finditer(text):
                func_to_files.setdefault(match.group(1), []).append(rel)

        for src, text in sources:
            try:
                rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue

            for match in _NSCLASS_FROM_STRING_RE.finditer(text):
                target = type_to_file.get(match.group(1))
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:nsclass_from_string",
                    ))

            for match in _NSSTRING_FROM_CLASS_RE.finditer(text):
                target = type_to_file.get(match.group(1))
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:nsstring_from_class",
                    ))

            for match in _SELECTOR_INIT_RE.finditer(text):
                for target in func_to_files.get(match.group(1), []):
                    if target != rel:
                        edges.append(DynamicEdge(
                            source=rel, target=target,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:selector",
                        ))

            for match in _HASH_SELECTOR_RE.finditer(text):
                name = match.group(1).rsplit(".", 1)[-1]
                for target in func_to_files.get(name, []):
                    if target != rel:
                        edges.append(DynamicEdge(
                            source=rel, target=target,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:hash_selector",
                        ))

            for match in _KVC_RE.finditer(text):
                edges.append(DynamicEdge(
                    source=rel,
                    target=f"external:swift_kvc:{match.group(1)}",
                    edge_type="dynamic_uses",
                    hint_source=f"{self.name}:kvc",
                ))

        return edges
