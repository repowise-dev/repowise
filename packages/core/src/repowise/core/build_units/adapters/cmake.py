"""CMake targets as build units, read from the C/C++ workspace index.

One unit per ``add_library`` / ``add_executable`` target. Its listed files
are its sources, the headers it names, and every header under its PUBLIC or
INTERFACE ``target_include_directories``, wherever that directory sits. A
directory several targets declare gives its headers several owners: the
build gives them no single one. A file no target lists falls to the deepest
unit root above it.
``target_link_libraries`` names that match another target become deps; the
rest (``Threads::Threads``, system libraries) are third-party and dropped.

A ``target_sources`` call in another file than the one declaring the target
reaches the index as a stub (kind ``unknown``) rooted at the calling file.
Its files and deps are merged into the declared target of that name, as
CMake does; a stub no file declares yields no unit.

The index merges CMake and Bazel targets; only the CMake ones are read here.

Ceilings. The index reads ``CMakeLists.txt`` with a regex tokenizer, not a
CMake evaluator (see ``ingestion/external_systems/cmake.py``):

- **Generator expressions are skipped.** A dep or source written as
  ``$<$<CONFIG:Debug>:core>`` is not seen, so its edge is missing.
- **Directory variables resolve wrongly below the repo root.** In a
  ``CMakeLists.txt`` that is not at the root, a source or include directory
  written as ``${CMAKE_CURRENT_SOURCE_DIR}/x`` (or ``CMAKE_CURRENT_LIST_DIR``)
  resolves to ``<dir>/<dir>/x``, and on Windows ``${PROJECT_SOURCE_DIR}/x``
  is read as relative. Such files are missed; paths written relative to the
  ``CMakeLists.txt`` resolve correctly.
- **Only literal variables expand.** Targets, sources and deps built in a
  ``foreach`` loop, a function or macro, or an ``include()``-d helper module
  are missed or kept unexpanded; unexpanded deps are dropped.
- **``ALIAS`` and ``IMPORTED`` libraries become units.** The parser reads
  ``add_library(proj::core ALIAS core)`` as a library of its own, so a dep on
  ``proj::core`` points at that empty unit instead of ``core``, and an
  ``IMPORTED`` third-party target gets a unit it should not have.
- **Every ``if()`` branch counts.** Conditionally compiled sources and deps
  are all kept, whichever options a real configure would pick.
- **Target names must be unique.** CMake requires that within one project.
  When two separate projects in one repo declare the same name, both claim
  one id and ``collect_build_units`` keeps the claim whose root sorts first
  as a string, not the shallower one, and warns about the duplicate. Stubs
  of that name merge into both claims.
- **Calls in another file that add no sources are lost.** The index drops a
  stub without sources, so a ``target_link_libraries`` or
  ``target_include_directories`` call outside the declaring file adds no
  dep or include directory.
- **Headers come from names and public include directories.** Private
  include directories are not carried by the index, so headers only they
  reach fall to the deepest unit root. A ``$<BUILD_INTERFACE:...>`` include
  directory is not read at all. Libraries that all declare one shared
  ``include/`` and keep their headers in ``include/<name>/`` each own every
  header there.
- **No manifest fallback.** ``CMakeLists.txt`` is build config, not a package
  manifest, so a file the reader gets nothing from leaves its sources without
  an owner unless another unit's root contains them.
- **Bazel ``cc_*`` targets are not read.** A Bazel adapter would read the same
  index's ``bazel://`` targets.

Upgrade path: when a configured build tree holds a CMake File API reply
(``build/.cmake/api/v1/reply/``), the index reads that instead, which
resolves variables, generator expressions and aliases as CMake itself does.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.resolvers import ResolverContext

# The include resolver's header extensions, so the two cannot disagree on
# what a header is.
from repowise.core.ingestion.resolvers.cpp import _HEADER_EXTS
from repowise.core.ingestion.resolvers.cpp_workspace import CppTarget, get_or_build_cpp_index

_MANIFEST = "CMakeLists.txt"
# ``cmake:<name>@<declaring CMakeLists.txt>``, set by ``_cmake_target_to_cpp``.
_CMAKE_ID_PREFIX = "cmake:"
# The kind the parser gives a target a file names without declaring it.
_STUB_KIND = "unknown"


def _has_cmake(paths: set[str]) -> bool:
    return any(p == _MANIFEST or p.endswith(f"/{_MANIFEST}") for p in paths)


def _headers_under(dirs: Iterable[str], headers: list[str]) -> list[str]:
    """The paths in sorted *headers* below any of *dirs* (repo-relative directories)."""
    out: list[str] = []
    for d in dirs:
        # Unexpanded ``${...}``, absolute paths and the repo root itself name
        # no directory of the repo to read.
        if not d or "${" in d or d.startswith("/") or ":" in d:
            continue
        prefix = d.rstrip("/") + "/"
        i = bisect_left(headers, prefix)
        while i < len(headers) and headers[i].startswith(prefix):
            out.append(headers[i])
            i += 1
    return out


def _files(target: CppTarget, headers: list[str]) -> list[str]:
    return [
        *target.sources,
        *target.public_headers,
        *target.private_headers,
        *target.conditional_sources,
        *_headers_under(target.include_dirs, headers),
    ]


def units(ctx: ResolverContext) -> Found:
    if not _has_cmake(ctx.path_set):
        return Found([])

    headers = sorted(p for p in ctx.path_set if p.lower().endswith(_HEADER_EXTS))
    declared: list[tuple[str, CppTarget]] = []
    stubs: dict[str, list[CppTarget]] = defaultdict(list)
    for target_id, target in get_or_build_cpp_index(ctx).targets.items():
        if not target_id.startswith(_CMAKE_ID_PREFIX):
            continue  # a Bazel target
        if target.kind == _STUB_KIND:
            stubs[target.name].append(target)
        else:
            declared.append((target_id.partition("@")[2], target))

    claims: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for declared_in, target in declared:
        parts = [target, *stubs.get(target.name, ())]
        files = [f for part in parts for f in _files(part, headers)]
        deps = {f"cmake:{dep}" for part in parts for dep in part.link_deps}
        unit = BuildUnit(
            id=f"cmake:{target.name}",
            ecosystem="cmake",
            name=target.name,
            root=target.root_dir,
            manifest=declared_in,
            source="cmake",
            deps=tuple(sorted(deps)),
        )
        claims.append((unit, tuple(dict.fromkeys(files))))
    return Found(claims)
