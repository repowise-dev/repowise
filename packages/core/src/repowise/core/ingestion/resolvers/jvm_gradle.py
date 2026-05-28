"""Gradle multi-module index for JVM (Java + Kotlin) import resolution.

Parses ``settings.gradle(.kts)`` for subprojects, discovers source-set
directories (main, test, integrationTest, etc.), reads ``package``
declarations from ``.java`` and ``.kt`` files, and builds a unified
lookup index consumed by both the Java and Kotlin import resolvers.

Superset of the previous ``kotlin_gradle.py`` (which is now a thin
re-export facade for backwards compatibility).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from .context import ResolverContext

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_INCLUDE_RE = re.compile(r'include\s*\(\s*((?:"[^"]+"\s*,?\s*)+)\)')
_INCLUDE_ITEM_RE = re.compile(r'"([^"]+)"')
_INCLUDE_LINE_RE = re.compile(r'include\s+([\'"])([^\'"]+)\1')
_INCLUDE_BUILD_RE = re.compile(r'includeBuild\s*\(\s*"([^"]+)"\s*\)')
_PROJECT_DIR_RE = re.compile(
    r'project\s*\(\s*"([^"]+)"\s*\)\s*\.\s*projectDir\s*=\s*file\s*\(\s*"([^"]+)"\s*\)'
)
_SRCDIRS_RE = re.compile(
    r'srcDirs?\s*[=(]\s*((?:listOf|setOf)?\s*\(?\s*(?:"[^"]+"\s*,?\s*)+\)?)',
    re.DOTALL,
)
_SOURCESET_BLOCK_RE = re.compile(r'sourceSets\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', re.DOTALL)
_SOURCESET_NAME_RE = re.compile(
    r'(?:val\s+|getByName\s*\(\s*"|named\s*\(\s*"|create\s*\(\s*"|register\s*\(\s*")?'
    r'(\w+)(?:"\s*\))?\s*\{',
)
_PLUGIN_ID_RE = re.compile(r'id\s*\(\s*"([^"]+)"\s*\)')
_PLUGIN_ID_GROOVY_RE = re.compile(r"id\s+['\"]([^'\"]+)['\"]")
_APPLY_PLUGIN_RE = re.compile(r"apply\s+plugin:\s*['\"]([^'\"]+)['\"]")
_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)
_ROOT_PROJECT_NAME_RE = re.compile(r'rootProject\s*\.\s*name\s*=\s*"([^"]+)"')

_DEFAULT_MAIN_SRC_ROOTS = ("src/main/java", "src/main/kotlin")
_DEFAULT_TEST_SRC_ROOTS = ("src/test/java", "src/test/kotlin")

_TEST_SOURCESET_MARKERS = frozenset({
    "test", "it", "jmh", "functional", "smoke", "acceptance",
    "integration", "e2e", "perf", "benchmark", "fray",
})

_JVM_EXTENSIONS = frozenset({".java", ".kt"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceSet:
    name: str
    is_main: bool
    is_test: bool
    src_dirs: tuple[str, ...]
    resource_dirs: tuple[str, ...] = ()


@dataclass(frozen=True)
class JvmGradleProject:
    id: str
    root_dir: str
    source_sets: dict[str, SourceSet] = field(default_factory=dict)
    plugins: frozenset[str] = frozenset()


@dataclass
class JvmGradleIndex:
    """Unified package-to-files index for Java + Kotlin Gradle projects."""

    modules: dict[str, list[str]] = field(default_factory=dict)
    package_to_files: dict[str, list[str]] = field(default_factory=dict)
    projects: dict[str, JvmGradleProject] = field(default_factory=dict)

    # Custom project-dir overrides from settings.gradle
    _project_dir_overrides: dict[str, str] = field(default_factory=dict)

    def lookup_class(self, fqn: str) -> list[str]:
        """Match ``com.example.Foo`` to files declaring ``package com.example``
        and named ``Foo.java`` / ``Foo.kt`` / ``Foo.kts``.
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

    def files_in_package(self, package_fqn: str) -> list[str]:
        """Return all files declaring the given package."""
        return self.package_to_files.get(package_fqn, [])


# ---------------------------------------------------------------------------
# Settings parsing
# ---------------------------------------------------------------------------

def _parse_settings(settings_file: Path) -> tuple[list[str], dict[str, str]]:
    """Return (module_names, project_dir_overrides) from settings.gradle(.kts)."""
    if not settings_file.is_file():
        return [], {}
    try:
        text = settings_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return [], {}

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

    # Project-dir overrides: project(":foo").projectDir = file("custom/path")
    overrides: dict[str, str] = {}
    for match in _PROJECT_DIR_RE.finditer(text):
        mod = match.group(1).lstrip(":")
        overrides[mod] = match.group(2)

    return result, overrides


