"""Gradle multi-module index for JVM (Java + Kotlin) import resolution.

Parses ``settings.gradle(.kts)`` for subprojects, discovers source-set
directories (main, test, integrationTest, etc.), reads ``package``
declarations from ``.java`` and ``.kt`` files, and builds a unified
lookup index consumed by both the Java and Kotlin import resolvers.

Superset of the previous ``kotlin_gradle.py`` (which is now a thin
re-export facade for backwards compatibility).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from repowise.core.fs_walk import glob_via
from repowise.core.ingestion.source_text import decode_source

if TYPE_CHECKING:
    from .context import ResolverContext

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# One quoted module name, in either quote style: 'core' or "core".
_QUOTED = r"""(?:'[^'\n]+'|"[^"\n]+")"""
# A whole ``include`` statement, in any of the forms Gradle accepts:
#   include("core", "api")          (Kotlin DSL / Groovy, parenthesised)
#   include('core', 'api')          (Groovy, parenthesised, single quotes)
#   include 'core', 'api', 'web'    (Groovy, space form)
#   include 'core',
#           'api'                   (Groovy, list continued over lines)
# ``\b`` after ``include`` keeps ``includeBuild`` out. Comments are stripped
# before matching, so one between two items does not end the list.
_INCLUDE_RE = re.compile(
    rf"\binclude\b\s*(?:\(\s*(?P<paren>[^)]*)\)|(?P<bare>{_QUOTED}(?:\s*,\s*{_QUOTED})*))"
)
# Each quoted name inside a matched ``include`` statement (group 1 or 2).
_INCLUDE_MODULE_RE = re.compile(r"""'([^'\n]+)'|"([^"\n]+)\"""")
# Double-quoted items of a ``srcDirs(...)`` list.
_INCLUDE_ITEM_RE = re.compile(r'"([^"]+)"')
_INCLUDE_BUILD_RE = re.compile(r"""includeBuild\s*\(?\s*(["'])([^"']+)\1""")
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
_PLUGIN_ID_RE = re.compile(r'id\s*\(\s*["\']([^"\']+)["\']\s*\)')
_PLUGIN_ID_GROOVY_RE = re.compile(r"id\s+['\"]([^'\"]+)['\"]")
_APPLY_PLUGIN_RE = re.compile(r"apply\s+plugin:\s*['\"]([^'\"]+)['\"]")
_APPLY_PLUGIN_KTS_RE = re.compile(r'apply\s*\(\s*plugin\s*=\s*["\']([^"\']+)["\']\s*\)')
_ALIAS_PLUGIN_RE = re.compile(r"alias\s*\(\s*([^)]+)\s*\)")
_PLUGINS_BLOCK_RE = re.compile(r"plugins\s*\{([^}]+)\}", re.DOTALL)
_KTS_BACKTICK_PLUGIN_RE = re.compile(r"`([^`]+)`")
_KTS_BARE_CORE_PLUGIN_RE = re.compile(
    r"^\s*(application|java-library|maven-publish|java)\s*$", re.MULTILINE
)
_GRADLE_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_GRADLE_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)
_ROOT_PROJECT_NAME_RE = re.compile(r'rootProject\s*\.\s*name\s*=\s*"([^"]+)"')
# A quoted fully-qualified class name: lowercase package segments, then a
# capitalised type (``'org.elasticsearch.system.indices.SystemIndicesQA'``).
_QUOTED_FQN_RE = re.compile(r"""['"]((?:[a-z_]\w*\.)+[A-Z]\w*)['"]""")
# A default-package class (``build-logic`` convention plugins) has no dots, so
# only a class-naming key vouches for the bare name.
_KEYED_BARE_CLASS_RE = re.compile(
    r"""\b(?:implementationClass|mainClass)\s*(?:=|\.set\s*\()\s*['"]([A-Z]\w*)['"]"""
)

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

def parse_settings(settings_file: Path) -> tuple[list[str], dict[str, str]]:
    """Return (module_names, project_dir_overrides) from settings.gradle(.kts)."""
    if not settings_file.is_file():
        return [], {}
    try:
        text = settings_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return [], {}

    # Drop comments first: a comment between two items would otherwise end
    # the list, and a commented-out include would still register.
    include_text = _GRADLE_BLOCK_COMMENT_RE.sub(
        lambda m: "\n" * m.group(0).count("\n"), text
    )
    include_text = _GRADLE_LINE_COMMENT_RE.sub("", include_text)

    modules: list[str] = []
    for match in _INCLUDE_RE.finditer(include_text):
        items = match.group("paren") if match.group("paren") is not None else match.group("bare")
        for single, double in _INCLUDE_MODULE_RE.findall(items):
            modules.append((single or double).lstrip(":"))

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


