"""Dynamic-hint extractor for C function pointers and dlopen/dlsym."""

from __future__ import annotations

import re
from functools import partial
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
_STATIC_RE = re.compile(r"\s*(?:extern\s+|inline\s+)*static\b")


def _scan_source(text: str, rel: str, *, source_file: bool) -> list:
    """What one file contributes, as ``[defs, fn_ptrs, dlsyms, dlopens]``.

    *defs* is ``[name, external]`` per definition in order, external when
    another file could name it. *fn_ptrs* is each assigned name once, in
    order: the join remembers what it emitted per target, so a repeat adds
    nothing. *dlsyms* and *dlopens* keep every occurrence, as the join does.
    """
    # A ``static`` function in a .c file (*source_file*) has internal linkage:
    # no other file can name it.
    defs = [
        [m.group(1), not (source_file and _STATIC_RE.match(m.group(0)))]
        for m in _FUNC_DEF_RE.finditer(text)
    ]
    fn_ptrs = list(dict.fromkeys(m.group(2) for m in _FN_PTR_ASSIGN_RE.finditer(text)))
    dlsyms = [m.group(1) for m in _DLSYM_RE.finditer(text)]
    dlopens = [m.group(1) for m in _DLOPEN_RE.finditer(text)]
    return [defs, fn_ptrs, dlsyms, dlopens]


class CDynamicHints(DynamicHintExtractor):
    """Discover C function-pointer assignments and dlopen/dlsym usage."""

    name = "c"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        func_to_files: dict[str, list[str]] = {}
        local_to: dict[str, set[str]] = {}
        sources: list[tuple[str, list]] = []
        repo_root_resolved = repo_root.resolve()
        for ext in (".c", ".h"):
            for src in self._rglob(repo_root, f"*{ext}"):
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
                # The glob decides, as it always has; a path matches one glob,
                # so the cache's path key covers it.
                scan = partial(_scan_source, source_file=ext == ".c")
                scanned = self._scan(rel, text, scan)
                sources.append((rel, scanned))
                for name, external in scanned[0]:
                    local_to.setdefault(name, set()).add(rel)
                    if external:
                        func_to_files.setdefault(name, []).append(rel)

        for rel, (_defs, fn_ptrs, dlsyms, dlopens) in sources:
            seen: set[tuple[str, str]] = set()

            for name in fn_ptrs:
                # Right-hand side must be a known function name to count as
                # a function-pointer assignment (filters out variable copies).
                # A file that defines the name binds the reference there.
                if name in func_to_files and rel not in local_to[name]:
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

            for name in dlsyms:
                for target in func_to_files.get(name, []):
                    if target != rel:
                        edges.append(DynamicEdge(
                            source=rel, target=target,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:dlsym",
                        ))

            for library in dlopens:
                edges.append(DynamicEdge(
                    source=rel,
                    target=f"external:dlopen:{library}",
                    edge_type="dynamic_imports",
                    hint_source=f"{self.name}:dlopen",
                ))

        return edges
