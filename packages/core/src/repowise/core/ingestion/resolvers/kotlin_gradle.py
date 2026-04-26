"""Gradle multi-module / sourceSets index for Kotlin import resolution.

Real Android and JVM projects use Gradle layouts where source roots live
under per-module directories: ``app/src/main/kotlin/...``,
``feature-foo/src/main/kotlin/...``. The default ``resolve_kotlin_import``
relies on stem matching, which fails when two modules contain a class of
the same name.

This module:
- enumerates Gradle subprojects from ``settings.gradle(.kts)`` ``include(...)``
  declarations,
- collects source roots per subproject (defaults: ``src/main/kotlin``,
  ``src/main/java``; explicit ``srcDirs`` overrides honoured),
- scans each source file's ``package`` declaration to build a
  ``{fully.qualified.Name → [path, ...]}`` map.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import ResolverContext


_INCLUDE_RE = re.compile(r'include\s*\(\s*((?:"[^"]+"\s*,?\s*)+)\)')
_INCLUDE_ITEM_RE = re.compile(r'"([^"]+)"')
_INCLUDE_LINE_RE = re.compile(r'include\s+([\'"])([^\'"]+)\1')
_SRCDIRS_RE = re.compile(
    r'srcDirs?\s*[=(]\s*((?:listOf|setOf)?\s*\(?\s*(?:"[^"]+"\s*,?\s*)+\)?)',
    re.DOTALL,
)
_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)
_DEFAULT_SRC_ROOTS = ("src/main/kotlin", "src/main/java")


@dataclass
class KotlinProjectIndex:
    modules: dict[str, list[str]] = field(default_factory=dict)
    package_to_files: dict[str, list[str]] = field(default_factory=dict)

    def lookup_class(self, fqn: str) -> list[str]:
        """Match ``com.example.Foo`` → files declaring ``package com.example``
        and named ``Foo.kt``/``Foo.kts``.
        """
        if "." not in fqn:
            return self.package_to_files.get(fqn, [])
        package, local = fqn.rsplit(".", 1)
        candidates = self.package_to_files.get(package, [])
        local_lower = local.lower()
        return [
            p for p in candidates
            if p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() == local_lower
        ]


def _parse_settings(settings_file: Path) -> list[str]:
    """Return Gradle module names from ``include(...)`` lines.

    Module names use ``:foo:bar`` syntax; we keep them as-written so callers
    can map ``:foo:bar`` → ``foo/bar`` directory.
    """
    if not settings_file.is_file():
        return []
    try:
        text = settings_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    modules: list[str] = []
    for match in _INCLUDE_RE.finditer(text):
        for item in _INCLUDE_ITEM_RE.findall(match.group(1)):
            modules.append(item.lstrip(":"))
    for match in _INCLUDE_LINE_RE.finditer(text):
        modules.append(match.group(2).lstrip(":"))
    # Dedupe preserving order
    seen: set[str] = set()
    result: list[str] = []
    for m in modules:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result


def _module_dir(repo_path: Path, module_name: str) -> Path:
    return repo_path / module_name.replace(":", "/")


def _source_roots_for_module(module_dir: Path) -> list[str]:
    """Return source-root paths (relative to module_dir) for *module_dir*.

    Honours explicit ``srcDirs(...)`` overrides in ``build.gradle(.kts)``;
    falls back to standard ``src/main/{kotlin,java}`` defaults.
    """
    overrides: list[str] = []
    for build_name in ("build.gradle.kts", "build.gradle"):
        build = module_dir / build_name
        if not build.is_file():
            continue
        try:
            text = build.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for match in _SRCDIRS_RE.finditer(text):
            for item in _INCLUDE_ITEM_RE.findall(match.group(1)):
                overrides.append(item)
    if overrides:
        # Dedupe + return verbatim (caller resolves relative-to-module)
        seen: set[str] = set()
        return [o for o in overrides if not (o in seen or seen.add(o))]
    return list(_DEFAULT_SRC_ROOTS)


def build_kotlin_index(repo_path: Path | None) -> KotlinProjectIndex:
    """Walk Gradle config + Kotlin sources to build the package-resolution index."""
    index = KotlinProjectIndex()
    if repo_path is None or not repo_path.is_dir():
        return index

    # Discover modules. Root-level Gradle build with no settings.gradle still
    # counts as a single anonymous module rooted at repo_path.
    settings_paths = [repo_path / "settings.gradle.kts", repo_path / "settings.gradle"]
    modules: list[str] = []
    for sp in settings_paths:
        modules.extend(_parse_settings(sp))
    if not modules:
        # Single-module fallback: only consider the repo itself when a
        # build.gradle is present.
        if (
            (repo_path / "build.gradle").is_file()
            or (repo_path / "build.gradle.kts").is_file()
        ):
            modules = [""]

    for module_name in modules:
        mod_dir = _module_dir(repo_path, module_name) if module_name else repo_path
        if not mod_dir.is_dir():
            continue
        roots = _source_roots_for_module(mod_dir)
        rel_roots: list[str] = []
        for root in roots:
            root_path = (mod_dir / root).resolve()
            if not root_path.is_dir():
                continue
            try:
                rel = root_path.relative_to(repo_path.resolve()).as_posix()
            except ValueError:
                continue
            rel_roots.append(rel)
            # Walk Kotlin files under this root
            for kt in root_path.rglob("*.kt"):
                try:
                    rel_kt = kt.relative_to(repo_path.resolve()).as_posix()
                except ValueError:
                    continue
                try:
                    text = kt.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                pkg_match = _PACKAGE_RE.search(text)
                if not pkg_match:
                    continue
                package = pkg_match.group(1)
                index.package_to_files.setdefault(package, []).append(rel_kt)
        if rel_roots:
            index.modules[module_name or "<root>"] = rel_roots
    return index


def get_or_build_kotlin_index(ctx: "ResolverContext") -> KotlinProjectIndex:
    cached = getattr(ctx, "_kotlin_index", None)
    if cached is not None:
        return cached
    index = build_kotlin_index(ctx.repo_path)
    ctx._kotlin_index = index  # type: ignore[attr-defined]
    return index


def resolve_via_kotlin_index(module_path: str, ctx: "ResolverContext") -> str | None:
    index = get_or_build_kotlin_index(ctx)
    matches = index.lookup_class(module_path)
    if matches:
        return matches[0]
    return None
