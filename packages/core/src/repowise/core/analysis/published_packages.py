"""Which project of a repository ships a package, read from its build files.

Two readers share this: the dead-code pass, which keeps an unused export of a
published package below the review floor, and the per-symbol ``public_api``
flag (:mod:`repowise.core.analysis.public_api`), which marks the .NET surface
on the graph from the same reader, so the two cannot disagree about what ships.

Only a build that declares the package counts, never a guess from layout. The
evidence is read per language from :data:`PUBLISHED_ROOTS`, each entry
returning ``{project dir: package name, or None when that project publishes
nothing}``. A file belongs to its innermost project, and only that project's
own build decides: a sample or test project nested in a packaged library's
folder is not part of the package. A reader may also map a directory or file
the ecosystem marks private (Go ``internal/``, a Python ``_module``) to None.

What counts as published, per ecosystem:

- .NET: a project that opts into packing (``IsPackable``, ``PackageId``,
  ``GeneratePackageOnBuild``), or keeps a public-API baseline
  (``PublicAPI.Shipped.txt``).
- Go: a module whose root package is importable, or that has no ``main``
  package at all; its non-``main`` packages outside ``internal/``.
- Rust: a crate with a library target (``[lib]`` or ``src/lib.rs``) and no
  ``publish = false``.
- Java/Kotlin: a Maven module whose pom, or an enclosing pom, deploys
  (``distributionManagement``, a release-signing or central-publishing plugin,
  an OSS release parent); a Gradle module that applies a publishing plugin.
  A war, a Spring Boot or an ``application`` module is an app.
- Python: a ``pyproject.toml`` with a ``[project]`` name and a
  ``[build-system]``, not marked unpackaged or private, and not a Django site.
- TypeScript/JavaScript: a ``package.json`` that is not ``private`` and
  declares a library surface (``exports``, ``module``, ``types``, ``typings``),
  or a ``main`` together with what it uploads (``files``, ``publishConfig``).
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import Any

from repowise.core.fsutils import repo_relative

#: The public-API analyzer's baseline file, beside the project or in a folder
#: of its own (``.PublicAPI/``).
API_BASELINE = "PublicAPI.Shipped.txt"


@dataclass(frozen=True)
class BuildFacts:
    """What the per-language readers may consult; a new language adds a field."""

    repo_root: Path | None
    dotnet_index: Any | None = None

    @cached_property
    def tree(self) -> Mapping[str, frozenset[str]]:
        """``{repo-relative dir: its file names}``, from one pruned walk."""
        # Deferred: the ingestion package imports analysis modules at load time.
        from repowise.core.fs_walk import walk_repo
        from repowise.core.ingestion.traverser import is_output_dir

        if self.repo_root is None:
            return {}
        root = Path(self.repo_root)
        out: dict[str, frozenset[str]] = {}
        for dirpath, _dirs, filenames in walk_repo(root, prune=is_output_dir):
            rel = repo_relative(dirpath, root)
            if rel is not None:
                out["" if rel == "." else rel] = frozenset(filenames)
        return out

    def read(self, rel_dir: str, name: str, limit: int = -1) -> str | None:
        """The text of *name* in *rel_dir* (its first *limit* chars), or None when unreadable."""
        if self.repo_root is None:
            return None
        try:
            with open(Path(self.repo_root) / rel_dir / name, encoding="utf-8", errors="replace") as f:
                return f.read(limit)
        except OSError:
            return None


def _relative(path: Path, root: Path) -> str | None:
    rel = repo_relative(path, root, resolve=True)
    if rel is None:
        return None
    return "" if rel == "." else rel


def keeps_api_baseline(project_dir: Path) -> bool:
    try:
        folders = [project_dir, *(p for p in project_dir.iterdir() if p.is_dir())]
        return any((folder / API_BASELINE).is_file() for folder in folders)
    except OSError:
        return False


def dotnet_published(facts: BuildFacts) -> dict[str, str | None]:
    """Every .NET project dir, mapped to the package it ships or None."""
    index = facts.dotnet_index
    if index is None:
        return {}
    root = Path(index.repo_path)
    out: dict[str, str | None] = {}
    for project in index.projects.values():
        rel = _relative(project.project_dir, root)
        if rel is None:
            continue
        name = project.published_id
        if name is None and keeps_api_baseline(project.project_dir):
            name = project.assembly_name or project.path.stem
        out[rel] = name
    return out


def _join(rel_dir: str, child: str) -> str:
    return f"{rel_dir}/{child}" if rel_dir else child


def _self_and_parents(path: str) -> Iterator[str]:
    """*path*, then each directory enclosing it, up to the root (``""``)."""
    pure = PurePosixPath(path)
    for part in (pure, *pure.parents):
        key = part.as_posix()
        yield "" if key == "." else key


def _innermost(path: str, keys: Mapping[str, Any]) -> str | None:
    return next((key for key in _self_and_parents(path) if key in keys), None)


def _dirs_with(facts: BuildFacts, name: str) -> list[str]:
    return sorted(d for d, names in facts.tree.items() if name in names)


def _toml(text: str | None) -> dict[str, Any]:
    try:
        data = tomllib.loads(text or "")
    except (tomllib.TOMLDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# --- Go ---------------------------------------------------------------------

_GO_MODULE_RE = re.compile(r"^module\s+(\S+)", re.MULTILINE)
_GO_PACKAGE_RE = re.compile(r"^package\s+(\w+)", re.MULTILINE)
#: The package clause follows at most a license header and build tags.
_GO_HEADER_CHARS = 8192


def _go_built(rel_dir: str) -> bool:
    """Whether the go tool builds *rel_dir* (it skips these by name)."""
    return not any(
        part.startswith(("_", ".")) or part in ("testdata", "vendor")
        for part in PurePosixPath(rel_dir).parts
    )


def _go_is_main(facts: BuildFacts, rel_dir: str, name: str) -> bool:
    match = _GO_PACKAGE_RE.search(facts.read(rel_dir, name, _GO_HEADER_CHARS) or "")
    return match is not None and match.group(1) == "main"


def _go_packages(facts: BuildFacts) -> dict[str, bool]:
    """``{package dir: is package main}``. A stray ``main`` file (a generator
    behind a build tag) does not make a library package a program."""
    out: dict[str, bool] = {}
    for rel_dir, names in facts.tree.items():
        sources = [n for n in names if n.endswith(".go") and not n.endswith("_test.go")]
        if sources and _go_built(rel_dir):
            out[rel_dir] = all(_go_is_main(facts, rel_dir, n) for n in sources)
    return out


def _go_unexported(module_dir: str, packages: Mapping[str, bool]) -> dict[str, None]:
    """Packages of a library module no outside importer can reach."""
    prefix = len(module_dir) + 1 if module_dir else 0
    return {
        d: None
        for d, is_main in packages.items()
        if is_main or "internal" in PurePosixPath(d[prefix:]).parts
    }


def _go_library(module_dir: str, packages: Mapping[str, bool]) -> bool:
    """Whether a module's root package is importable, or it builds no program."""
    return packages.get(module_dir) is False or (bool(packages) and not any(packages.values()))


