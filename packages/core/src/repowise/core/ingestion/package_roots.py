"""Which directories of a repo are package roots, read from disk.

One walk, one manifest list. The list is
:meth:`LanguageRegistry.package_manifest_filenames` — the single source of
truth for "is this directory a package" — so registering a language grants a
repo in that language monorepo bucketing with no second edit here.

Why a disk scan rather than the indexed file list: the health analyzer used to
derive package roots from ``parsed_files``, and the traverser drops any file
whose language it cannot detect. Measured on the current tree, 18 manifest
names are dropped that way — ``go.mod``, ``pom.xml``, ``build.gradle``,
``Gemfile``, ``build.sbt``, ``deps.edn``, ``rebar.config`` and ``setup.cfg``
among them — so Go, Maven, Groovy-Gradle, Ruby, Scala, Clojure and Erlang
monorepos silently got the top-level-directory fallback. Reading the manifests'
*paths* off disk needs no parse, no embedding and no ingestion: a package root
is a directory name, and nothing here opens a manifest.

.NET is **not** among the languages this fixes, and deliberately so: the file
that declares a .NET package is the ``.csproj``, which is a glob and therefore
cannot live in a spec's exact-filename ``manifest_files``. Every name C# does
declare is MSBuild/NuGet configuration and is excluded as such, so C#
contributes no package roots and a .NET monorepo keeps the top-level fallback.
Closing that needs glob support in the manifest list, which is its own change.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from ..fs_walk import walk_repo
from .languages.registry import REGISTRY as _LANG_REGISTRY


def package_manifest_names() -> frozenset[str]:
    """The manifest filenames that mark a directory as a package root."""
    return _LANG_REGISTRY.package_manifest_filenames()


def _default_prune(repo_root: Path) -> Callable[[Path], bool]:
    """Traversal's own boundary test, imported lazily to avoid a cycle.

    ``walk_repo`` already prunes vendored directories and nested checkouts,
    but not gitignored ones, and a gitignored tree can be large: this repo's
    ``packages/vscode/.vscode-test`` is a downloaded VS Code archive carrying
    500 vendored ``package.json`` files. Reusing the traverser's test rather
    than restating it also means the scan stops exactly where indexing stops.

    Stock settings on purpose. A repo indexed with ``--include-nested-repos``
    analyzes files the default traverser prunes, so a manifest inside a nested
    repo is not seen as a package root and those files keep the top-level
    fallback. Every writer of ``module`` resolves roots the same way, so they
    agree; passing per-repo settings to some callers and not others is what
    would make them disagree, and disagreement means each ``repowise update``
    reports work forever.
    """
    from .traverser import FileTraverser

    return FileTraverser(repo_root).dir_chain_skipped


def scan_package_roots(
    repo_root: str | os.PathLike[str],
    *,
    is_pruned: Callable[[Path], bool] | None = None,
) -> set[str]:
    """Repo-relative POSIX directories holding a package manifest.

    A manifest at the repo root is omitted: it would put every file in one
    bucket, which is the degenerate case package attribution exists to avoid.

    Walks via :func:`~repowise.core.fs_walk.walk_repo`, so vendored trees,
    nested git repos and junction cycles are already excluded. ``is_pruned``
    adds the ignore-file layer on top: it receives each candidate directory as
    a repo-relative :class:`Path` and returns True to skip it and everything
    beneath it. It defaults to a stock :class:`FileTraverser`'s boundary test;
    pass a configured traverser's :meth:`~FileTraverser.dir_chain_skipped` (or
    use :meth:`FileTraverser.package_root_dirs`) when the repo has submodule
    or exclude settings.
    """
    root = Path(repo_root)
    names = package_manifest_names()
    prune = is_pruned if is_pruned is not None else _default_prune(root)
    roots: set[str] = set()

    for dirpath, dirnames, filenames in walk_repo(root):
        rel_dir = Path(dirpath).relative_to(root)
        # Prune in place so the walk never descends — the point of the scan is
        # to be cheap enough to run on every update.
        dirnames[:] = [d for d in dirnames if not prune(rel_dir / d)]
        if rel_dir == Path() or not names.intersection(filenames):
            continue
        roots.add(rel_dir.as_posix())

    return roots


def module_for(rel_path: str, package_roots: set[str]) -> str | None:
    """The package boundary *rel_path* belongs to, or its top-level directory.

    ``module`` is the directory/package axis and nothing else. It used to be
    ``community_label or top_level_dir``, which mixed two namespaces in one
    field: graph community labels are semantic clusters named after a member
    directory, so a file could report a module it does not live in (measured on
    this repo: 1,355 of 3,263 files, e.g. every ``packages/api-client`` source
    file reporting ``tests/unit``). Worse, only the full-index path ever passed
    a community map, so which namespace a row carried depended on which code
    path last wrote it.

    The top-level directory alone is not enough either: on a monorepo it
    buckets 69% of this repo under ``packages``. So prefer the deepest
    enclosing package root and fall back to the top-level directory, which is
    exactly the old behaviour on a repo with no nested packages.

    Root-level files return ``None`` so the rollup does not grow a phantom
    ``""`` bucket.
    """
    norm = rel_path.replace("\\", "/")
    if "/" not in norm:
        return None
    if package_roots:
        parts = norm.split("/")
        # Deepest first, so a nested package wins over the one containing it.
        for i in range(len(parts) - 1, 0, -1):
            candidate = "/".join(parts[:i])
            if candidate in package_roots:
                return candidate
    head = norm.split("/", 1)[0]
    return head or None


def package_roots_from_paths(all_paths: set[str]) -> set[str]:
    """Package roots inferred from a file list, for callers with no checkout.

    The fallback when :func:`scan_package_roots` cannot run. It sees only
    manifests the traverser emitted, so it misses every language in the dropped
    list above — which is the gap the disk scan exists to close.
    """
    names = package_manifest_names()
    roots: set[str] = set()
    for p in all_paths:
        norm = p.replace("\\", "/")
        if "/" not in norm:
            continue
        head, base = norm.rsplit("/", 1)
        if base in names:
            roots.add(head)
    return roots
