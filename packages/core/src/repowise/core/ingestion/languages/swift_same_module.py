"""Swift intra-module type-reference resolution.

Background
----------
Swift has no intra-module imports *by design*: every file in a target
sees every other file's top-level declarations. Without help, an SPM
target is an edge desert — files connect only through the rare
cross-module ``import``, so exactly the most cohesive unit of a Swift
codebase reads as disconnected files.

This is the Swift binding of the shared implicit-scope scan
(:mod:`.scope_scan`): this module supplies a per-target map of declared
top-level types (class/struct/enum/protocol/actor) and the stdlib skip list,
and that module runs the identifier scan and emits the edges. An edge A → B
is emitted only when ALL of:

- the identifier names a top-level type declared in exactly **one**
  file B of A's own SPM target (ambiguous names link to no one);
- B is not A itself, and the type is not also declared in A;
- the identifier is not a ubiquitous Swift stdlib/Foundation name
  (the declared-in-target requirement already filters the framework
  universe precisely — the skip list only guards the rare case of a
  target declaring its own ``Data`` or ``View``).

Files not under any SPM target (Xcode projects carry no
``Package.swift``) fall into a single implicit module — Xcode app
targets are one module, and references still have to match a unique
locally-declared type.

``typealias`` declarations and extensions are deliberately out of
scope: an extension declares nothing new, and aliases are far more
often file-local plumbing than the referenced surface.

Emitted edges are ``imports`` edges carrying
``hint_source="same_module"`` so density metrics can separate them and
false positives stay diagnosable.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .scope_scan import FileScope, ScopeTier, collect_source_texts, emit_scope_edges

if TYPE_CHECKING:
    import networkx as nx

# Top-level (and nested — indentation is not anchored, matching the JVM
# scan's pragmatics) Swift type declarations.
_SWIFT_TYPE_DECL_RE = re.compile(
    r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*"
    r"(?:(?:public|open|internal|final|private|fileprivate|indirect|dynamic)\s+)*"
    r"(?:class|struct|enum|protocol|actor)\s+([A-Z]\w*)",
    re.MULTILINE,
)

# Ubiquitous Swift stdlib / Foundation names — references are
# overwhelmingly to the framework type even when a target shadows one.
_SWIFT_COMMON_TYPES = frozenset({
    "String", "Int", "Int8", "Int16", "Int32", "Int64", "UInt", "UInt8",
    "UInt16", "UInt32", "UInt64", "Double", "Float", "Bool", "Character",
    "Array", "Dictionary", "Set", "Optional", "Result", "Error", "Never",
    "Any", "AnyObject", "AnyClass", "Self", "Void", "Sequence", "Collection",
    "Iterator", "IteratorProtocol", "Equatable", "Hashable", "Comparable",
    "Codable", "Encodable", "Decodable", "Identifiable", "CustomStringConvertible",
    "CaseIterable", "RawRepresentable", "ExpressibleByStringLiteral",
    "Data", "Date", "URL", "URLRequest", "URLResponse", "URLSession",
    "UUID", "Notification", "NotificationCenter", "IndexPath", "IndexSet",
    "TimeInterval", "Calendar", "Locale", "TimeZone", "Bundle", "FileManager",
    "JSONDecoder", "JSONEncoder", "JSONSerialization", "NumberFormatter",
    "DateFormatter", "RunLoop", "Thread", "OperationQueue", "DispatchQueue",
    "Task", "Actor", "MainActor", "Sendable", "AsyncSequence", "AsyncStream",
    "Published", "ObservableObject", "State", "Binding", "Environment",
    "View", "Text", "Image", "Color", "Font",
})

_SAME_MODULE_HINT = "same_module"


def _module_for_file(path: str, target_dirs: list[tuple[str, str]]) -> str:
    """Return the SPM target name owning *path* (longest prefix wins), or ""."""
    best = ""
    best_len = -1
    for name, prefix in target_dirs:
        if path.startswith(prefix) and len(prefix) > best_len:
            best, best_len = name, len(prefix)
    return best


def resolve_swift_same_module_refs(
    graph: nx.DiGraph,
    swift_targets: dict[str, str],
    texts: dict[str, str],
) -> int:
    """Emit same-module ``imports`` edges for Swift files.

    *swift_targets* maps SPM target name → repo-relative source dir
    (from ``Package.swift``); empty when the repo has no SPM manifest,
    in which case all Swift files form one implicit module.

    Returns the number of edges added.
    """
    target_dirs = [
        (name, d.rstrip("/") + "/") for name, d in sorted(swift_targets.items())
    ]

    # Group files by module and collect each module's declared types.
    files_by_module: dict[str, list[str]] = {}
    declared: dict[str, dict[str, list[str]]] = {}  # module → type → [files]
    for path in sorted(texts):
        module = _module_for_file(path, target_dirs)
        files_by_module.setdefault(module, []).append(path)
        type_map = declared.setdefault(module, {})
        for m in _SWIFT_TYPE_DECL_RE.finditer(texts[path]):
            type_map.setdefault(m.group(1), []).append(path)

    scannable = {
        path: module
        for module, files in files_by_module.items()
        if len(files) >= 2
        for path in files
    }

    def plan(path: str, _text: str) -> FileScope | None:
        module = scannable.get(path)
        if module is None:
            return None
        type_map = declared[module]
        return FileScope(
            tiers=(
                ScopeTier(
                    hint=_SAME_MODULE_HINT,
                    lookup=lambda ident: set(type_map.get(ident, ())),
                ),
            )
        )

    return emit_scope_edges(
        graph,
        ((path, texts[path]) for path in sorted(texts)),
        plan,
        skip_names=_SWIFT_COMMON_TYPES,
    )


def collect_swift_source_texts(parsed_files: dict[str, Any]) -> dict[str, str]:
    """Read each parsed Swift file's source from disk, keyed by repo path."""
    return collect_source_texts(parsed_files, ("swift",))
