"""Where a JVM build runner compiles and runs tests, read from its build files.

Classes residing in a main source set (``src/main/<lang>/``, or
``src/<target>Main/<lang>/`` in Gradle Kotlin Multiplatform) that carry test-like
names (such as ScalaTest's ``AsyncTestSuite.scala``, Kotest's
``DynamicRootTest.kt``, or Javalin's ``JavalinTest.kt``) are production APIs or
testing utilities, never tests executed by test runners. :mod:`.test_paths`
asks :meth:`JvmSourceSets.collects` before trusting a test-shaped JVM name outside
any test directory.

Pure: callers hand in build file text, so ingestion reads each build file once
in the walk it already makes and a server can call this with data it holds.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

JVM_BUILD_FILE_NAMES: tuple[str, ...] = (
    "build.gradle.kts",
    "build.gradle",
    "pom.xml",
    "build.sbt",
)
_PRECEDENCE = {name: rank for rank, name in enumerate(JVM_BUILD_FILE_NAMES)}

_JVM_EXTENSIONS = frozenset({".java", ".kt", ".scala"})

# Standard test source set names/tokens
_TEST_SOURCE_SETS = frozenset({"test", "tests", "it", "testfixtures"})


class JvmBuildUnreadableError(ValueError):
    """A JVM build file that does not parse or contains dynamic/unresolvable paths."""


@dataclass(frozen=True, slots=True)
class _SourceSetRule:
    main_dirs: tuple[str, ...] = ()
    test_dirs: tuple[str, ...] = ()
    is_custom: bool = False


@dataclass(frozen=True, slots=True)
class JvmSourceSets:
    """Source set rules for each directory holding a JVM build file."""

    by_dir: Mapping[str, _SourceSetRule] = field(default_factory=dict)
    # Directories holding a build file that failed to parse or has dynamic configuration
    unreadable: frozenset[str] = frozenset()

    def collects(self, path: str) -> bool | None:
        """Whether a JVM build collects *path* as a test; ``None`` when unreadable or no build file.

        - ``False``: Path is confirmed to be in a main source set (never a runnable test).
        - ``True``: Path is confirmed to be in a test source set.
        - ``None``: Path is outside recognized source sets, no build file governs it,
          or the nearest build file has dynamic/unparsable configuration (fail-closed).
        """
        p = PurePosixPath(path)
        for parent in (p, *p.parents):
            key = "" if str(parent) == "." else str(parent)
            if key in self.unreadable:
                return None
            if key in self.by_dir:
                rule = self.by_dir[key]
                rel = p if key == "" else p.relative_to(parent)
                return _source_set_verdict(rel, rule)
        return None


def _source_set_verdict(rel: PurePosixPath, rule: _SourceSetRule) -> bool | None:
    rel_str = str(rel)

    # 1. Custom configured directories take precedence
    if rule.test_dirs and any(rel_str == d or rel_str.startswith(f"{d}/") for d in rule.test_dirs):
        return True
    if rule.main_dirs and any(rel_str == d or rel_str.startswith(f"{d}/") for d in rule.main_dirs):
        return False

    # 2. Standard directory layout conventions
    parts = rel.parts
    # Find the nearest enclosing "src" directory
    for idx in range(len(parts) - 1, -1, -1):
        if parts[idx] == "src" and idx + 1 < len(parts):
            source_set = parts[idx + 1]
            source_set_lower = source_set.lower()

            # Test source set: test, tests, it, testFixtures, or ending with Test/Tests
            # (e.g. commonTest, jvmTest, androidTest, integrationTest)
            if not rule.test_dirs and (
                source_set_lower in _TEST_SOURCE_SETS
                or source_set.endswith(("Test", "Tests"))
            ):
                return True

            # Main source set: main or ending with Main
            # (e.g. commonMain, jvmMain, jsMain, androidMain)
            if not rule.main_dirs and (
                source_set_lower == "main"
                or (source_set.endswith("Main") and source_set != "Main")
            ):
                return False

    return None


def _parse_maven_pom(text: str) -> _SourceSetRule:
    """Extract <sourceDirectory> and <testSourceDirectory> from pom.xml."""
    try:
        root = ET.fromstring(text)
    except Exception as exc:
        raise JvmBuildUnreadableError(f"pom.xml XML parse error: {exc}") from exc

    main_dirs: list[str] = []
    test_dirs: list[str] = []

    for elem in root.iter():
        tag = elem.tag.split("}", 1)[1] if "}" in elem.tag else elem.tag
        if tag == "sourceDirectory" and elem.text:
            val = elem.text.strip()
            if "${" in val:
                raise JvmBuildUnreadableError(f"Dynamic expression in <sourceDirectory>: {val}")
            main_dirs.append(val.strip("/").removeprefix("./"))
        elif tag == "testSourceDirectory" and elem.text:
            val = elem.text.strip()
            if "${" in val:
                raise JvmBuildUnreadableError(f"Dynamic expression in <testSourceDirectory>: {val}")
            test_dirs.append(val.strip("/").removeprefix("./"))

    is_custom = bool(main_dirs or test_dirs)
    return _SourceSetRule(
        main_dirs=tuple(main_dirs),
        test_dirs=tuple(test_dirs),
        is_custom=is_custom,
    )


def _parse_gradle_build(text: str) -> _SourceSetRule:
    """Extract custom source directories from build.gradle / build.gradle.kts."""
    if "sourceSets" not in text:
        return _SourceSetRule(is_custom=False)

    dynamic_markers = (
        "buildDir",
        "layout.buildDirectory",
        "layout.projectDirectory",
        "extra[",
        "extra.",
        "project.property",
        "findProperty",
    )
    for marker in dynamic_markers:
        if marker in text:
            raise JvmBuildUnreadableError(f"Dynamic expression '{marker}' in Gradle build")

    if re.search(r'["\'][^"\']*\$[^"\']*["\']', text):
        raise JvmBuildUnreadableError("String interpolation in Gradle build")

    if "srcDir" not in text:
        return _SourceSetRule(is_custom=False)

    main_dirs: list[str] = []
    test_dirs: list[str] = []

    # Look for main block within sourceSets
    main_match = re.search(
        r"""(?:\bmain\b\s*\{|\bsourceSets\.main\b)(.*?)(?:\n\s*\}\s*|\n\s*(?:test|commonTest|jvmTest)\b|\Z)""",
        text,
        re.DOTALL,
    )
    if main_match:
        for block in re.finditer(r"""\bsrcDirs?\s*(?:=|\(|\+=)?\s*\[?([^\]\)\n]+)""", main_match.group(1)):
            for lit in re.findall(r"""['"]([^'"]+)['"]""", block.group(1)):
                main_dirs.append(lit.strip("/").removeprefix("./"))

    # Look for test block within sourceSets
    test_match = re.search(
        r"""(?:\btest\b\s*\{|\bsourceSets\.test\b)(.*?)(?:\n\s*\}\s*|\n\s*(?:main|commonMain|jvmMain)\b|\Z)""",
        text,
        re.DOTALL,
    )
    if test_match:
        for block in re.finditer(r"""\bsrcDirs?\s*(?:=|\(|\+=)?\s*\[?([^\]\)\n]+)""", test_match.group(1)):
            for lit in re.findall(r"""['"]([^'"]+)['"]""", block.group(1)):
                test_dirs.append(lit.strip("/").removeprefix("./"))

    if not main_dirs and not test_dirs:
        raise JvmBuildUnreadableError("Unrecognized sourceSets configuration in Gradle build")

    return _SourceSetRule(
        main_dirs=tuple(main_dirs),
        test_dirs=tuple(test_dirs),
        is_custom=True,
    )


def _parse_sbt_build(text: str) -> _SourceSetRule:
    """Extract custom source directories from build.sbt, or standard conventions."""
    if "unmanagedSourceDirectories" in text:
        if any(marker in text for marker in ("baseDirectory", "target", "crossTarget", "$")):
            raise JvmBuildUnreadableError("Dynamic expression in sbt unmanagedSourceDirectories")
        custom_dirs = re.findall(
            r"""unmanagedSourceDirectories\s*(?:\+=|\+\+=|:=)\s*.*['"]([^'"]+)['"]""",
            text,
        )
        if custom_dirs:
            main_dirs = [d.strip("/").removeprefix("./") for d in custom_dirs if "Test" not in d]
            test_dirs = [d.strip("/").removeprefix("./") for d in custom_dirs if "Test" in d]
            return _SourceSetRule(tuple(main_dirs), tuple(test_dirs), is_custom=True)
    return _SourceSetRule(is_custom=False)


def jvm_build_options(name: str, text: str) -> _SourceSetRule | None:
    """The source set rules of one build file, or None if not a recognized JVM build file.

    Raises JvmBuildUnreadableError if the file is a JVM build file but fails to parse.
    """
    if name == "pom.xml":
        return _parse_maven_pom(text)
    if name in ("build.gradle", "build.gradle.kts"):
        return _parse_gradle_build(text)
    if name == "build.sbt":
        return _parse_sbt_build(text)
    return None


def jvm_source_sets(
    configs: Iterable[tuple[str, _SourceSetRule]], unreadable: Iterable[str] = ()
) -> JvmSourceSets:
    """Build from (config path, rule) pairs.

    The highest-precedence build file in each directory wins.
    unreadable are the paths of build files that failed to parse.
    """
    chosen: dict[str, tuple[int, _SourceSetRule]] = {}
    for path, rule in configs:
        p = PurePosixPath(path)
        key = "" if str(p.parent) == "." else str(p.parent)
        rank = _PRECEDENCE.get(p.name, len(_PRECEDENCE))
        if key not in chosen or rank < chosen[key][0]:
            chosen[key] = (rank, rule)
    return JvmSourceSets(
        by_dir={key: rule for key, (_, rule) in chosen.items()},
        unreadable=frozenset(_dir_key(PurePosixPath(u)) for u in unreadable),
    )


def _dir_key(path: PurePosixPath) -> str:
    return "" if str(path.parent) == "." else str(path.parent)


def read_jvm_source_sets(texts: Iterable[tuple[str, str]]) -> JvmSourceSets:
    """Build JvmSourceSets from (config path, text) pairs."""
    rules: list[tuple[str, _SourceSetRule]] = []
    unreadable: list[str] = []
    for path, text in texts:
        p = PurePosixPath(path)
        if p.name in JVM_BUILD_FILE_NAMES:
            try:
                rule = jvm_build_options(p.name, text)
                if rule is not None:
                    rules.append((path, rule))
            except JvmBuildUnreadableError:
                unreadable.append(path)
    return jvm_source_sets(rules, unreadable)
