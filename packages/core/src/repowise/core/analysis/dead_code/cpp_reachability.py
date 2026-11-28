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
through the warmup as ``is_never_flag`` / ``is_reachability_root`` graph
attributes instead.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ...entry_candidacy import is_reachability_root
from ...ingestion.languages.specs.cpp import INCLUDE_FRAGMENT_EXTENSIONS
from ...ingestion.models import REACHABILITY_USE_EDGE_TYPES

# File extensions this module rescues. The C/C++ tree-sitter grammar
# tag covers all of these; the resolver shares them too. Include fragments
# count as headers here: like a header, a fragment has no importer of its
# own beyond the translation unit that pastes it in.
_CPP_HEADER_EXTS: tuple[str, ...] = (
    ".h", ".hpp", ".hxx", ".hh", ".h++", ".inc",
    *sorted(INCLUDE_FRAGMENT_EXTENSIONS),
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
_SYMBOL_USE_EDGE_TYPES: frozenset[str] = REACHABILITY_USE_EDGE_TYPES


def is_cpp_path(path: str) -> bool:
    """Return True if *path* has a C or C++ source/header extension."""
    return path.endswith(_CPP_ALL_EXTS)


def _is_header(path: str) -> bool:
    return path.endswith(_CPP_HEADER_EXTS)


def _dir(node: str) -> str:
    """Repo-relative POSIX directory of a C/C++ file ("" = repo root)."""
    parent = PurePosixPath(node).parent.as_posix()
    return "" if parent == "." else parent


@dataclass(frozen=True)
class _DirFacts:
    """What the sibling rescues read about one directory's C/C++ files."""

    entries: frozenset[str]
    importers: frozenset[str]
    importers_by_prefix: dict[str, frozenset[str]]


class CppPackageFiles(dict[str, list[str]]):
    """Directory -> C/C++ files, plus each directory's sibling facts, read once.

    Every importer-less file in a directory asks the same questions about its
    siblings (an entry point among them? one something depends on?), so
    answering them per file was quadratic in the directory's size: a
    1,000-file directory cost a million in-edge scans.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._facts: dict[str, _DirFacts] = {}

    def dir_facts(self, graph: Any, directory: str) -> _DirFacts:
        facts = self._facts.get(directory)
        if facts is None:
            facts = self._facts[directory] = _read_dir_facts(graph, self.get(directory, ()))
        return facts


def _read_dir_facts(graph: Any, members: Iterable[str]) -> _DirFacts:
    from .file_reachability import has_dependency_importer

    entries: set[str] = set()
    importers: set[str] = set()
    by_prefix: dict[str, set[str]] = {}
    for member in members:
        if is_reachability_root(graph.nodes.get(member, {})) or _file_defines_entry_function(
            graph, member
        ):
            entries.add(member)
        elif has_dependency_importer(graph, member):
            importers.add(member)
            prefix = _stem_prefix(member)
            if prefix:
                by_prefix.setdefault(prefix, set()).add(member)
    return _DirFacts(
        entries=frozenset(entries),
        importers=frozenset(importers),
        importers_by_prefix={k: frozenset(v) for k, v in by_prefix.items()},
    )


def _has_other(members: frozenset[str], node: str) -> bool:
    return len(members) > (node in members)


def build_cpp_package_files(graph: Any) -> CppPackageFiles:
    """Group every C/C++ file node by directory.

    Directory is the closest thing to a "compilation neighborhood" the
    graph can derive without the workspace index — siblings in the same
    directory typically share a CMake / Bazel target.
    """
    packages = CppPackageFiles()
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
    # Called, not imported at module level: file_reachability imports this module.
    from .file_reachability import has_dependency_importer

    if has_dependency_importer(graph, node):
        return True

    # Build-listed rescue: the file is compiled by a CMake target, MSBuild
    # .vcxproj, or included by an .rc resource script.
    if graph.nodes.get(node, {}).get("build_listed"):
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

    if isinstance(package_files, CppPackageFiles):
        facts = package_files.dir_facts(graph, _dir(node))
    else:
        facts = _read_dir_facts(graph, package_files.get(_dir(node), ()))

    if _has_other(facts.entries, node):
        # Any sibling is an entry point — we are part of an apps / demo
        # / cmd directory. Treat as live regardless of header/source
        # status; helper files alongside ``main.cc`` are not dead.
        return True

    if is_header and _has_other(facts.importers, node):
        # Internal header sitting next to its implementation .cc whose
        # importer chain we observed — the header is implicitly part
        # of the same TU.
        return True

    # Conditional-alt pairing: a sibling something depends on shares this
    # file's stem prefix (``env_posix.cc`` ↔ ``env_windows.cc``,
    # ``crypto_openssl.cc`` ↔ ``crypto_gnutls.cc``).
    stem_prefix = _stem_prefix(node)
    pair = facts.importers_by_prefix.get(stem_prefix, frozenset()) if stem_prefix else frozenset()
    return _has_other(pair, node)
