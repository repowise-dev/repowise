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

if TYPE_CHECKING:
    from .context import ResolverContext

log = structlog.get_logger(__name__)

_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)
_TOP_LEVEL_TYPE_RE = re.compile(
    r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+|abstract\s+|final\s+|sealed\s+|open\s+|data\s+)*"
    r"(?:class|interface|enum|object|record|annotation)\s+(\w+)",
    re.MULTILINE,
)
_JVM_EXTENSIONS = frozenset({".java", ".kt"})

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
    pkg_match = _PACKAGE_RE.search(text)
    if pkg_match:
        package = pkg_match.group(1)

    types: list[str] = []
    for m in _TOP_LEVEL_TYPE_RE.finditer(text):
        types.append(m.group(1))

    return package, tuple(types)


def _scan_meta_inf_services(repo_path: Path) -> dict[str, tuple[str, ...]]:
    """Scan META-INF/services/ directories for SPI declarations."""
    services: dict[str, list[str]] = {}
    for services_dir in repo_path.rglob("META-INF/services"):
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


def _scan_spring_autoconfig(repo_path: Path) -> dict[str, tuple[str, ...]]:
    """Scan spring.factories and Boot 3 AutoConfiguration.imports."""
    result: dict[str, list[str]] = {}

    # spring.factories (Boot 2 style)
    for factories in repo_path.rglob("META-INF/spring.factories"):
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
    for imports_file in repo_path.rglob("META-INF/spring/*.imports"):
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

    for path in ctx.path_set:
        if not (path.endswith(".java") or path.endswith(".kt")):
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

    # Scan META-INF resources (cheap glob, O(matching files))
    try:
        index.services = _scan_meta_inf_services(repo_path)
    except Exception:
        pass
    try:
        index.autoconfig_imports = _scan_spring_autoconfig(repo_path)
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
