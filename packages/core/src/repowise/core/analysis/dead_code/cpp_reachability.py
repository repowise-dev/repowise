"""Directory-granular reachability for C and C++ source.

The generic ``in_degree == 0`` view misses four real C/C++ shapes the
file-level import graph cannot see:

- **Header-as-API**. A public header (``include/leveldb/cache.h``) is a
  pure declaration file. Without a ``compile_commands.json``, the
  consumers of ``leveldb::Cache`` reach the declaration only through
  type-use, ``extends``, ``implements``, or ``reads`` edges on its
  declared symbols — never through a file-level ``imports`` edge. A
  header whose symbols are referenced anywhere is live.
- **``int main`` carriers.** Every ``apps/io_tester/io_tester.cc``,
  ``demos/foo_demo.cc``, ``benchmarks/db_bench.cc``, and
  ``LLVMFuzzerTestOneInput``-bearing fuzz harness defines its own
  binary entry. They are the entry point and have no importer by
  design.
- **Sibling-rescued directories.** Internal headers in the same
  directory as their sibling implementation files share an inclusion
  relationship the resolver may miss when ``compile_commands.json`` is
  absent. If the directory has any live source file, internal headers
  alongside it are part of the same compilation unit.
- **Conditional-compile alternatives.** CMake ``option(WITH_OPENSSL)``
  selects one of ``crypto_openssl.cc`` / ``crypto_gnutls.cc`` at
  configure time. Whichever was *not* picked by the local configure
  still ships as live source. Detected heuristically as same-directory
  siblings whose filenames share a stem prefix (``env_posix.cc`` /
  ``env_windows.cc``, ``crypto_openssl.cc`` / ``crypto_gnutls.cc``).

The helper derives directories from the graph itself; the
:class:`CppWorkspaceIndex` populated during ingestion is not threaded
into the analyzer. Workspace-discovered conditional sources surface
through the warmup as ``is_never_flag`` / ``is_entry_point`` graph
attributes instead.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

# File extensions this module rescues. The C/C++ tree-sitter grammar
# tag covers all of these; the resolver shares them too.
_CPP_HEADER_EXTS: tuple[str, ...] = (
    ".h", ".hpp", ".hxx", ".hh", ".h++", ".inc",
)
_CPP_SOURCE_EXTS: tuple[str, ...] = (
    ".c", ".cc", ".cpp", ".cxx", ".c++", ".C",
)
_CPP_ALL_EXTS: tuple[str, ...] = _CPP_HEADER_EXTS + _CPP_SOURCE_EXTS


# Function names that mark a translation unit as a binary entry point.
# A file defining one of these is the program / DLL / fuzzer entry — no
# importer will ever exist by design.
_CPP_ENTRY_FUNCTION_NAMES: frozenset[str] = frozenset({
    "main",
    "WinMain",
    "wWinMain",
    "wmain",
    "LLVMFuzzerTestOneInput",
    "LLVMFuzzerInitialize",
    "DllMain",
})


# Edge types that count as "this symbol is used by something". A header
# whose declared symbols carry any of these inbound edges is live, even
# without a file-level ``imports`` edge from the consumer.
_SYMBOL_USE_EDGE_TYPES: frozenset[str] = frozenset({
    "calls",
    "method_implements",
    "reads",
    "extends",
    "implements",
    "type_use",
})


def is_cpp_path(path: str) -> bool:
    """Return True if *path* has a C or C++ source/header extension."""
    return path.endswith(_CPP_ALL_EXTS)


def _is_header(path: str) -> bool:
    return path.endswith(_CPP_HEADER_EXTS)


def _dir(node: str) -> str:
    """Repo-relative POSIX directory of a C/C++ file ("" = repo root)."""
    parent = PurePosixPath(node).parent.as_posix()
    return "" if parent == "." else parent


def build_cpp_package_files(graph: Any) -> dict[str, list[str]]:
    """Group every C/C++ file node by directory.

    Directory is the closest thing to a "compilation neighborhood" the
    graph can derive without the workspace index — siblings in the same
    directory typically share a CMake / Bazel target.
    """
    packages: dict[str, list[str]] = {}
    for node in graph.nodes():
        s = str(node)
        if is_cpp_path(s):
            packages.setdefault(_dir(s), []).append(s)
    return packages


def _file_defines_entry_function(graph: Any, file_node: str) -> bool:
    """True if *file_node* defines an ``int main`` / ``WinMain`` / fuzzer entry."""
    for succ in graph.successors(file_node):
        succ_data = graph.nodes.get(succ, {})
        if succ_data.get("node_type") != "symbol":
            continue
        edge = graph.get_edge_data(file_node, succ, {})
        if edge.get("edge_type") != "defines":
            continue
        if (
            succ_data.get("kind") in ("function", "method")
            and succ_data.get("name") in _CPP_ENTRY_FUNCTION_NAMES
        ):
            return True
    return False


def _any_defined_symbol_is_used(graph: Any, file_node: str) -> bool:
    """True iff any symbol defined in *file_node* carries an inbound use edge.

    Drives the header-reached-by-symbol-reference rescue: a header that
    no TU ``#include``d directly, but whose declared struct / class
    appears as a parameter, field, return, or base type elsewhere in the
    codebase, is live.
    """
    for succ in graph.successors(file_node):
        succ_data = graph.nodes.get(succ, {})
        if succ_data.get("node_type") != "symbol":
            continue
        edge = graph.get_edge_data(file_node, succ, {})
        if edge.get("edge_type") != "defines":
            continue
        for pred in graph.predecessors(succ):
            etype = graph[pred][succ].get("edge_type")
            if etype in _SYMBOL_USE_EDGE_TYPES:
                return True
    return False


def _stem_prefix(path: str) -> str | None:
    """First ``_``-separated chunk of a filename's stem.

    ``env_posix.cc`` → ``env``; ``crypto_openssl.cc`` → ``crypto``;
    ``standalone.cc`` → None (no underscore — not a conditional alt).
    """
    name = PurePosixPath(path).name
    dot = name.rfind(".")
    stem = name[:dot] if dot > 0 else name
    underscore = stem.find("_")
    if underscore <= 0:
        return None
    return stem[:underscore]


def is_cpp_file_reachable(
    node: str,
    graph: Any,
    package_files: dict[str, list[str]],
) -> bool:
    """Return True if a C/C++ source file is reachable beyond ``in_degree``.

    Called from the analyzer only for ``.c``/``.cc``/``.cpp``/``.cxx``/
    ``.h``/``.hpp``/``.hxx`` nodes that have already survived the
    generic skips (``is_entry_point``, ``is_test``, ``is_never_flag``,
    fixture / barrel filters). Rules below short-circuit.
    """
    if graph.in_degree(node) > 0:
        return True

    # ``int main`` / ``WinMain`` / fuzzer entry — the file IS the binary
    # entry point.
    if _file_defines_entry_function(graph, node):
        return True

    is_header = _is_header(node)

    # Header-reached-by-symbol-reference rescue. Public-API headers
    # whose declared types are referenced anywhere (param / field /
    # return / extends / implements / type alias RHS) are live even
    # without a direct ``#include`` chain.
    if is_header and _any_defined_symbol_is_used(graph, node):
        return True

    siblings = package_files.get(_dir(node), ())
    stem_prefix = _stem_prefix(node)
    has_sibling_with_importer = False
    has_sibling_entry = False
    has_conditional_pair = False
    for sibling in siblings:
        if sibling == node:
            continue
        sib_data = graph.nodes.get(sibling, {})
        sib_has_importer = graph.in_degree(sibling) > 0
        if sib_data.get("is_entry_point", False):
            has_sibling_entry = True
            break
        if _file_defines_entry_function(graph, sibling):
            has_sibling_entry = True
            break
        if sib_has_importer:
            has_sibling_with_importer = True
            # Conditional-alt pairing: same directory + shared stem
            # prefix (``env_posix.cc`` ↔ ``env_windows.cc``,
            # ``crypto_openssl.cc`` ↔ ``crypto_gnutls.cc``).
            sib_prefix = _stem_prefix(sibling)
            if stem_prefix and sib_prefix and sib_prefix == stem_prefix:
                has_conditional_pair = True
                break

    if has_sibling_entry:
        # Any sibling is an entry point — we are part of an apps / demo
        # / cmd directory. Treat as live regardless of header/source
        # status; helper files alongside ``main.cc`` are not dead.
        return True

    if is_header and has_sibling_with_importer:
        # Internal header sitting next to its implementation .cc whose
        # importer chain we observed — the header is implicitly part
        # of the same TU.
        return True

    if has_conditional_pair:
        return True

    return False