def go_published(facts: BuildFacts) -> dict[str, str | None]:
    """Every Go module dir, mapped to its path when it is a library.

    A module is meant for import when its root package is importable, or when
    it builds no program at all. Its ``main`` and ``internal`` packages are not.
    """
    modules = {
        d: _GO_MODULE_RE.search(facts.read(d, "go.mod") or "") for d in _dirs_with(facts, "go.mod")
    }
    by_module: dict[str, dict[str, bool]] = {d: {} for d in modules}
    for rel_dir, is_main in _go_packages(facts).items():
        owner = _innermost(rel_dir, modules)
        if owner is not None:
            by_module[owner][rel_dir] = is_main
    out: dict[str, str | None] = {}
    for module_dir, match in modules.items():
        own = by_module[module_dir]
        out[module_dir] = match.group(1) if match and _go_library(module_dir, own) else None
        if out[module_dir] is not None:
            out.update(_go_unexported(module_dir, own))
    return out


# --- Rust -------------------------------------------------------------------


def cargo_published(facts: BuildFacts) -> dict[str, str | None]:
    """Every crate dir, mapped to its name when it ships a library target."""
    out: dict[str, str | None] = {}
    for rel_dir in _dirs_with(facts, "Cargo.toml"):
        data = _toml(facts.read(rel_dir, "Cargo.toml"))
        package = data.get("package")
        if not isinstance(package, dict):
            continue  # a virtual workspace manifest is no crate
        name = package.get("name")
        has_lib = isinstance(data.get("lib"), dict) or "lib.rs" in facts.tree.get(
            _join(rel_dir, "src"), frozenset()
        )
        publishes = package.get("publish", True) not in (False, [])
        out[rel_dir] = name if isinstance(name, str) and has_lib and publishes else None
        if out[rel_dir] is not None:
            out.update(dict.fromkeys(_cargo_bin_paths(rel_dir, data)))
    return out


