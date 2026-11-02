"""Which independent build contains a file.

A repo can hold several builds that never see each other's code: Ktor's
samples are thirty Gradle builds side by side, a Flutter monorepo has one
pubspec per package, an iOS app ships local Swift packages. A bare name in one
build cannot mean a top-level symbol of another, so the name-only call tiers
(same package, global unique) stay inside the caller's build unless the build
composes the other in (`includeBuild`, `buildSrc`) or the file imports it.

A build root is the deepest directory holding ``settings.gradle(.kts)``,
``pubspec.yaml`` or ``Package.swift``, or named ``buildSrc`` (Gradle builds it
on its own). A Gradle project with no settings file anywhere above it is its
own build, rooted at its outermost ``build.gradle(.kts)``. A file under no
root belongs to no build, and nothing is refused for it.
"""

from __future__ import annotations

import os
import posixpath
from collections.abc import Iterable
from pathlib import Path

from .resolvers.jvm_gradle import included_builds

_SETTINGS = ("settings.gradle.kts", "settings.gradle")
_ROOT_FILES = (*_SETTINGS, "pubspec.yaml", "Package.swift")
_GRADLE_BUILDS = ("build.gradle.kts", "build.gradle")


def _ancestors(path: str) -> list[str]:
    """Every directory above *path*, the repo root ``""`` first."""
    parts = path.split("/")[:-1]
    return ["", *("/".join(parts[: i + 1]) for i in range(len(parts)))]


def _root_marks(here: Path, inside_gradle: bool) -> tuple[bool, bool]:
    """``(roots a Gradle build, roots a Dart or Swift package)`` for directory *here*."""
    gradle = any(os.path.isfile(here / name) for name in _SETTINGS)
    if not gradle and not inside_gradle:
        gradle = any(os.path.isfile(here / name) for name in _GRADLE_BUILDS)
    return gradle, any(os.path.isfile(here / name) for name in _ROOT_FILES[2:])


def _scan_roots(repo: Path, dirs: set[str]) -> set[str]:
    """The build roots among *dirs*."""
    roots: set[str] = set()
    # Directories already inside a Gradle build, where a build.gradle is a
    # subproject rather than a build of its own.
    in_gradle: set[str] = set()
    for d in sorted(dirs, key=lambda d: d.count("/")):  # parents before children
        inside = bool(d) and posixpath.dirname(d) in in_gradle
        gradle_root, other_root = _root_marks(repo / d, inside)
        if gradle_root or other_root or posixpath.basename(d) == "buildSrc":
            roots.add(d)
        if gradle_root or inside:
            in_gradle.add(d)
    return roots


class BuildRoots:
    """The build roots of one repo, read from the indexed paths and the disk."""

    def __init__(self, repo_path: str | None, paths: Iterable[str]) -> None:
        self._roots: set[str] = set()
        self._includes: dict[str, frozenset[str]] = {}
        if repo_path is None:
            return
        repo = Path(repo_path)
        self._roots = _scan_roots(repo, {d for path in paths for d in _ancestors(path)})
        for root in self._roots:
            self._includes[root] = frozenset(self._composed(repo, root))

    def _composed(self, repo: Path, root: str) -> Iterable[str]:
        """The roots *root*'s Gradle build composes in: its ``buildSrc`` and included builds."""
        build_src = posixpath.join(root, "buildSrc")
        if build_src in self._roots:
            yield build_src
        for name in _SETTINGS:
            settings = repo / root / name
            if not settings.is_file():
                continue
            text = settings.read_text(encoding="utf-8", errors="ignore")
            for included in included_builds(text):
                target = posixpath.normpath(posixpath.join(root, included))
                yield "" if target == "." else target

    def root_of(self, path: str) -> str | None:
        """The deepest build root above *path*, or None."""
        for d in reversed(_ancestors(path)):
            if d in self._roots:
                return d
        return None

    def composes(self, root: str | None, other: str | None) -> bool:
        """Does the build at *root* compose the build at *other* in?"""
        return root is not None and other in self._includes.get(root, ())

    def script_sees(self, script: str, other: str | None) -> bool:
        """Does Gradle Kotlin *script* compile against code in build root *other*?

        A build script sees only the build logic its build composes in. A
        precompiled script plugin (under a ``src`` directory of its build)
        also sees the sources it is compiled with.
        """
        own = self.root_of(script)
        if self.composes(own, other):
            return True
        below = script[len(own) + 1 :] if own else script
        return other == own and own is not None and "src" in below.split("/")[:-1]

    def separates(self, root: str | None, other: str | None) -> bool:
        """Are *root* and *other* two builds, neither composed into *root*?"""
        if root is None or other is None or root == other:
            return False
        return not self.composes(root, other)