def included_builds(settings_text: str) -> list[str]:
    """The directories a settings script composes in with ``includeBuild``, as written."""
    return [match.group(2) for match in _INCLUDE_BUILD_RE.finditer(settings_text)]


def _module_dir(repo_path: Path, module_name: str, overrides: dict[str, str]) -> Path:
    if module_name in overrides:
        return repo_path / overrides[module_name]
    return repo_path / module_name.replace(":", "/")


# ---------------------------------------------------------------------------
# Source-set discovery
# ---------------------------------------------------------------------------

# ``<module>/src/<set>/kotlin/``: the source-set directory a JVM file sits in.
_SOURCE_SET_DIR_RE = re.compile(r"(?:^|/)src/[^/]+/(?=(?:java|kotlin)/)")


def source_set_dir(path: str) -> str | None:
    """The source-set directory *path* sits in (``okio/src/jvmMain/``), or None."""
    match = _SOURCE_SET_DIR_RE.search(path)
    return path[: match.end()] if match else None


# ---------------------------------------------------------------------------
# Kotlin Multiplatform source-set visibility
# ---------------------------------------------------------------------------

# ``dependsOn(commonMain)`` / ``jvmMain.dependsOn(zlibMain)`` in a build script.
_DEPENDS_ON_RE = re.compile(r"\bdependsOn\b")
_SET_CALL = r"""(?:getByName|named|maybeCreate|create)\s*\(\s*["'](\w+)["']\s*\)"""
_SET_INDEX = r"""sourceSets\s*\[\s*["'](\w+)["']\s*\]"""
# What follows ``dependsOn``: ``(commonMain)``, ``(sourceSets.commonMain)``,
# ``(getByName("commonMain"))``, ``(sourceSets["commonMain"])``, or Groovy's bare ``commonMain``.
_DEPENDS_ON_ARG_RE = re.compile(
    rf"\s*\(?\s*(?:sourceSets\s*\.\s*)?(?:{_SET_CALL}|{_SET_INDEX}|(\w+))"
)
# The receiver of ``x.dependsOn(..)``, read backwards from the dot.
_RECEIVER_RE = re.compile(rf"(?:{_SET_CALL}|{_SET_INDEX}|(\w+))\s*\.\s*$")
# The block a bare ``dependsOn(..)`` sits in, from the text before its ``{``.
_BLOCK_HEADERS = (
    re.compile(r"(?:val|var)\s+(\w+)\s+by\s+(?:getting|creating|registering)\s*$"),
    re.compile(rf"{_SET_CALL}\s*$"),
    re.compile(r"(?:getByName|named|maybeCreate|create|register)\s*\(\s*[\"'](\w+)[\"']\s*\)\s*$"),
    re.compile(_SET_INDEX + r"\s*$"),
    re.compile(r"(\w+)\s*$"),
)
# ``jvm("desktop")``: a custom-named target, whose source sets the default
# hierarchy template still hangs under the common ones.
_TARGET_RE = re.compile(r"""\b(?:jvm|js|wasmJs|wasmWasi)\s*\(\s*["'](\w+)["']""")
_MULTIPLATFORM_PLUGIN_RE = re.compile(
    r"""kotlin\s*\(\s*["']multiplatform["']\s*\)|kotlin[-.]?multiplatform|kotlinMultiplatform""",
    re.IGNORECASE,
)

# The default hierarchy template (Kotlin 1.9.20+): who each platform group sits under.
_COMMON = "common"
_TEMPLATE_GROUP_PARENT = {
    "jvm": _COMMON,
    "js": _COMMON,
    "wasmJs": _COMMON,
    "wasmWasi": _COMMON,
    "android": _COMMON,
    "androidUnit": _COMMON,  # androidUnitTest, androidInstrumentedTest
    "androidInstrumented": _COMMON,
    "native": _COMMON,
    "apple": "native",
    "linux": "native",
    "mingw": "native",
    "androidNative": "native",
    "ios": "apple",
    "macos": "apple",
    "tvos": "apple",
    "watchos": "apple",
}
# ``iosArm64Main`` sits under ``iosMain``, ``linuxX64Main`` under ``linuxMain``, ...
_TEMPLATE_TARGET_RE = re.compile(r"^(ios|macos|tvos|watchos|linux|mingw|androidNative)[A-Z]\w*$")


