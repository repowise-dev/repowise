"""Dynamic-hint extractor for C++ function pointers, dlopen/dlsym, and Qt
``QObject::connect`` signal/slot wiring.

Mirrors :mod:`.c` but on C++ extensions (``.cc``/``.cpp``/``.cxx``/``.hpp``/
``.hxx``) and adds the Qt connect-string idiom — ``QObject::connect(s,
SIGNAL(sig()), r, SLOT(slot()))`` — which wires a method into a runtime
dispatch table that the static call graph never sees.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {
    "build", "out", "cmake-build-debug", "cmake-build-release",
    "node_modules", ".git", "third_party", "vendor", "_deps",
}

_CPP_EXTS: tuple[str, ...] = (".cc", ".cpp", ".cxx", ".c++", ".hpp", ".hxx", ".h")

# fp = some_function;  (function-pointer wiring — RHS must be a known
# function name to count as a function-pointer assignment).
_FN_PTR_ASSIGN_RE = re.compile(r"\b(\w+)\s*=\s*([a-zA-Z_]\w*)\s*;")

# Designated-initializer style: ``.callback = my_callback,``
_DESIG_INIT_RE = re.compile(r"\.\s*\w+\s*=\s*&?\s*([a-zA-Z_]\w*)\s*[,}]")

_DLSYM_RE = re.compile(r"dlsym\s*\(\s*\w+\s*,\s*[\"']([A-Za-z_]\w*)[\"']")
_DLOPEN_RE = re.compile(r"dlopen\s*\(\s*[\"']([^\"']+)[\"']")
_GETPROCADDR_RE = re.compile(r"GetProcAddress\s*\(\s*\w+\s*,\s*[\"']([A-Za-z_]\w*)[\"']")

# Qt old-style string connect: ``QObject::connect(sender, SIGNAL(sig()), recv, SLOT(slot()))``
_QT_CONNECT_STR_RE = re.compile(
    r"\bconnect\s*\([^,]+,\s*SIGNAL\s*\(\s*([A-Za-z_]\w*)\s*\([^)]*\)\s*\)\s*,"
    r"\s*[^,]+,\s*SLOT\s*\(\s*([A-Za-z_]\w*)\s*\([^)]*\)\s*\)"
)
# Qt new-style member-pointer connect: ``connect(s, &Sender::sig, r, &Recv::slot)``
_QT_CONNECT_PTR_RE = re.compile(
    r"\bconnect\s*\([^,]+,\s*&\s*[\w:]+::([A-Za-z_]\w*)\s*,"
    r"\s*[^,]+,\s*&\s*[\w:]+::([A-Za-z_]\w*)"
)

# Function/method definitions — best-effort, intentionally loose.
# Matches a top-level function or out-of-class method definition with a
# body. We capture the bare identifier as the function name; methods
# defined as ``ReturnType Class::method(...) { ... }`` also match
# (capture is ``method``).
_FUNC_DEF_RE = re.compile(
    r"^\s*(?:[\w:<>,\s\*&]+?\s+)?(?:[\w]+::)?([a-zA-Z_]\w*)\s*\([^;{}]*\)\s*"
    r"(?:const\s+)?(?:noexcept\s*(?:\([^)]*\))?\s*)?(?:override\s+)?"
    r"(?:->\s*[\w:<>,\s\*&]+\s+)?\{",
    re.MULTILINE,
)


class CppDynamicHints(DynamicHintExtractor):
    """Discover C++ function-pointer assignments, dlopen/dlsym, Qt connect."""

    name = "cpp"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        func_to_files: dict[str, list[str]] = {}
        sources: list[tuple[Path, str, str]] = []
        repo_root_resolved = repo_root.resolve()
        for ext in _CPP_EXTS:
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
                sources.append((src, text, rel))
                for match in _FUNC_DEF_RE.finditer(text):
                    func_to_files.setdefault(match.group(1), []).append(rel)

        for _src, text, rel in sources:
            seen: set[tuple[str, str]] = set()

            def _emit(name: str, kind: str) -> None:
                for target in func_to_files.get(name, ()):
                    key = (target, kind)
                    if key in seen or target == rel:
                        continue
                    seen.add(key)
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:{kind}",
                    ))

            for m in _FN_PTR_ASSIGN_RE.finditer(text):
                _emit(m.group(2), "fn_ptr")

            for m in _DESIG_INIT_RE.finditer(text):
                _emit(m.group(1), "desig_init")

            for m in _DLSYM_RE.finditer(text):
                _emit(m.group(1), "dlsym")

            for m in _GETPROCADDR_RE.finditer(text):
                _emit(m.group(1), "getprocaddress")

            for m in _QT_CONNECT_STR_RE.finditer(text):
                _emit(m.group(1), "qt_signal")
                _emit(m.group(2), "qt_slot")
            for m in _QT_CONNECT_PTR_RE.finditer(text):
                _emit(m.group(1), "qt_signal")
                _emit(m.group(2), "qt_slot")

            for m in _DLOPEN_RE.finditer(text):
                edges.append(DynamicEdge(
                    source=rel,
                    target=f"external:dlopen:{m.group(1)}",
                    edge_type="dynamic_imports",
                    hint_source=f"{self.name}:dlopen",
                ))

        return edges
