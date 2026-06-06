"""JVM workspace index — unified package model for Java + Kotlin.

Groups ``.java`` and ``.kt`` files by their JVM package declaration,
building a single lookup surface consumed by both the Java and Kotlin
import resolvers, the call resolver (same-package implicit access), and
the dead-code analyzer (package-aware reachability).

The index is the JVM analogue of :class:`GoPackageIndex` (Go),
:class:`DotNetProjectIndex` (C#), and :class:`CargoWorkspaceIndex`
(Rust). Built once per resolver run via :func:`get_or_build_jvm_index`
and cached on the context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import structlog

from repowise.core.fs_walk import iter_glob

if TYPE_CHECKING:
    from .context import ResolverContext

log = structlog.get_logger(__name__)

_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)
# Compound kinds (``enum class`` / ``annotation class`` — Kotlin) must come
# before their single-keyword prefixes in the alternation, otherwise the
# bare ``enum`` / ``annotation`` branch matches first and captures the
# literal word "class" as the type name. ``trait`` and the ``case`` /
# ``implicit`` modifiers cover Scala's type declarations.
_TOP_LEVEL_TYPE_RE = re.compile(
    r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+|abstract\s+|final\s+"
    r"|sealed\s+|open\s+|data\s+|value\s+|inline\s+|case\s+|implicit\s+)*"
    r"(?:annotation\s+class|enum\s+class|class|interface|trait|enum|object|record|annotation)\s+(\w+)",
    re.MULTILINE,
)
_JVM_EXTENSIONS = frozenset({".java", ".kt", ".scala"})

# Chained Scala package clauses (``package org.example`` then ``package
# tools`` → org.example.tools) are scanned line-wise from the top of the
# file; ``package object foo`` is a definition, not a clause.
_SCALA_PACKAGE_LINE_RE = re.compile(r"package\s+(?!object\b)([\w.]+)")

# JDK / Kotlin-stdlib namespaces never resolve to repo files and produce
# no node at all. Each prefix carries its trailing dot so matching stays
# segment-aware (``javautil.Foo`` must not match ``java.``).
_JAVA_STDLIB_PREFIXES = ("java.", "javax.", "jdk.")
_KOTLIN_STDLIB_PREFIXES = ("kotlin.",)

# Well-known external library namespaces: a real dependency (an external
# node is wanted), but short-circuited before the stem/directory fallbacks
# so e.g. ``jakarta.servlet.Filter`` can never false-match a repo-local
# ``Filter.java``. Unlike stdlib these only apply AFTER exact workspace
# lookup fails — a repo can legitimately contain these namespaces (the
# library's own source tree).
_JAVA_EXTERNAL_PREFIXES = ("jakarta.",)
_KOTLIN_EXTERNAL_PREFIXES = ("kotlinx.",)


def classify_jvm_import(module_path: str, *, kotlin: bool = False) -> str | None:
    """Classify an import path as ``"stdlib"`` (drop — no node),
    ``"external"`` (external node, skip fuzzy local fallbacks), or
    ``None`` (may be repo-local)."""
    if module_path.startswith(_JAVA_STDLIB_PREFIXES):
        return "stdlib"
    if kotlin and module_path.startswith(_KOTLIN_STDLIB_PREFIXES):
        return "stdlib"
    if module_path.startswith(_JAVA_EXTERNAL_PREFIXES):
        return "external"
    if kotlin and module_path.startswith(_KOTLIN_EXTERNAL_PREFIXES):
        return "external"
    return None

# Standard-library packages that should never produce import edges
_JAVA_LANG_PACKAGES = frozenset({
    "java.lang",
    "java.lang.annotation",
    "java.lang.invoke",
    "java.lang.reflect",
})

# Types automatically imported via java.lang.*
_JAVA_LANG_TYPES = frozenset({
    "String", "Object", "Class", "System", "Math",
    "Integer", "Long", "Double", "Float", "Boolean", "Character", "Byte", "Short",
    "Number", "Void",
    "Thread", "Runnable", "Process", "ProcessBuilder", "Runtime",
    "Throwable", "Exception", "RuntimeException", "Error",
    "IllegalArgumentException", "IllegalStateException", "NullPointerException",
    "UnsupportedOperationException", "IndexOutOfBoundsException",
    "ClassCastException", "ArithmeticException", "SecurityException",
    "ClassNotFoundException", "InterruptedException", "CloneNotSupportedException",
    "StringBuilder", "StringBuffer", "StringIndexOutOfBoundsException",
    "Enum", "Record", "Comparable", "Iterable", "AutoCloseable", "Cloneable",
    "Override", "Deprecated", "SuppressWarnings", "FunctionalInterface", "SafeVarargs",
})


@dataclass(frozen=True)
class JvmPackage:
    """A single JVM package — a directory of ``.java`` + ``.kt`` sibling files."""

    fqn: str
    files: tuple[str, ...]
    exported_top_level: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass
class JvmWorkspaceIndex:
    """Repo-scoped view of every local JVM package."""

    packages: dict[str, JvmPackage] = field(default_factory=dict)
    """Keyed by fully-qualified package name."""

    file_to_package: dict[str, str] = field(default_factory=dict)
    """Maps repo-relative file path → package FQN."""

    fqn_to_files: dict[str, list[str]] = field(default_factory=dict)
    """Maps a fully-qualified class name → defining file(s)."""

    services: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """META-INF/services/<Iface> → impl FQNs (for Phase 3/4 reachability)."""

    autoconfig_imports: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Spring Boot autoconfig import files → listed FQNs (for Phase 3/4)."""

    def files_for_package(self, fqn: str) -> tuple[str, ...]:
        """Return all files in the package."""
        pkg = self.packages.get(fqn)
        return pkg.files if pkg else ()

    def files_for_fqn(self, fqn: str) -> tuple[str, ...]:
        """Resolve a fully-qualified name to its defining file(s)."""
        direct = self.fqn_to_files.get(fqn)
        if direct:
            return tuple(direct)

        # Fall back: split into package + type name, search package files
        if "." not in fqn:
            return ()
        package, type_name = fqn.rsplit(".", 1)
        pkg = self.packages.get(package)
        if pkg is None:
            return ()
        files = pkg.exported_top_level.get(type_name)
        return files if files else ()

    def files_for_member_fqn(self, fqn: str) -> tuple[str, ...]:
        """Resolve an import whose tail is a *member*, not a type.

        Kotlin member imports (``import okio.ByteString.Companion.encodeUtf8``)
        and Java static-member imports (``import static com.foo.Bar.CONSTANT``)
        name a function/constant inside a type. Strip trailing segments until
        the prefix resolves as a type FQN — ``…ByteString.Companion.encodeUtf8``
        → ``…ByteString.Companion`` → ``…ByteString`` (hit). At least
        ``package.Type`` (two segments) must remain.
        """
        parts = fqn.split(".")
        while len(parts) > 2:
            parts = parts[:-1]
            files = self.files_for_fqn(".".join(parts))
            if files:
                return files
        return ()

    def wildcard_expand(self, pkg_fqn: str) -> tuple[str, ...]:
        """Expand ``import pkg.*`` → all files in the package."""
        return self.files_for_package(pkg_fqn)

    def static_wildcard_expand(self, type_fqn: str) -> tuple[str, ...]:
        """Expand ``import static pkg.Type.*`` → file(s) defining Type."""
        return self.files_for_fqn(type_fqn)

    def package_for_file(self, file_path: str) -> str | None:
        """Return the package FQN for a file, or None."""
        return self.file_to_package.get(file_path)

    def same_package_files(self, file_path: str) -> tuple[str, ...]:
        """Return all sibling files in the same package (excluding the file itself)."""
        pkg_fqn = self.file_to_package.get(file_path)
        if not pkg_fqn:
            return ()
        pkg = self.packages.get(pkg_fqn)
        if not pkg:
            return ()
        return tuple(f for f in pkg.files if f != file_path)

    def is_java_lang(self, import_path: str) -> bool:
        """Return True if the import is a java.lang.* builtin."""
        if import_path.startswith("java.lang."):
            remainder = import_path[len("java.lang."):]
            if "." not in remainder:
                return True
            # java.lang.annotation.*, java.lang.reflect.*, etc.
            pkg = import_path.rsplit(".", 1)[0]
            return pkg in _JAVA_LANG_PACKAGES
        # Unqualified types in java.lang
        parts = import_path.split(".")
        if len(parts) == 1 and parts[0] in _JAVA_LANG_TYPES:
            return True
        return False


