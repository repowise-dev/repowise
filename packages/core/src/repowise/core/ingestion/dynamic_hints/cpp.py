"""Dynamic-hint extractor for C++ function pointers, dlopen/dlsym, and Qt
``QObject::connect`` signal/slot wiring.

Mirrors :mod:`.c` but on the C++ extensions in ``_CPP_EXTS`` below, and adds
the Qt connect-string idiom — ``QObject::connect(s,
SIGNAL(sig()), r, SLOT(slot()))`` — which wires a method into a runtime
dispatch table that the static call graph never sees.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..languages.specs.cpp import INCLUDE_FRAGMENT_EXTENSIONS
from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {
    "build", "out", "cmake-build-debug", "cmake-build-release",
    "node_modules", ".git", "third_party", "vendor", "_deps",
}

# Include fragments are scanned too: a generated binding table pasted into a
# .inl is exactly where function-pointer and designated-initialiser wiring
# tends to live, which is the wiring this extractor exists to find.
_CPP_EXTS: tuple[str, ...] = (
    ".cc", ".cpp", ".cxx", ".c++", ".hh", ".hpp", ".hxx", ".h",
    *sorted(INCLUDE_FRAGMENT_EXTENSIONS),
)

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

# Statement / block delimiters. No component of _FUNC_DEF_RE can match
# across a ``;``, ``{`` or ``}``: the prefix class, the ``[^;{}]*`` arg
# span, the whitespace runs and the trailing-return class all exclude
# them. The single exception is the ``noexcept\(([^)]*)\)`` argument
# span, handled by the fallback below.
_DELIM_RE = re.compile(r"[;{}]")

# A _FUNC_DEF_RE match can only cross a delimiter through noexcept
# arguments that themselves contain ``;``/``{``/``}``. Files containing
# that (pathological, never seen in real code) take the full-text scan.
_NOEXCEPT_HOLE_RE = re.compile(r"noexcept\s*\([^)]*[;{}]")


def _iter_func_defs(text: str):
    """Yield ``_FUNC_DEF_RE`` matches via delimiter-windowed search.

    Equivalent to ``_FUNC_DEF_RE.finditer(text)`` but immune to the
    pattern's catastrophic backtracking on long runs of prefix-class
    characters (large initializer lists, expression-template headers),
    which cost tens of seconds per file at WinUI-monorepo scale.

    Every match ends at a ``{`` and, delimiter-crossing being impossible
    (see ``_DELIM_RE``), lies entirely between the previous delimiter
    and that brace. Searching the original pattern window-by-window with
    ``pattern.search(text, pos, endpos)`` preserves ``^``/anchor
    semantics against the full string, so each windowed hit is a
    verbatim full-text match, at most one per window (one ``{``).
    Windows without a ``(`` cannot satisfy the mandatory arg span and
    are skipped outright.
    """
    if _NOEXCEPT_HOLE_RE.search(text):
        yield from _FUNC_DEF_RE.finditer(text)
        return
    pos = 0
    for dm in _DELIM_RE.finditer(text):
        d = dm.start()
        if text[d] == "{" and text.find("(", pos, d) != -1:
            m = _FUNC_DEF_RE.search(text, pos, d + 1)
            if m is not None:
                yield m
        pos = d + 1


# Kinds that name a function by ordinary lookup, so a definition in the
# referencing file wins. dlsym / GetProcAddress name an exported symbol of a
# loaded library and Qt slots a member of the receiver's class: not these.
_NAME_LOOKUP_KINDS = frozenset({"fn_ptr", "desig_init"})

_HEADER_EXTS = frozenset({".h", ".hh", ".hpp", ".hxx", *INCLUDE_FRAGMENT_EXTENSIONS})
_STATIC_RE = re.compile(r"\s*(?:inline\s+|constexpr\s+)*static\b")


def _internal_linkage(definition: str, rel: str) -> bool:
    """A ``static`` definition in a source file (not a header or include
    fragment) no other file can reach: a free function with internal linkage,
    or a static member of a class declared in that source file.

    Ceiling: anonymous-namespace functions also have internal linkage and
    still read as reachable; spotting them needs the enclosing scope.
    """
    return Path(rel).suffix.lower() not in _HEADER_EXTS and bool(_STATIC_RE.match(definition))


def _scan_source(text: str, rel: str) -> list:
    """What one file contributes, as ``[defs, calls, dlopens]``.

    *defs* is ``[name, external]`` per definition in order, external when
    another file could name it. *calls* is ``[kind, name]`` per runtime
    reference in the order the join visits them, a repeat dropped: the join
    remembers what it emitted per target and kind, so a repeat adds nothing.
    *dlopens* is each loaded library in order.
    """
    defs = [
        [m.group(1), not _internal_linkage(m.group(0), rel)] for m in _iter_func_defs(text)
    ]
    calls: list[list[str]] = []
    seen: set[tuple[str, str]] = set()

    def _call(kind: str, name: str) -> None:
        if (kind, name) not in seen:
            seen.add((kind, name))
            calls.append([kind, name])

    for m in _FN_PTR_ASSIGN_RE.finditer(text):
        _call("fn_ptr", m.group(2))
    for m in _DESIG_INIT_RE.finditer(text):
        _call("desig_init", m.group(1))
    for m in _DLSYM_RE.finditer(text):
        _call("dlsym", m.group(1))
    for m in _GETPROCADDR_RE.finditer(text):
        _call("getprocaddress", m.group(1))
    for m in _QT_CONNECT_STR_RE.finditer(text):
        _call("qt_signal", m.group(1))
        _call("qt_slot", m.group(2))
    for m in _QT_CONNECT_PTR_RE.finditer(text):
        _call("qt_signal", m.group(1))
        _call("qt_slot", m.group(2))
    dlopens = [m.group(1) for m in _DLOPEN_RE.finditer(text)]
    return [defs, calls, dlopens]


class CppDynamicHints(DynamicHintExtractor):
    """Discover C++ function-pointer assignments, dlopen/dlsym, Qt connect."""

    name = "cpp"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        func_to_files: dict[str, list[str]] = {}
        local_to: dict[str, set[str]] = {}
        sources: list[tuple[str, list]] = []
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
                scanned = self._scan(rel, text, _scan_source)
                sources.append((rel, scanned))
                for name, external in scanned[0]:
                    local_to.setdefault(name, set()).add(rel)
                    if external:
                        func_to_files.setdefault(name, []).append(rel)

        for rel, (_defs, calls, dlopens) in sources:
            seen: set[tuple[str, str]] = set()
            for kind, name in calls:
                if kind in _NAME_LOOKUP_KINDS and rel in local_to.get(name, ()):
                    # The file defines the name itself: ordinary lookup binds
                    # the reference there, never to a same-named function in
                    # another translation unit.
                    continue
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

            for library in dlopens:
                edges.append(DynamicEdge(
                    source=rel,
                    target=f"external:dlopen:{library}",
                    edge_type="dynamic_imports",
                    hint_source=f"{self.name}:dlopen",
                ))

        return edges
