"""Dynamic-hint extractor for C function pointers and dlopen/dlsym."""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {"build", "out", "node_modules", ".git", "third_party"}

# fp = some_function;  (assigning a function name to a variable — common C function-pointer wiring)
_FN_PTR_ASSIGN_RE = re.compile(
    r"\b(\w+)\s*=\s*([a-z_]\w*)\s*;",
)
# dlsym(handle, "name")
_DLSYM_RE = re.compile(r"dlsym\s*\(\s*\w+\s*,\s*[\"']([A-Za-z_]\w*)[\"']")
# dlopen("./libfoo.so", ...)
_DLOPEN_RE = re.compile(r"dlopen\s*\(\s*[\"']([^\"']+)[\"']")

_FUNC_DEF_RE = re.compile(
    r"^\s*(?:static\s+|extern\s+|inline\s+)*[\w\*\s]+\s+([a-z_]\w*)\s*\([^;]*\)\s*\{",
    re.MULTILINE,
)


class CDynamicHints(DynamicHintExtractor):
    """Discover C function-pointer assignments and dlopen/dlsym usage."""

    name = "c"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        func_to_files: dict[str, list[str]] = {}
        sources: list[tuple[Path, str]] = []
        repo_root_resolved = repo_root.resolve()
        for ext in (".c", ".h"):
            for src in repo_root.rglob(f"*{ext}"):
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
                for match in _FUNC_DEF_RE.finditer(text):
                    func_to_files.setdefault(match.group(1), []).append(rel)

        for src, text in sources:
            try:
                rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue

            seen: set[tuple[str, str]] = set()

            for match in _FN_PTR_ASSIGN_RE.finditer(text):
                # Right-hand side must be a known function name to count as
                # a function-pointer assignment (filters out variable copies).
                name = match.group(2)
                if name in func_to_files:
                    for target in func_to_files[name]:
                        key = (target, "fn_ptr")
                        if key in seen or target == rel:
                            continue
                        seen.add(key)
                        edges.append(DynamicEdge(
                            source=rel, target=target,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:fn_ptr",
                        ))

            for match in _DLSYM_RE.finditer(text):
                for target in func_to_files.get(match.group(1), []):
                    if target != rel:
                        edges.append(DynamicEdge(
                            source=rel, target=target,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:dlsym",
                        ))

            for match in _DLOPEN_RE.finditer(text):
                edges.append(DynamicEdge(
                    source=rel,
                    target=f"external:dlopen:{match.group(1)}",
                    edge_type="dynamic_imports",
                    hint_source=f"{self.name}:dlopen",
                ))

        return edges