def _module_dir(repo_path: Path, module_name: str, overrides: dict[str, str]) -> Path:
    if module_name in overrides:
        return repo_path / overrides[module_name]
    return repo_path / module_name.replace(":", "/")


# ---------------------------------------------------------------------------
# Source-set discovery
# ---------------------------------------------------------------------------

def _is_test_source_set(name: str) -> bool:
    lower = name.lower()
    for marker in _TEST_SOURCESET_MARKERS:
        if marker in lower:
            return True
    return False


def _detect_source_sets_from_dirs(module_dir: Path) -> list[SourceSet]:
    """Detect source sets by scanning src/<name>/{java,kotlin} directories."""
    src_dir = module_dir / "src"
    if not src_dir.is_dir():
        return []
    source_sets: list[SourceSet] = []
    try:
        entries = sorted(src_dir.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir():
            continue
        name = entry.name
        sub_dirs: list[str] = []
        for lang_dir in ("java", "kotlin"):
            candidate = entry / lang_dir
            if candidate.is_dir():
                sub_dirs.append(f"src/{name}/{lang_dir}")
        # Also check for files directly in src/<name> (some projects put .kt/.java there)
        if not sub_dirs:
            has_jvm = any(
                f.suffix in _JVM_EXTENSIONS
                for f in entry.iterdir()
                if f.is_file()
            ) if entry.is_dir() else False
            if has_jvm:
                sub_dirs.append(f"src/{name}")
        if sub_dirs:
            is_main = name == "main"
            is_test = _is_test_source_set(name) if not is_main else False
            source_sets.append(SourceSet(
                name=name,
                is_main=is_main,
                is_test=is_test,
                src_dirs=tuple(sub_dirs),
            ))
    return source_sets


def _source_roots_for_module(module_dir: Path) -> list[str]:
    """Return source-root paths (relative to module_dir).

    Honours explicit ``srcDirs(...)`` overrides in ``build.gradle(.kts)``;
    falls back to standard defaults.
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
        seen: set[str] = set()
        return [o for o in overrides if not (o in seen or seen.add(o))]  # type: ignore[func-returns-value]
    return list(_DEFAULT_MAIN_SRC_ROOTS)


def _detect_plugins(module_dir: Path) -> frozenset[str]:
    """Extract plugin IDs from build.gradle(.kts)."""
    plugins: list[str] = []
    for build_name in ("build.gradle.kts", "build.gradle"):
        build = module_dir / build_name
        if not build.is_file():
            continue
        try:
            text = build.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat in (_PLUGIN_ID_RE, _PLUGIN_ID_GROOVY_RE, _APPLY_PLUGIN_RE):
            for m in pat.finditer(text):
                plugins.append(m.group(1))
    return frozenset(plugins)


# ---------------------------------------------------------------------------
# Package extraction (cached per file)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=8192)
def _extract_file_info(abs_path: str) -> tuple[str, tuple[str, ...], bool, bool]:
    """Return (package_decl, top_level_types, is_module_info, is_package_info).

    Reads the file once and caches the result. Cheap line scan, no AST.
    """
    path = Path(abs_path)
    name = path.name
    is_module_info = name == "module-info.java"
    is_package_info = name == "package-info.java"

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "", (), is_module_info, is_package_info

    package = ""
    pkg_match = _PACKAGE_RE.search(text)
    if pkg_match:
        package = pkg_match.group(1)

    top_level: list[str] = []
    _TYPE_DECL_RE = re.compile(
        r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+|abstract\s+|final\s+|sealed\s+|open\s+|data\s+)*"
        r"(?:class|interface|enum|object|record|annotation)\s+(\w+)",
        re.MULTILINE,
    )
    for m in _TYPE_DECL_RE.finditer(text):
        top_level.append(m.group(1))

    return package, tuple(top_level), is_module_info, is_package_info


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

def build_jvm_gradle_index(repo_path: Path | None) -> JvmGradleIndex:
    """Walk Gradle config + JVM sources to build the package-resolution index."""
    index = JvmGradleIndex()
    if repo_path is None or not repo_path.is_dir():
        return index

    settings_paths = [repo_path / "settings.gradle.kts", repo_path / "settings.gradle"]
    modules: list[str] = []
    project_dir_overrides: dict[str, str] = {}
    for sp in settings_paths:
        mods, ovr = _parse_settings(sp)
        modules.extend(mods)
        project_dir_overrides.update(ovr)

    if not modules:
        if (repo_path / "build.gradle").is_file() or (repo_path / "build.gradle.kts").is_file():
            modules = [""]

    resolved_repo = repo_path.resolve()

    for module_name in modules:
        mod_dir = _module_dir(repo_path, module_name, project_dir_overrides) if module_name else repo_path
        if not mod_dir.is_dir():
            continue

        plugins = _detect_plugins(mod_dir)

        # Discover source sets
        source_sets = _detect_source_sets_from_dirs(mod_dir)
        if not source_sets:
            # Fallback: use explicit srcDirs or defaults
            roots = _source_roots_for_module(mod_dir)
            source_sets = [SourceSet(
                name="main",
                is_main=True,
                is_test=False,
                src_dirs=tuple(roots),
            )]

        ss_map: dict[str, SourceSet] = {ss.name: ss for ss in source_sets}
        project = JvmGradleProject(
            id=module_name or "<root>",
            root_dir=mod_dir.relative_to(resolved_repo).as_posix() if module_name else "",
            source_sets=ss_map,
            plugins=plugins,
        )
        index.projects[project.id] = project

        rel_roots: list[str] = []
        for ss in source_sets:
            for src_dir in ss.src_dirs:
                root_path = (mod_dir / src_dir).resolve()
                if not root_path.is_dir():
                    continue
                try:
                    rel = root_path.relative_to(resolved_repo).as_posix()
                except ValueError:
                    continue
                rel_roots.append(rel)

                for jvm_file in root_path.rglob("*"):
                    if jvm_file.suffix not in _JVM_EXTENSIONS or not jvm_file.is_file():
                        continue
                    try:
                        rel_file = jvm_file.relative_to(resolved_repo).as_posix()
                    except ValueError:
                        continue
                    package, _types, _is_mod, _is_pkg = _extract_file_info(str(jvm_file.resolve()))
                    if not package:
                        continue
                    index.package_to_files.setdefault(package, []).append(rel_file)

        if rel_roots:
            index.modules[module_name or "<root>"] = rel_roots

    _extract_file_info.cache_clear()

    log.debug(
        "Built JVM Gradle index",
        modules=len(index.modules),
        packages=len(index.package_to_files),
        projects=len(index.projects),
    )
    return index


def get_or_build_jvm_gradle_index(ctx: "ResolverContext") -> JvmGradleIndex:
    """Return the cached JvmGradleIndex, building it on first access."""
    cached = getattr(ctx, "_jvm_gradle_index", None)
    if cached is not None:
        return cached
    index = build_jvm_gradle_index(ctx.repo_path)
    ctx._jvm_gradle_index = index  # type: ignore[attr-defined]
    return index


def resolve_via_jvm_gradle_index(module_path: str, ctx: "ResolverContext") -> str | None:
    """Resolve an FQN to a single file via the Gradle index."""
    index = get_or_build_jvm_gradle_index(ctx)
    matches = index.lookup_class(module_path)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Back-compat: KotlinProjectIndex facade for kotlin_gradle.py re-exports
# ---------------------------------------------------------------------------

@dataclass
class KotlinProjectIndex:
    """Thin wrapper for backwards compatibility with kotlin_gradle.py consumers."""

    _inner: JvmGradleIndex = field(default_factory=JvmGradleIndex)

    @property
    def modules(self) -> dict[str, list[str]]:
        return self._inner.modules

    @property
    def package_to_files(self) -> dict[str, list[str]]:
        return self._inner.package_to_files

    def lookup_class(self, fqn: str) -> list[str]:
        return self._inner.lookup_class(fqn)


def build_kotlin_index(repo_path: Path | None) -> KotlinProjectIndex:
    """Back-compat: build a KotlinProjectIndex wrapping the JVM Gradle index."""
    return KotlinProjectIndex(_inner=build_jvm_gradle_index(repo_path))


def get_or_build_kotlin_index(ctx: "ResolverContext") -> KotlinProjectIndex:
    """Back-compat: return a KotlinProjectIndex wrapping the JVM Gradle index."""
    jvm_index = get_or_build_jvm_gradle_index(ctx)
    return KotlinProjectIndex(_inner=jvm_index)


def resolve_via_kotlin_index(module_path: str, ctx: "ResolverContext") -> str | None:
    """Back-compat: resolve via the JVM Gradle index."""
    return resolve_via_jvm_gradle_index(module_path, ctx)
