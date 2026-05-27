"""Go package index — groups ``.go`` files by their package directory.

Go's unit of compilation is the *package*: a directory of ``.go`` files
sharing a ``package X`` clause. Files in the same package reference each
other's symbols with **no import statement**, and a cross-package import
names a *package directory*, not a file. The legacy resolver
(``resolve_go_import``) returns only the first ``.go`` file it finds in an
imported package directory, so sibling files in that package get
``in_degree == 0`` and are mis-flagged as unreachable.

``GoPackageIndex`` models packages as first-class directories and maps an
import path → every ``.go`` file in the resolved package. It is the
foundation the call/type-ref/dead-code phases consume. Built once per
resolver run via :func:`get_or_build_go_index` and cached on the context,
mirroring the ``DotNetProjectIndex`` / ``CargoWorkspaceIndex`` pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import structlog

from .go import read_go_modules

if TYPE_CHECKING:
    from .context import ResolverContext

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class GoPackage:
    """A single Go package — one directory of sibling ``.go`` files."""

    dir: str  # repo-relative POSIX path of the package directory ("" = repo root)
    pkg_name: str  # the ``package X`` clause (best-effort; "" if undetected)
    is_main: bool  # any file declares ``package main``
    files: tuple[str, ...]  # repo-relative POSIX ``.go`` paths, sorted
    build_constrained: bool = False  # any file carries //go:build or // +build
    has_init: bool = False  # any file declares a top-level ``func init``
    # name → defining file(s) within this package. Deferred to Phase 3
    # (call/type-ref resolution) — left empty here to keep the warmup pure
    # and dependency-light. See GO_PARITY_PLAN.md Phase 3.
    exported_symbols: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass
class GoPackageIndex:
    """Repo-scoped view of every local Go package."""

    packages: dict[str, GoPackage] = field(default_factory=dict)
    """Keyed by package directory (repo-relative POSIX)."""

    import_path_to_dir: dict[str, str] = field(default_factory=dict)
    """Maps a fully-qualified import path → its package directory."""

    def files_for_import(self, import_path: str) -> tuple[str, ...]:
        """Return every ``.go`` file in the package the import resolves to.

        Empty tuple when the import path is not a local package (e.g. an
        external ``github.com/...`` dependency) — callers fall back to the
        external-node path for those.
        """
        pkg_dir = self.import_path_to_dir.get(import_path)
        if pkg_dir is None:
            return ()
        pkg = self.packages.get(pkg_dir)
        return pkg.files if pkg else ()

    def package_for_file(self, file_path: str) -> GoPackage | None:
        """Return the package owning *file_path*, or None."""
        parent = PurePosixPath(file_path).parent.as_posix()
        if parent == ".":
            parent = ""
        return self.packages.get(parent)


_BUILD_TAG_PREFIXES = ("//go:build", "// +build", "//+build")


def _scan_go_file(text: str) -> tuple[str, bool, bool, bool]:
    """Return ``(pkg_name, is_main, build_constrained, has_init)``.

    Reads only what's needed to characterise the file: the ``package``
    clause, whether a build constraint is present (must appear before the
    package clause, in the leading comment block), and whether a top-level
    ``func init()`` is declared. Cheap line scan — no tree-sitter.
    """
    pkg_name = ""
    build_constrained = False
    has_init = False
    seen_package = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not seen_package and line.startswith(_BUILD_TAG_PREFIXES):
            build_constrained = True
            continue
        if not seen_package and line.startswith("package "):
            pkg_name = line.split(None, 1)[1].split()[0].strip()
            seen_package = True
            continue
        # Top-level ``func init()`` — no receiver, exact name.
        if seen_package and line.startswith("func init(") and "init()" in line.replace(" ", ""):
            has_init = True
    return pkg_name, pkg_name == "main", build_constrained, has_init


def _read_text(ctx: "ResolverContext", rel_path: str) -> str:
    if ctx.repo_path is None:
        return ""
    try:
        return (ctx.repo_path / rel_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _import_path_for_dir(
    pkg_dir: str, go_modules: tuple[tuple[str, str], ...]
) -> str | None:
    """Compute a package's import path from the enclosing module.

    *go_modules* is ``((module_dir, module_path), ...)`` sorted longest
    module-path first; we re-rank by the longest matching *module_dir* so
    nested modules in a monorepo win over the root module.
    """
    best: tuple[str, str] | None = None
    best_len = -1
    for module_dir, mod_path in go_modules:
        if module_dir == "" or pkg_dir == module_dir or pkg_dir.startswith(module_dir + "/"):
            if len(module_dir) > best_len:
                best = (module_dir, mod_path)
                best_len = len(module_dir)
    if best is None:
        return None
    module_dir, mod_path = best
    suffix = pkg_dir[len(module_dir):].lstrip("/") if module_dir else pkg_dir
    return f"{mod_path}/{suffix}" if suffix else mod_path


def build_go_package_index(ctx: "ResolverContext") -> GoPackageIndex:
    """Group every ``.go`` file in ``ctx.path_set`` by package directory.

    One walk over the path set; each file is read at most once to detect
    ``package main`` / build tags / ``func init``. The import-path → dir
    mapping reuses ``read_go_modules`` for monorepo correctness.
    """
    # Resolve the module list. Prefer the context's pre-read tuple; fall
    # back to a fresh read, then to the legacy single-module field so
    # manually-built test contexts still map import paths.
    go_modules = ctx.go_modules or read_go_modules(ctx.repo_path)
    if not go_modules and ctx.go_module_path:
        go_modules = (("", ctx.go_module_path),)

    files_by_dir: dict[str, list[str]] = {}
    for path in ctx.path_set:
        if not path.endswith(".go"):
            continue
        parent = PurePosixPath(path).parent.as_posix()
        if parent == ".":
            parent = ""
        files_by_dir.setdefault(parent, []).append(path)

    index = GoPackageIndex()
    for pkg_dir, files in files_by_dir.items():
        files.sort()
        pkg_name = ""
        is_main = False
        build_constrained = False
        has_init = False
        for f in files:
            name, file_main, file_bc, file_init = _scan_go_file(_read_text(ctx, f))
            # First non-empty package name wins (sibling files agree by
            # Go rule, except for the ``_test`` external-test package).
            if name and not pkg_name:
                pkg_name = name
            is_main = is_main or file_main
            build_constrained = build_constrained or file_bc
            has_init = has_init or file_init
        index.packages[pkg_dir] = GoPackage(
            dir=pkg_dir,
            pkg_name=pkg_name,
            is_main=is_main,
            files=tuple(files),
            build_constrained=build_constrained,
            has_init=has_init,
        )
        import_path = _import_path_for_dir(pkg_dir, go_modules)
        if import_path is not None:
            index.import_path_to_dir[import_path] = pkg_dir

    log.debug(
        "Built Go package index",
        packages=len(index.packages),
        import_paths=len(index.import_path_to_dir),
        modules=len(go_modules),
    )
    return index


_INDEX_KEY = "_go_package_index"


def get_or_build_go_index(ctx: "ResolverContext") -> GoPackageIndex:
    """Return the cached GoPackageIndex, building it on first access."""
    cached = getattr(ctx, _INDEX_KEY, None)
    if cached is not None:
        return cached
    index = build_go_package_index(ctx)
    setattr(ctx, _INDEX_KEY, index)
    return index