def _split_source_set_name(name: str) -> tuple[str, str] | None:
    """``("jvm", "Main")`` for ``jvmMain``; None when *name* ends in neither ``Main`` nor ``Test``."""
    for suffix in ("Main", "Test"):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], suffix
    return None


def _template_stem_parent(stem: str) -> str | None:
    if stem in _TEMPLATE_GROUP_PARENT:
        return _TEMPLATE_GROUP_PARENT[stem]
    match = _TEMPLATE_TARGET_RE.match(stem)
    return match.group(1) if match else None


def _is_template_set(name: str) -> bool:
    """Is *name* a source set only a Kotlin Multiplatform module has?"""
    split = _split_source_set_name(name)
    if split is None:
        return False
    stem = split[0]
    # ``src/androidTest`` is an ordinary Android app's instrumented-test set.
    return not stem.startswith("android") and (
        stem == _COMMON or _template_stem_parent(stem) is not None
    )


def _template_deps(name: str) -> tuple[str, ...]:
    """The source sets the default hierarchy template makes *name* depend on.

    ``linuxMain`` -> ``nativeMain`` -> ``commonMain``; a test set mirrors its
    main set's tree and also sees that main set (``jvmTest`` -> ``jvmMain``,
    ``commonTest`` -> ``commonMain``).
    """
    split = _split_source_set_name(name)
    if split is None:
        return ()
    stem, suffix = split
    deps: list[str] = []
    parent = _template_stem_parent(stem)
    if parent is not None:
        deps.append(parent + suffix)
    if suffix == "Test":
        main = "android" if stem in ("androidUnit", "androidInstrumented") else stem
        deps.append(main + "Main")
    return tuple(deps)


def _plausible_source_set(name: str, known: set[str]) -> bool:
    return name in known or _split_source_set_name(name) is not None


def _block_header(text: str, pos: int) -> str | None:
    """The statement before the ``{`` that encloses *pos*, or None at top level."""
    depth = 0
    for i in range(pos - 1, -1, -1):
        char = text[i]
        if char == "}":
            depth += 1
        elif char == "{":
            if depth == 0:
                return re.split(r"[\n;{}]", text[max(0, i - 200) : i])[-1]
            depth -= 1
    return None


def _parse_source_set_edges(script: str, known: set[str]) -> dict[str, list[str]]:
    """``{source set: the sets it explicitly depends on}`` from a build script's text.

    Reads ``x.dependsOn(y)`` and a bare ``dependsOn(y)`` inside the block that
    configures ``x`` (``val x by getting { .. }``, ``getByName("x") { .. }``,
    ``x { .. }``), plus custom-named targets such as ``jvm("desktop")``. A
    task's ``dependsOn`` is not an edge: both ends have to look like source sets.
    """
    edges: dict[str, list[str]] = {}

    def add(source: str, target: str) -> None:
        if (
            source != target
            and _plausible_source_set(source, known)
            and _plausible_source_set(target, known)
            and target not in edges.setdefault(source, [])
        ):
            edges[source].append(target)

    for match in _DEPENDS_ON_RE.finditer(script):
        arg = _DEPENDS_ON_ARG_RE.match(script, match.end())
        if arg is None:
            continue
        target = arg.group(1) or arg.group(2) or arg.group(3)
        before = script[: match.start()].rstrip()
        if before.endswith("."):
            receiver = _RECEIVER_RE.search(before[-160:])
            if receiver is not None:
                add(receiver.group(1) or receiver.group(2) or receiver.group(3), target)
            continue
        header = _block_header(script, match.start())
        if header is None or re.search(r"\btasks\b", header):
            continue
        for pattern in _BLOCK_HEADERS:
            named = pattern.search(header)
            if named is not None:
                add(named.group(1), target)
                break

    for match in _TARGET_RE.finditer(script):
        add(match.group(1) + "Main", "commonMain")
        add(match.group(1) + "Test", "commonTest")
    return edges


