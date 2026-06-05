"""C/C++ workspace index — unified build-graph view across CMake + Bazel.

Built once per resolver run via :func:`get_or_build_cpp_index` and cached
on the :class:`ResolverContext`. Mirrors the role that
:class:`JvmWorkspaceIndex`, :class:`GoPackageIndex`, and
:class:`DotNetProjectIndex` play for their languages.

What the index gives downstream consumers:

* ``targets`` — every CMake / Bazel ``cc_*`` target with its source list,
  public/private header list, include dirs, link deps, and a flag for
  conditionally-compiled sources (CMake ``if(option)`` blocks).
* ``file_to_targets`` — which targets own a given source/header file
  (drives sibling fan-out in :func:`resolve_cpp_import`).
* ``public_header_includes`` — every ``#include "x/y.h"`` shape supported
  by the workspace's public-header layout. Without this, ``leveldb``-shape
  repos (where every ``include/leveldb/*.h`` is the library's public API
  surface) read the entire public API as ``unreachable_file``.
* ``project_export_macros`` — the project's own visibility macros
  (``LEVELDB_EXPORT``, ``SEASTAR_API``, …) discovered by scanning header
  files for ``#define X __declspec(dllexport)`` /
  ``__attribute__((visibility("default")))`` patterns and from CMake
  ``target_compile_definitions`` ending in ``_EXPORT`` / ``_API``.

The index is **best-effort**. CMake parsing is regex-based and may miss
sources hidden behind generator expressions, ``foreach`` loops, or
``include()`` of helper modules. Callers must always combine the
workspace data with the lazy stem-fallback path so unparsable repos
degrade gracefully.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import structlog

from ..external_systems.bazel import (
    BazelTarget,
    discover_bazel_packages,
    is_bazel_repo,
)
from ..external_systems.cmake import (
    CMakeTarget,
    discover_cmake_reactor,
    parse_cmake_file_api_reply,
)

if TYPE_CHECKING:
    from .context import ResolverContext

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Stdlib filter — system includes we should never even emit as external nodes
# ---------------------------------------------------------------------------


_CPP_STDLIB_HEADERS: frozenset[str] = frozenset({
    # C++ standard library — bare names without extension
    "algorithm", "any", "array", "atomic", "barrier", "bit", "bitset",
    "cassert", "ccomplex", "cctype", "cerrno", "cfenv", "cfloat",
    "charconv", "chrono", "cinttypes", "ciso646", "climits", "clocale",
    "cmath", "codecvt", "compare", "complex", "concepts", "condition_variable",
    "coroutine", "csetjmp", "csignal", "cstdalign", "cstdarg", "cstdbool",
    "cstddef", "cstdint", "cstdio", "cstdlib", "cstring", "ctgmath",
    "ctime", "cuchar", "cwchar", "cwctype",
    "deque", "exception", "execution", "expected", "filesystem",
    "format", "forward_list", "fstream", "functional", "future",
    "initializer_list", "iomanip", "ios", "iosfwd", "iostream",
    "istream", "iterator", "latch", "limits", "list", "locale",
    "map", "memory", "memory_resource", "mutex", "new", "numbers",
    "numeric", "optional", "ostream", "queue", "random", "ranges",
    "ratio", "regex", "scoped_allocator", "semaphore", "set", "shared_mutex",
    "source_location", "span", "sstream", "stack", "stacktrace",
    "stdexcept", "stop_token", "streambuf", "string", "string_view",
    "syncstream", "system_error", "thread", "tuple", "typeindex",
    "typeinfo", "type_traits", "unordered_map", "unordered_set",
    "utility", "valarray", "variant", "vector", "version", "print",
    # std experimental
    "experimental/optional", "experimental/string_view",
})


_C_STDLIB_HEADERS: frozenset[str] = frozenset({
    "assert.h", "complex.h", "ctype.h", "errno.h", "fenv.h", "float.h",
    "inttypes.h", "iso646.h", "limits.h", "locale.h", "math.h", "setjmp.h",
    "signal.h", "stdalign.h", "stdarg.h", "stdatomic.h", "stdbool.h",
    "stddef.h", "stdint.h", "stdio.h", "stdlib.h", "stdnoreturn.h",
    "string.h", "tgmath.h", "threads.h", "time.h", "uchar.h", "wchar.h",
    "wctype.h",
    # POSIX / commonly-bundled
    "unistd.h", "fcntl.h", "sys/types.h", "sys/stat.h", "sys/mman.h",
    "sys/socket.h", "sys/wait.h", "sys/ioctl.h", "sys/select.h", "sys/time.h",
    "sys/uio.h", "sys/un.h", "sys/file.h", "sys/syscall.h", "sys/resource.h",
    "sys/utsname.h", "sys/epoll.h", "sys/eventfd.h", "sys/timerfd.h",
    "sys/inotify.h", "sys/sysinfo.h", "sys/random.h", "sys/prctl.h",
    "netdb.h", "netinet/in.h", "netinet/tcp.h", "netinet/udp.h",
    "arpa/inet.h", "poll.h", "pthread.h", "sched.h", "semaphore.h",
    "syslog.h", "termios.h", "dirent.h", "dlfcn.h", "ftw.h", "glob.h",
    "grp.h", "pwd.h", "regex.h", "spawn.h", "strings.h", "tar.h",
    "ulimit.h", "utime.h", "wordexp.h", "libgen.h",
    # Windows SDK common
    "windows.h", "winsock2.h", "ws2tcpip.h", "io.h", "process.h", "direct.h",
    "intrin.h", "mmintrin.h", "xmmintrin.h", "emmintrin.h", "immintrin.h",
})


def is_stdlib_include(raw_include: str) -> bool:
    """Return True if *raw_include* names a C/C++ standard-library header.

    *raw_include* is the path inside ``<>`` (e.g., ``"vector"`` or
    ``"stdio.h"``).
    """
    if raw_include in _C_STDLIB_HEADERS or raw_include in _CPP_STDLIB_HEADERS:
        return True
    # Headers like ``sys/types.h`` — already covered. The bare-stem ``c*``
    # form (cstdio, cstdlib, …) — also covered. Anything starting with
    # ``__`` or ``bits/`` is libstdc++/libc internals.
    return raw_include.startswith(("bits/", "ext/", "tr1/", "tr2/", "__"))


# ---------------------------------------------------------------------------
# Index types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CppTarget:
    """Unified build target (CMake or Bazel)."""

    id: str
    """Stable identifier — ``<source>:<name>`` (e.g., ``cmake:leveldb``,
    ``bazel://absl/strings:strings``)."""

    name: str
    kind: str
    root_dir: str  # repo-relative dir owning the build file
    sources: tuple[str, ...]
    public_headers: tuple[str, ...]
    private_headers: tuple[str, ...]
    include_dirs: tuple[str, ...]
    compile_defines: tuple[str, ...]
    link_deps: tuple[str, ...]
    conditional_sources: tuple[str, ...]
    is_test: bool
    is_benchmark: bool
    is_app: bool
    is_demo: bool
    is_example: bool

    @property
    def is_library(self) -> bool:
        return self.kind.startswith("library")


@dataclass
class CppWorkspaceIndex:
    """Repo-scoped view of every local C/C++ build target."""

    targets: dict[str, CppTarget] = field(default_factory=dict)

    file_to_targets: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Maps repo-relative file path → target IDs that own it as src/hdr."""

    public_header_includes: dict[str, str] = field(default_factory=dict)
    """Maps ``#include "x/y.h"`` style key → resolved repo file path.

    Keys come from ``target_include_directories(PUBLIC|INTERFACE …)`` —
    for every include-dir + every public header, the relative path from
    the include dir is registered. Also pre-seeded with the as-written
    path so ``#include "include/leveldb/cache.h"`` keeps working.
    """

    project_export_macros: frozenset[str] = field(default_factory=frozenset)
    """``LEVELDB_EXPORT``, ``SEASTAR_API``, ``ABSL_DLL`` — discovered from
    headers and target_compile_definitions."""

    target_include_search_dirs: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Per-target list of include-search-roots (repo-relative). Used as
    fallback when ``public_header_includes`` misses."""

    main_files: frozenset[str] = field(default_factory=frozenset)
    """Source files defining ``int main`` / ``WinMain`` / fuzzer entry.

    Populated lazily by Phase 3 when reachability analysis kicks in."""

    def targets_owning(self, file_path: str) -> tuple[str, ...]:
        return self.file_to_targets.get(file_path, ())

    def siblings_in_targets(self, file_path: str) -> tuple[str, ...]:
        """All source files sharing at least one target with *file_path*."""
        owners = self.file_to_targets.get(file_path, ())
        if not owners:
            return ()
        out: list[str] = []
        seen: set[str] = set()
        for tid in owners:
            t = self.targets.get(tid)
            if not t:
                continue
            for src in t.sources:
                if src == file_path or src in seen:
                    continue
                seen.add(src)
                out.append(src)
        return tuple(out)

    def is_public_header(self, file_path: str) -> bool:
        for tid in self.file_to_targets.get(file_path, ()):
            t = self.targets.get(tid)
            if t and file_path in t.public_headers:
                return True
        return False

    def is_conditional(self, file_path: str) -> bool:
        for tid in self.file_to_targets.get(file_path, ()):
            t = self.targets.get(tid)
            if t and file_path in t.conditional_sources:
                return True
        return False


# ---------------------------------------------------------------------------
# Build pipeline
# ---------------------------------------------------------------------------


_PROJECT_EXPORT_MACRO_RE = re.compile(
    r"^\s*#\s*define\s+([A-Z][A-Z0-9_]{2,})\s+(?:__declspec\(\s*dllexport|__attribute__\(\(\s*(?:visibility\(\s*\"default\"|used)|EMSCRIPTEN_KEEPALIVE)",
    re.MULTILINE,
)
_EXPORT_LIKE_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}_(?:EXPORT|API|PUBLIC|DLL|VISIBLE)$")