def _cargo_bin_paths(rel_dir: str, data: dict[str, Any]) -> list[str]:
    """Binary targets beside the library: programs, not its API."""
    bins = data.get("bin")
    declared = [b.get("path") for b in bins if isinstance(b, dict)] if isinstance(bins, list) else []
    paths = ["src/main.rs", "src/bin", *(p for p in declared if isinstance(p, str))]
    return [_join(rel_dir, p.removeprefix("./")) for p in paths]


# --- Java / Kotlin ----------------------------------------------------------

_POM_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_POM_PARENT_RE = re.compile(r"<parent>(.*?)</parent>", re.DOTALL)
_POM_ARTIFACT_RE = re.compile(r"<artifactId>\s*([^<\s]+)\s*</artifactId>")
_POM_PACKAGING_RE = re.compile(r"<packaging>\s*([^<\s]+)\s*</packaging>")
#: A pom that deploys an artifact: a repository to deploy to, or a plugin a
#: release to a public repository signs or uploads with.
_MAVEN_DEPLOYS = (
    "<distributionManagement>",
    "central-publishing-maven-plugin",
    "nexus-staging-maven-plugin",
    "maven-gpg-plugin",
)
#: Parents whose job is releasing to Maven Central.
_MAVEN_RELEASE_PARENTS = frozenset({"oss-parent", "commons-parent", "apache"})
#: A module that ships no library artifact although a parent deploys.
_MAVEN_NO_LIBRARY_RE = re.compile(
    r"spring-boot-maven-plugin|<maven\.deploy\.skip>\s*true\s*<"
)
_MAVEN_NON_LIBRARY_PACKAGING = frozenset({"pom", "war", "ear"})
_GRADLE_PUBLISHES = ("maven-publish", "com.vanniktech.maven.publish")
_GRADLE_APPS = frozenset({"application", "org.springframework.boot", "com.android.application"})


@dataclass(frozen=True)
class _Build:
    name: str | None
    deploys: bool
    app: bool


def _pom(text: str) -> _Build:
    text = _POM_COMMENT_RE.sub("", text)
    parent = _POM_PARENT_RE.search(text)
    parent_artifact = _POM_ARTIFACT_RE.search(parent.group(1)) if parent else None
    own = _POM_PARENT_RE.sub("", text, count=1)
    artifact = _POM_ARTIFACT_RE.search(own)
    packaging = _POM_PACKAGING_RE.search(own)
    release_parent = parent_artifact is not None and parent_artifact.group(1) in _MAVEN_RELEASE_PARENTS
    return _Build(
        name=artifact.group(1) if artifact else None,
        deploys=release_parent or any(m in own for m in _MAVEN_DEPLOYS),
        app=(packaging.group(1).lower() if packaging else "jar") in _MAVEN_NON_LIBRARY_PACKAGING
        or _MAVEN_NO_LIBRARY_RE.search(own) is not None,
    )


def _gradle(facts: BuildFacts, rel_dir: str) -> _Build:
    from repowise.core.ingestion.resolvers.jvm_gradle import _detect_plugins

    root = Path(facts.repo_root or ".")
    plugins = _detect_plugins(root / rel_dir)
    return _Build(
        name=PurePosixPath(rel_dir).name or root.name,
        deploys=any(p.startswith(_GRADLE_PUBLISHES) for p in plugins),
        app=bool(plugins & _GRADLE_APPS),
    )


def _publishing(builds: Mapping[str, _Build]) -> dict[str, str | None]:
    """A module publishes when it is no app and it, or a build enclosing it
    (a parent pom, a root ``subprojects`` block), deploys."""
    return {
        d: build.name
        if not build.app and any(builds[k].deploys for k in _self_and_parents(d) if k in builds)
        else None
        for d, build in builds.items()
    }


def jvm_published(facts: BuildFacts) -> dict[str, str | None]:
    """Every Maven or Gradle module dir, mapped to its artifact when it publishes."""
    poms = {d: _pom(facts.read(d, "pom.xml") or "") for d in _dirs_with(facts, "pom.xml")}
    gradle = {
        d: _gradle(facts, d)
        for d, names in facts.tree.items()
        if d not in poms and names & {"build.gradle", "build.gradle.kts"}
    }
    return {**_publishing(gradle), **_publishing(poms)}


# --- Python -----------------------------------------------------------------


def _python_distribution(data: dict[str, Any], names: frozenset[str]) -> str | None:
    """The distribution's name, unless the project says it ships none."""
    from repowise.core.workspace.code_api import _table

    name = _table(data, "project").get("name") or _table(data, "tool", "poetry").get("name")
    if not isinstance(name, str) or not name or _python_unpackaged(data, names):
        return None
    return name