@dataclass(frozen=True)
class SourceSetModule:
    """One Kotlin Multiplatform module's source sets and what each one depends on."""

    root: str
    # Direct edges, default hierarchy template and build-script ``dependsOn`` merged.
    depends_on: dict[str, tuple[str, ...]] = field(default_factory=dict)
    _reach: dict[str, dict[str, int]] = field(default_factory=dict, repr=False, compare=False)

    def visible_from(self, name: str) -> dict[str, int]:
        """The source sets *name* sees, each with how many ``dependsOn`` steps away (itself 0)."""
        cached = self._reach.get(name)
        if cached is not None:
            return cached
        distance = {name: 0}
        queue = [name]
        for current in queue:
            for dep in self.depends_on.get(current) or _template_deps(current):
                if dep not in distance:
                    distance[dep] = distance[current] + 1
                    queue.append(dep)
        self._reach[name] = distance
        return distance


@dataclass
class SourceSetGraph:
    """The source-set dependencies of every Kotlin Multiplatform module, by module directory."""

    modules: dict[str, SourceSetModule] = field(default_factory=dict)

    def scope(self, importer: str, candidate: str) -> tuple[SourceSetModule, str, str] | None:
        """``(module, importer's set, candidate's set)`` when both sit in one multiplatform module."""
        located = _locate_source_set(importer)
        other = _locate_source_set(candidate)
        if located is None or other is None or located[0] != other[0]:
            return None
        module = self.modules.get(located[0])
        return None if module is None else (module, located[1], other[1])


def _locate_source_set(path: str) -> tuple[str, str] | None:
    """``("lib", "jvmMain")`` for ``lib/src/jvmMain/kotlin/x.kt``."""
    match = _SOURCE_SET_DIR_RE.search(path)
    if match is None:
        return None
    return path[: match.start()], match.group(0).strip("/").split("/")[1]