@lru_cache(maxsize=8192)
def _scan_header_for_export_macros(abs_path: str) -> tuple[str, ...]:
    try:
        text = Path(abs_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ()
    # Limit to the first ~8KB — export macros are nearly always near the top.
    head = text[:8192]
    return tuple({m.group(1) for m in _PROJECT_EXPORT_MACRO_RE.finditer(head)})


def _classify_dir(root_dir: str) -> dict[str, bool]:
    """Classify a target's root dir into ``apps`` / ``demos`` / etc.

    Heuristic on common path segments. Used to drive Phase 3 never-flag
    decisions without hard-coding repo-specific paths.
    """
    parts = [p.lower() for p in PurePosixPath(root_dir).parts]
    return {
        "is_app": any(p in ("apps", "app", "bin", "cmd") for p in parts),
        "is_demo": any(p in ("demos", "demo") for p in parts),
        "is_example": any(p in ("examples", "example", "samples", "sample") for p in parts),
        "is_benchmark": any(p in ("benchmarks", "benchmark", "bench") for p in parts)
            or any("perf" in p for p in parts),
        "is_test_dir": any(p in ("tests", "test", "testing") for p in parts),
    }


def _cmake_target_to_cpp(t: CMakeTarget, *, path_set: set[str]) -> CppTarget:
    classification = _classify_dir(PurePosixPath(t.cmakelists).parent.as_posix() if "/" in t.cmakelists else "")
    is_test = (t.kind == "test") or classification["is_test_dir"]
    # Filter sources / headers to those actually present in the path_set
    # so downstream lookups never resolve to phantom files.
    sources = tuple(s for s in t.sources if s in path_set)
    public_headers = tuple(s for s in t.public_headers if s in path_set)
    private_headers = tuple(s for s in t.private_headers if s in path_set)
    root_dir = PurePosixPath(t.cmakelists).parent.as_posix() if "/" in t.cmakelists else ""
    if root_dir == ".":
        root_dir = ""
    return CppTarget(
        id=f"cmake:{t.name}@{t.cmakelists}",
        name=t.name,
        kind=t.kind,
        root_dir=root_dir,
        sources=sources,
        public_headers=public_headers,
        private_headers=private_headers,
        include_dirs=tuple(dict.fromkeys(t.include_dirs)),
        compile_defines=tuple(dict.fromkeys(t.compile_defines)),
        link_deps=tuple(dict.fromkeys(t.link_deps)),
        conditional_sources=tuple(s for s in t.conditional_sources if s in path_set),
        is_test=is_test,
        is_benchmark=classification["is_benchmark"],
        is_app=classification["is_app"] and t.kind == "executable",
        is_demo=classification["is_demo"],
        is_example=classification["is_example"],
    )


def _bazel_target_to_cpp(t: BazelTarget, *, path_set: set[str]) -> CppTarget:
    classification = _classify_dir(t.package)
    sources = tuple(s for s in t.srcs if s in path_set)
    public_headers = tuple(s for s in t.hdrs if s in path_set)
    return CppTarget(
        id=f"bazel://{t.package}:{t.name}",
        name=t.name,
        kind=t.kind,
        root_dir=t.package,
        sources=sources,
        public_headers=public_headers,
        private_headers=(),
        include_dirs=tuple(dict.fromkeys(t.includes)),
        compile_defines=(),
        link_deps=tuple(dict.fromkeys(t.deps)),
        conditional_sources=(),
        is_test=t.testonly or t.kind in ("cc_test", "cc_fuzz_test"),
        is_benchmark=classification["is_benchmark"],
        is_app=classification["is_app"] and t.kind == "cc_binary",
        is_demo=classification["is_demo"],
        is_example=classification["is_example"],
    )


def _register_target(index: CppWorkspaceIndex, t: CppTarget) -> None:
    index.targets[t.id] = t
    for f in (*t.sources, *t.public_headers, *t.private_headers):
        existing = index.file_to_targets.get(f, ())
        if t.id not in existing:
            index.file_to_targets[f] = (*existing, t.id)

    # Populate include-search-roots and public-header path keys.
    search_roots: list[str] = []
    if t.include_dirs:
        search_roots.extend(t.include_dirs)
    # Heuristic: if the target's root_dir contains an ``include/`` subdir,
    # treat that as an implicit public include root even without an
    # explicit ``target_include_directories`` call (catches the leveldb
    # layout where ``include/leveldb/*.h`` lives under the project root
    # with no explicit declaration).
    if t.root_dir:
        impl_inc = (PurePosixPath(t.root_dir) / "include").as_posix()
    else:
        impl_inc = "include"
    if not any(d == impl_inc for d in search_roots):
        search_roots.append(impl_inc)
    index.target_include_search_dirs[t.id] = tuple(dict.fromkeys(search_roots))

    for hdr in t.public_headers:
        # Map ``include/leveldb/cache.h`` → itself (literal include path)
        index.public_header_includes.setdefault(hdr, hdr)
        # Also strip each candidate include-search root prefix so
        # ``#include "leveldb/cache.h"`` works.
        for root in search_roots:
            prefix = root.rstrip("/") + "/"
            if hdr.startswith(prefix):
                rel = hdr[len(prefix):]
                # Always prefer the most-specific include-key first match.
                index.public_header_includes.setdefault(rel, hdr)


def build_cpp_workspace_index(ctx: "ResolverContext") -> CppWorkspaceIndex:
    index = CppWorkspaceIndex()
    if ctx.repo_path is None:
        return index

    repo_path = ctx.repo_path.resolve()
    path_set = set(ctx.path_set)

    cmake_files = discover_cmake_reactor(repo_path)
    file_api_targets = parse_cmake_file_api_reply(repo_path)
    bazel_files = discover_bazel_packages(repo_path) if is_bazel_repo(repo_path) else []

    # Prefer File API output if present — it's authoritative.
    if file_api_targets:
        for ct in file_api_targets:
            _register_target(index, _cmake_target_to_cpp(ct, path_set=path_set))
    else:
        for cf in cmake_files:
            for ct in cf.targets:
                # Skip stub targets that ``target_sources`` created without
                # a corresponding ``add_*`` (we have no source list nor a
                # known kind).
                if ct.kind == "unknown" and not ct.sources and not ct.public_headers:
                    continue
                _register_target(index, _cmake_target_to_cpp(ct, path_set=path_set))

    for bf in bazel_files:
        for bt in bf.targets:
            _register_target(index, _bazel_target_to_cpp(bt, path_set=path_set))

    # Project export macros — scan every header in any target's public list
    # plus ``include/**/*.h`` as a fallback (covers projects with no public
    # header declared in CMake).
    macros: set[str] = set()
    scanned: set[str] = set()
    header_candidates: list[str] = []
    for t in index.targets.values():
        for h in t.public_headers:
            if h not in scanned:
                scanned.add(h)
                header_candidates.append(h)
        # ``target_compile_definitions`` ending in ``_EXPORT`` etc.
        for define in t.compile_defines:
            base = define.split("=", 1)[0].strip()
            if _EXPORT_LIKE_NAME_RE.match(base):
                macros.add(base)
    if not header_candidates:
        # Sorted: the 200-candidate cap below must cut deterministically.
        for p in sorted(path_set):
            if p.startswith(("include/", "include\\")) and p.lower().endswith((".h", ".hpp", ".hxx")):
                header_candidates.append(p)
                if len(header_candidates) > 200:
                    break
    for rel in header_candidates:
        abs_p = str((repo_path / rel).resolve())
        macros.update(_scan_header_for_export_macros(abs_p))
    _scan_header_for_export_macros.cache_clear()

    # Filter out anything that doesn't look like an export marker name.
    cleaned = {m for m in macros if _EXPORT_LIKE_NAME_RE.match(m) or m.endswith(("_EXPORT", "_API", "_DLL", "_PUBLIC", "_VISIBLE"))}
    index.project_export_macros = frozenset(cleaned)

    log.debug(
        "cpp_workspace_index_built",
        targets=len(index.targets),
        files=len(index.file_to_targets),
        public_keys=len(index.public_header_includes),
        export_macros=len(index.project_export_macros),
    )
    return index


_INDEX_KEY = "_cpp_workspace_index"


def get_or_build_cpp_index(ctx: "ResolverContext") -> CppWorkspaceIndex:
    cached = getattr(ctx, _INDEX_KEY, None)
    if cached is not None:
        return cached
    index = build_cpp_workspace_index(ctx)
    setattr(ctx, _INDEX_KEY, index)
    return index


__all__ = [
    "CppTarget",
    "CppWorkspaceIndex",
    "build_cpp_workspace_index",
    "get_or_build_cpp_index",
    "is_stdlib_include",
]