@lru_cache(maxsize=16384)
def _scan_jvm_file(abs_path: str) -> tuple[str, tuple[str, ...]]:
    """Return (package_fqn, top_level_type_names) for a JVM source file.

    Reads the file once and caches the result. Cheap line scan — no AST.
    """
    try:
        text = Path(abs_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "", ()

    package = ""
    if abs_path.endswith(".scala"):
        package = _scala_package(text)
    else:
        pkg_match = _PACKAGE_RE.search(text)
        if pkg_match:
            package = pkg_match.group(1)

    types: list[str] = []
    for m in _TOP_LEVEL_TYPE_RE.finditer(text):
        types.append(m.group(1))

    return package, tuple(types)


def _scala_package(text: str) -> str:
    """Join chained Scala package clauses from the top of the file.

    ``package org.example`` followed by ``package tools`` nests —
    the file's package is ``org.example.tools``. Scanning stops at the
    first non-package code line so a ``package`` token later in the file
    (strings, comments mid-file) cannot append junk.
    """
    pkgs: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "/*", "*")):
            continue
        m = _SCALA_PACKAGE_LINE_RE.match(stripped)
        if m:
            pkgs.append(m.group(1))
            continue
        break
    return ".".join(pkgs)


_JPMS_PROVIDES_RE = re.compile(
    r"provides\s+([\w.]+)\s+with\s+([\w.,\s]+);",
    re.MULTILINE,
)