def _read_build_script(module_dir: Path) -> str:
    """The comment-free text of *module_dir*'s ``build.gradle(.kts)``, or ``""``."""
    texts: list[str] = []
    for build_name in ("build.gradle.kts", "build.gradle"):
        try:
            text = (module_dir / build_name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = _GRADLE_BLOCK_COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
        texts.append(_GRADLE_LINE_COMMENT_RE.sub("", text))
    return "\n".join(texts)


def build_source_set_graph(paths: Iterable[str], repo_path: Path | None) -> SourceSetGraph:
    """Each Kotlin Multiplatform module's source-set dependencies.

    Modules and their source sets come from the file *paths* (``lib/src/jvmMain/kotlin/..``),
    so a module no ``settings.gradle`` lists is still covered. A module is
    multiplatform when it has a ``commonMain``-style set or applies the
    multiplatform plugin; any other module (``src/main`` over ``src/test``) is
    left out, and its files rank by ``declaration_rank``'s plain rule.

    The edges are the default hierarchy template, plus ``dependsOn`` read from
    the module's build script. A script the reader cannot follow (a helper
    function that creates the sets) adds nothing, so it falls back to the
    template, never to a wider guess.
    """
    names_by_module: dict[str, set[str]] = {}
    for path in paths:
        located = _locate_source_set(path)
        if located is not None:
            names_by_module.setdefault(located[0], set()).add(located[1])

    graph = SourceSetGraph()
    for root, names in names_by_module.items():
        script = _read_build_script(repo_path / root) if repo_path is not None else ""
        if not (
            any(_is_template_set(name) for name in names)
            or _MULTIPLATFORM_PLUGIN_RE.search(script)
        ):
            continue
        explicit = _parse_source_set_edges(script, names)
        depends_on: dict[str, tuple[str, ...]] = {}
        for name in sorted(names | set(explicit) | {d for deps in explicit.values() for d in deps}):
            deps = list(_template_deps(name))
            deps.extend(dep for dep in explicit.get(name, ()) if dep not in deps)
            depends_on[name] = tuple(deps)
        graph.modules[root] = SourceSetModule(root=root, depends_on=depends_on)
    return graph


def get_or_build_source_set_graph(ctx: ResolverContext) -> SourceSetGraph:
    """Return the cached :class:`SourceSetGraph`, building it on first access."""
    cached = getattr(ctx, "_source_set_graph", None)
    if cached is not None:
        return cached
    graph = build_source_set_graph(ctx.path_set, ctx.repo_path)
    ctx._source_set_graph = graph  # type: ignore[attr-defined]
    return graph


def declaration_rank(
    importer: str, candidate: str, is_expect: bool, graph: SourceSetGraph | None = None
) -> int | None:
    """How directly *importer* sees *candidate*, one of a name's declarations; lowest wins.

    Kotlin Multiplatform declares a name once per source set: ``expect`` in
    common code and ``actual`` in each platform's. A file binds its own source
    set's declaration (0), else the nearest set its own depends on: a source
    set's ``actual`` outranks the ``expect`` further up (``jvmTest`` -> ``jvmMain``
    -> ``commonMain``; ``linuxMain`` -> ``nativeMain``). With *graph*, a
    declaration in a set of the same module that the importer's set cannot see
    (``appleMain`` from ``linuxMain``) is no candidate: None. Ties keep their
    order. Without a graph entry for the module (a plain Gradle project, or a
    candidate in another module) only a declaration in the file's own source
    set (``src/main`` over ``src/test``) moves ahead; the ``expect`` is next (1),
    any other declaration last (2).
    """
    own = source_set_dir(importer)
    if own is not None and source_set_dir(candidate) == own:
        return 0
    scope = graph.scope(importer, candidate) if graph is not None else None
    if scope is None:
        return 1 if is_expect else 2
    module, importer_set, candidate_set = scope
    steps = module.visible_from(importer_set).get(candidate_set)
    if steps is None:
        return None
    return 2 * max(steps, 1) - 1 + int(is_expect)


def _is_test_source_set(name: str) -> bool:
    lower = name.lower()
    return any(marker in lower for marker in _TEST_SOURCESET_MARKERS)


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
        clean_text = _GRADLE_BLOCK_COMMENT_RE.sub(
            lambda m: "\n" * m.group(0).count("\n"), text
        )
        clean_text = _GRADLE_LINE_COMMENT_RE.sub("", clean_text)
        for pat in (_PLUGIN_ID_RE, _PLUGIN_ID_GROOVY_RE, _APPLY_PLUGIN_RE, _APPLY_PLUGIN_KTS_RE):
            for m in pat.finditer(clean_text):
                plugins.append(m.group(1))
        for m in _ALIAS_PLUGIN_RE.finditer(clean_text):
            raw_alias = m.group(1).strip()
            if "plugins." in raw_alias:
                plugin_sub = raw_alias.split("plugins.", 1)[1].strip()
                plugins.append(plugin_sub)
                plugins.append(plugin_sub.replace(".", "-"))
        for blk in _PLUGINS_BLOCK_RE.finditer(clean_text):
            block_content = blk.group(1)
            for m in _KTS_BACKTICK_PLUGIN_RE.finditer(block_content):
                plugins.append(m.group(1))
            for m in _KTS_BARE_CORE_PLUGIN_RE.finditer(block_content):
                plugins.append(m.group(1))
    return frozenset(plugins)


def is_publishing_gradle_project(project: JvmGradleProject) -> bool:
    """Whether a Gradle module publishes an artifact to consumers.

    Published modules apply ``java-library``, ``maven-publish``, or a convention
    plugin matching ``java-library.*``, and do not apply ``application``.
    """
    if "application" in project.plugins:
        return False
    return any(
        p in ("java-library", "maven-publish") or p.startswith("java-library.")
        for p in project.plugins
    )


# ---------------------------------------------------------------------------
# Package extraction (cached per file)
# ---------------------------------------------------------------------------

def _extract_text_info(
    abs_path: str, text: str | None
) -> tuple[str, tuple[str, ...], bool, bool]:
    """Return (package_decl, top_level_types, is_module_info, is_package_info).

    Cheap line scan, no AST. *text* of None means the file was unreadable.
    """
    name = Path(abs_path).name
    is_module_info = name == "module-info.java"
    is_package_info = name == "package-info.java"
    if text is None:
        return "", (), is_module_info, is_package_info

    package = ""
    pkg_match = _PACKAGE_RE.search(text)
    if pkg_match:
        package = pkg_match.group(1)

    top_level: list[str] = []
    type_decl_re = re.compile(
        r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+|abstract\s+|final\s+|sealed\s+|open\s+|data\s+)*"
        r"(?:class|interface|enum|object|record|annotation)\s+(\w+)",
        re.MULTILINE,
    )
    for m in type_decl_re.finditer(text):
        top_level.append(m.group(1))

    return package, tuple(top_level), is_module_info, is_package_info