def _python_unpackaged(data: dict[str, Any], names: frozenset[str]) -> bool:
    """No build backend (an app's pyproject), an opt-out, or a Django site."""
    from repowise.core.workspace.code_api import _table

    classifiers = _table(data, "project").get("classifiers")
    return (
        not _table(data, "build-system")
        or _table(data, "tool", "poetry").get("package-mode") is False
        or _table(data, "tool", "uv").get("package") is False
        or (isinstance(classifiers, list) and "Private :: Do Not Upload" in classifiers)
        or "manage.py" in names
    )


def _python_package_dirs(
    facts: BuildFacts, rel_dir: str, data: dict[str, Any], name: str
) -> list[str]:
    """The import packages a distribution ships: declared, else the module its
    name spells, in ``src/`` or beside the manifest."""
    from repowise.core.workspace.code_api import _pypi_package_dirs

    module = name.replace("-", "_").replace(".", "_")
    candidates = _pypi_package_dirs(data) or [
        f"src/{module}",
        module,
        f"src/{module.lower()}",
        module.lower(),
    ]
    return [d for d in (_join(rel_dir, c) for c in candidates) if d in facts.tree]


def _private_name(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def _python_private(facts: BuildFacts, package_dir: str) -> dict[str, None]:
    """Modules and subpackages under *package_dir* named as private."""
    out: dict[str, None] = {}
    for rel_dir, names in facts.tree.items():
        if rel_dir != package_dir and not rel_dir.startswith(f"{package_dir}/"):
            continue
        if rel_dir != package_dir and _private_name(PurePosixPath(rel_dir).name):
            out[rel_dir] = None
        out.update(
            {_join(rel_dir, n): None for n in names if n.endswith((".py", ".pyi")) and _private_name(n)}
        )
    return out


def python_published(facts: BuildFacts) -> dict[str, str | None]:
    """Every ``pyproject.toml`` dir, with the packages it ships mapped to its name."""
    out: dict[str, str | None] = {}
    for rel_dir in _dirs_with(facts, "pyproject.toml"):
        data = _toml(facts.read(rel_dir, "pyproject.toml"))
        out[rel_dir] = None
        name = _python_distribution(data, facts.tree.get(rel_dir, frozenset()))
        if name is None:
            continue
        for package_dir in _python_package_dirs(facts, rel_dir, data, name):
            out[package_dir] = name
            out.update(_python_private(facts, package_dir))
    return out


# --- TypeScript / JavaScript ------------------------------------------------

#: Fields only a package meant for import declares. A bare ``main`` is not one
#: (a server or an Electron app names its start file there) unless the
#: manifest also says what it uploads (``files``, ``publishConfig``).
_NPM_LIBRARY_KEYS = ("exports", "module", "types", "typings")
_NPM_UPLOAD_KEYS = ("files", "publishConfig")


def _npm_ships(data: dict[str, Any]) -> bool:
    if data.get("private") is True:
        return False
    if any(data.get(k) for k in _NPM_LIBRARY_KEYS):
        return True
    return bool(data.get("main")) and any(data.get(k) for k in _NPM_UPLOAD_KEYS)


def npm_published(facts: BuildFacts) -> dict[str, str | None]:
    """Every named ``package.json`` dir, mapped to its name when it is a library."""
    out: dict[str, str | None] = {}
    for rel_dir in _dirs_with(facts, "package.json"):
        try:
            data = json.loads(facts.read(rel_dir, "package.json") or "")
        except ValueError:
            continue
        name = data.get("name") if isinstance(data, dict) else None
        if not isinstance(name, str) or not name:
            continue  # a nameless manifest (``{"type": "module"}``) is no package
        out[rel_dir] = name if _npm_ships(data) else None
    return out


#: Language -> reader of the build files that declare a published package.
#: A language without an entry has no published-surface rule yet.
PUBLISHED_ROOTS: dict[str, Callable[[BuildFacts], Mapping[str, str | None]]] = {
    "csharp": dotnet_published,
    "vbnet": dotnet_published,
    "go": go_published,
    "rust": cargo_published,
    "java": jvm_published,
    "kotlin": jvm_published,
    "python": python_published,
    "typescript": npm_published,
    "javascript": npm_published,
}


def package_of(path: str, roots: Mapping[str, str | None]) -> str | None:
    """The package of the innermost project enclosing *path*, if it ships one.

    *path* itself is looked up first, so a reader can mark one file private.
    """
    key = _innermost(path, roots)
    return None if key is None else roots[key]