def _scan_jpms_provides(
    repo_path: Path, *, prune_nested_git: bool = True
) -> dict[str, tuple[str, ...]]:
    """Scan ``module-info.java`` files for ``provides X with Y, Z`` directives.

    Returns a mapping iface_fqn → impl_fqns identical in shape to
    ``_scan_meta_inf_services``, so the two sources merge cleanly. Cheap
    regex scan — the module-info syntax is restrictive enough that a
    real parser is unnecessary, and avoiding the AST round-trip keeps
    the warmup fast.
    """
    out: dict[str, list[str]] = {}
    for mi in iter_glob(repo_path, "module-info.java", prune_nested_git=prune_nested_git):
        if not mi.is_file():
            continue
        try:
            text = mi.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _JPMS_PROVIDES_RE.finditer(text):
            iface = m.group(1).strip()
            impls = [s.strip() for s in m.group(2).split(",") if s.strip()]
            if iface and impls:
                out.setdefault(iface, []).extend(impls)
    return {k: tuple(v) for k, v in out.items()}


def _scan_meta_inf_services(
    repo_path: Path, *, prune_nested_git: bool = True
) -> dict[str, tuple[str, ...]]:
    """Scan META-INF/services/ directories for SPI declarations."""
    services: dict[str, list[str]] = {}
    for services_dir in iter_glob(
        repo_path, "META-INF/services", prune_nested_git=prune_nested_git
    ):
        if not services_dir.is_dir():
            continue
        try:
            for entry in services_dir.iterdir():
                if not entry.is_file():
                    continue
                iface_fqn = entry.name
                try:
                    text = entry.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                impls: list[str] = []
                for line in text.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        # Remove inline comments
                        line = line.split("#")[0].strip()
                        if line:
                            impls.append(line)
                if impls:
                    services.setdefault(iface_fqn, []).extend(impls)
        except OSError:
            continue
    return {k: tuple(v) for k, v in services.items()}