@lru_cache(maxsize=8192)
def _extract_file_info(abs_path: str) -> tuple[str, tuple[str, ...], bool, bool]:
    """:func:`_extract_text_info` for a file the source map did not have."""
    try:
        text: str | None = Path(abs_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = None
    return _extract_text_info(abs_path, text)


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

def build_jvm_gradle_index(
    repo_path: Path | None,
    *,
    prune_nested_git: bool = True,
    snapshot: Any | None = None,
    source_map: dict[str, bytes] | None = None,
) -> JvmGradleIndex:
    """Walk Gradle config + JVM sources to build the package-resolution index."""
    index = JvmGradleIndex()
    if repo_path is None or not repo_path.is_dir():
        return index

    settings_paths = [repo_path / "settings.gradle.kts", repo_path / "settings.gradle"]
    modules: list[str] = []
    project_dir_overrides: dict[str, str] = {}
    for sp in settings_paths:
        mods, ovr = parse_settings(sp)
        modules.extend(mods)
        project_dir_overrides.update(ovr)

    if not modules and (
        (repo_path / "build.gradle").is_file() or (repo_path / "build.gradle.kts").is_file()
    ):
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
            root_dir=Path(os.path.relpath(mod_dir, repo_path)).as_posix() if module_name else "",
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

                for jvm_file in glob_via(
                    snapshot, root_path, "*", prune_nested_git=prune_nested_git
                ):
                    if jvm_file.suffix not in _JVM_EXTENSIONS or not jvm_file.is_file():
                        continue
                    try:
                        rel_file = jvm_file.relative_to(resolved_repo).as_posix()
                    except ValueError:
                        continue
                    abs_file = str(jvm_file.resolve())
                    data = source_map.get(rel_file) if source_map is not None else None
                    if data is not None:
                        package, _types, _is_mod, _is_pkg = _extract_text_info(
                            abs_file, decode_source(data)
                        )
                    else:
                        package, _types, _is_mod, _is_pkg = _extract_file_info(abs_file)
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


def build_script_class_names(ctx: ResolverContext) -> set[str]:
    """Class names quoted in any Gradle build script, fully qualified or bare.

    A build names a class it loads by reflection: a plugin's
    ``esplugin { classname '...' }``, ``gradlePlugin`` ``implementationClass``,
    an application ``mainClass``. Every build script in the tree is read,
    since a settings file that adds projects by walking directories names
    none of them. A name only counts once it resolves to a class in the repo.
    A name without a package is the default package's class.
    """
    names: set[str] = set()
    if ctx.repo_path is None:
        return names
    for script in glob_via(
        getattr(ctx, "walk_snapshot", None),
        ctx.repo_path,
        ("build.gradle", "build.gradle.kts"),
        prune_nested_git=ctx.prune_nested_git,
    ):
        try:
            text = script.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        names.update(_QUOTED_FQN_RE.findall(text))
        names.update(_KEYED_BARE_CLASS_RE.findall(text))
    return names


def get_or_build_jvm_gradle_index(ctx: ResolverContext) -> JvmGradleIndex:
    """Return the cached JvmGradleIndex, building it on first access."""
    cached = getattr(ctx, "_jvm_gradle_index", None)
    if cached is not None:
        return cached
    index = build_jvm_gradle_index(
        ctx.repo_path,
        prune_nested_git=ctx.prune_nested_git,
        snapshot=getattr(ctx, "walk_snapshot", None),
        source_map=getattr(ctx, "source_map", None),
    )
    ctx._jvm_gradle_index = index  # type: ignore[attr-defined]
    return index


def resolve_via_jvm_gradle_index(module_path: str, ctx: ResolverContext) -> str | None:
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


def get_or_build_kotlin_index(ctx: ResolverContext) -> KotlinProjectIndex:
    """Back-compat: return a KotlinProjectIndex wrapping the JVM Gradle index."""
    jvm_index = get_or_build_jvm_gradle_index(ctx)
    return KotlinProjectIndex(_inner=jvm_index)


def resolve_via_kotlin_index(module_path: str, ctx: ResolverContext) -> str | None:
    """Back-compat: resolve via the JVM Gradle index."""
    return resolve_via_jvm_gradle_index(module_path, ctx)
