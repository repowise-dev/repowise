"""CMake targets as build units, read from the C/C++ workspace index.

One unit per ``add_library`` / ``add_executable`` target, with the target's
sources and declared headers listed as its files. A file no target lists
(an undeclared header, say) falls to the deepest unit root above it.
``target_link_libraries`` names that match another target become deps; the
rest (``Threads::Threads``, system libraries) are third-party and dropped.

The index merges CMake and Bazel targets; only the CMake ones are read here.

Ceilings. The index reads ``CMakeLists.txt`` with a regex tokenizer, not a
CMake evaluator (see ``ingestion/external_systems/cmake.py``):

- **Generator expressions are skipped.** A dep or source written as
  ``$<$<CONFIG:Debug>:core>`` is not seen, so its edge is missing.
- **Only literal variables expand.** Targets, sources and deps built in a
  ``foreach`` loop, a function or macro, or an ``include()``-d helper module
  are missed or kept unexpanded; unexpanded deps are dropped.
- **``ALIAS`` and ``IMPORTED`` libraries become units.** The parser reads
  ``add_library(proj::core ALIAS core)`` as a library of its own, so a dep on
  ``proj::core`` points at that empty unit instead of ``core``, and an
  ``IMPORTED`` third-party target gets a unit it should not have.
- **Every ``if()`` branch counts.** Conditionally compiled sources and deps
  are all kept, whichever options a real configure would pick.
- **Target names must be unique.** CMake requires that within one project;
  two separate projects in one repo declaring the same name keep only the
  shallower one (``collect_build_units`` warns about the duplicate).
- **Only declared headers are listed.** A header no target names falls to
  the deepest unit root above it, which a shared top-level ``include/`` does
  not match.
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

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.resolvers import ResolverContext
from repowise.core.ingestion.resolvers.cpp_workspace import get_or_build_cpp_index

_MANIFEST = "CMakeLists.txt"
# ``cmake:<name>@<declaring CMakeLists.txt>``, set by ``_cmake_target_to_cpp``.
_CMAKE_ID_PREFIX = "cmake:"


def _has_cmake(paths: set[str]) -> bool:
    return any(p == _MANIFEST or p.endswith(f"/{_MANIFEST}") for p in paths)


def units(ctx: ResolverContext) -> Found:
    if not _has_cmake(ctx.path_set):
        return Found([])

    claims: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for target_id, target in get_or_build_cpp_index(ctx).targets.items():
        if not target_id.startswith(_CMAKE_ID_PREFIX):
            continue  # a Bazel target
        _, _, declared_in = target_id.partition("@")
        files = (
            *target.sources,
            *target.public_headers,
            *target.private_headers,
            *target.conditional_sources,
        )
        unit = BuildUnit(
            id=f"cmake:{target.name}",
            ecosystem="cmake",
            name=target.name,
            root=target.root_dir,
            manifest=declared_in,
            source="cmake",
            deps=tuple(sorted({f"cmake:{dep}" for dep in target.link_deps})),
        )
        claims.append((unit, tuple(dict.fromkeys(files))))
    return Found(claims)