def _scan_spring_autoconfig(
    repo_path: Path, *, prune_nested_git: bool = True
) -> dict[str, tuple[str, ...]]:
    """Scan spring.factories and Boot 3 AutoConfiguration.imports."""
    result: dict[str, list[str]] = {}

    # spring.factories (Boot 2 style)
    for factories in iter_glob(
        repo_path, "META-INF/spring.factories", prune_nested_git=prune_nested_git
    ):
        if not factories.is_file():
            continue
        try:
            text = factories.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        current_key = ""
        current_values: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line and not line.startswith("\\"):
                if current_key and current_values:
                    rel = str(factories.relative_to(repo_path).as_posix())
                    result.setdefault(rel, []).extend(current_values)
                key, _, val = line.partition("=")
                current_key = key.strip()
                val = val.strip().rstrip("\\").strip()
                current_values = [v.strip() for v in val.split(",") if v.strip()]
            elif line.startswith("\\") or (current_key and line):
                val = line.lstrip("\\").strip()
                current_values.extend(v.strip() for v in val.split(",") if v.strip())
        if current_key and current_values:
            rel = str(factories.relative_to(repo_path).as_posix())
            result.setdefault(rel, []).extend(current_values)

    # Boot 3 style: META-INF/spring/*.imports
    for imports_file in iter_glob(
        repo_path, "META-INF/spring/*.imports", prune_nested_git=prune_nested_git
    ):
        if not imports_file.is_file():
            continue
        try:
            text = imports_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fqns: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                fqns.append(line)
        if fqns:
            rel = str(imports_file.relative_to(repo_path).as_posix())
            result.setdefault(rel, []).extend(fqns)

    return {k: tuple(v) for k, v in result.items()}


def build_jvm_workspace_index(ctx: "ResolverContext") -> JvmWorkspaceIndex:
    """Build the JVM workspace index from all ``.java`` and ``.kt`` files in the path set.

    One walk over the path set; each file read at most once (via
    ``_scan_jvm_file`` LRU cache). The index groups files by package
    and builds FQN → file mappings.
    """
    index = JvmWorkspaceIndex()

    if ctx.repo_path is None:
        return index

    repo_path = ctx.repo_path.resolve()

    # Group files by package
    pkg_files: dict[str, list[str]] = {}
    pkg_types: dict[str, dict[str, list[str]]] = {}

    for path in ctx.sorted_paths:
        if not path.endswith((".java", ".kt", ".scala")):
            continue

        abs_path = str((repo_path / path).resolve())
        package, top_types = _scan_jvm_file(abs_path)
        if not package:
            continue

        pkg_files.setdefault(package, []).append(path)
        index.file_to_package[path] = package

        type_map = pkg_types.setdefault(package, {})
        for type_name in top_types:
            type_map.setdefault(type_name, []).append(path)
            fqn = f"{package}.{type_name}"
            index.fqn_to_files.setdefault(fqn, []).append(path)

    # Build JvmPackage objects
    for pkg_fqn, files in pkg_files.items():
        files.sort()
        exported = {
            name: tuple(file_list)
            for name, file_list in pkg_types.get(pkg_fqn, {}).items()
        }
        index.packages[pkg_fqn] = JvmPackage(
            fqn=pkg_fqn,
            files=tuple(files),
            exported_top_level=exported,
        )

    # Scan META-INF resources + JPMS module-info.java provides directives
    # (cheap glob, O(matching files)). Both populate ``services``; later
    # phases treat any FQN listed there as reachable.
    try:
        services = _scan_meta_inf_services(repo_path, prune_nested_git=ctx.prune_nested_git)
        jpms = _scan_jpms_provides(repo_path, prune_nested_git=ctx.prune_nested_git)
        merged: dict[str, list[str]] = {k: list(v) for k, v in services.items()}
        for iface, impls in jpms.items():
            merged.setdefault(iface, []).extend(impls)
        index.services = {k: tuple(v) for k, v in merged.items()}
    except Exception:
        pass
    try:
        index.autoconfig_imports = _scan_spring_autoconfig(
            repo_path, prune_nested_git=ctx.prune_nested_git
        )
    except Exception:
        pass

    _scan_jvm_file.cache_clear()

    log.debug(
        "Built JVM workspace index",
        packages=len(index.packages),
        fqns=len(index.fqn_to_files),
        files=len(index.file_to_package),
        services=len(index.services),
        autoconfig=len(index.autoconfig_imports),
    )
    return index


_INDEX_KEY = "_jvm_workspace_index"


def get_or_build_jvm_index(ctx: "ResolverContext") -> JvmWorkspaceIndex:
    """Return the cached JvmWorkspaceIndex, building it on first access."""
    cached = getattr(ctx, _INDEX_KEY, None)
    if cached is not None:
        return cached
    index = build_jvm_workspace_index(ctx)
    setattr(ctx, _INDEX_KEY, index)
    return index
