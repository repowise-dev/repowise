"""Which non-source files the graph build reads, and whether an edit changed what it read.

A graph patch is exact only when every input of the build that it does not
re-derive is unchanged. Source files are compared through their parses
(:mod:`.surface`); this module covers the rest of a commit: manifests,
configs, lockfiles, assets and data files.

- A file no build step opens or looks for cannot change the graph. Lockfiles,
  images, ``requirements.txt`` and CI workflow files are of this kind.
  :func:`presence_read` names the files some step opens or looks for, by name,
  glob or directory, from the readers under ``ingestion/`` (resolvers,
  warmups, framework edges, dynamic hints) plus the traverser, the tech stack
  detector and the manifest names the language registry declares. The list
  is deliberately wide: a file it names wrongly only costs a full build.
- A manifest the build does read can still be edited in a way no reader sees,
  such as a version bump. For ``package.json``, ``pyproject.toml`` and
  ``go.mod`` the last build records a digest of what its readers can consume
  (:func:`record_inputs`), and an edit that leaves the digest and the detected
  tech stack unchanged leaves the graph unchanged.

Ceiling: a new by-name reader in the graph build must be added here, or its
file's edits would patch around it. Upgrade path: route every manifest read
through one helper that this module can enumerate.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import tomllib
from collections.abc import Callable, Collection, Iterable
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

# Files a graph step opens by exact name (lowercased), wherever they sit.
_READ_NAMES = frozenset(
    name.lower()
    for name in (
        # traversal: ignore rules, submodules, console scripts, test roots
        ".gitignore", ".gitattributes", ".gitmodules", ".repowiseignore",
        "pytest.ini", "tox.ini", "setup.cfg", "setup.py", "pyproject.toml",
        "phpunit.xml", "phpunit.dist.xml", "phpunit.xml.dist", "ProjectVersion.txt",
        # JS / TS workspaces, entries, aliases, tool configs
        "package.json", "plugin.json", "jsconfig.json", "pnpm-workspace.yaml",
        "angular.json", "project.json", "deno.json", "deno.jsonc",
        # other ecosystems' build files and manifests
        "go.mod", "go.work", "Cargo.toml", "composer.json", "pubspec.yaml",
        "Package.swift", "Gemfile", "build.sbt", "build.sc", "pom.xml",
        "settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts",
        "Directory.Build.props", "Directory.Build.targets", "Directory.Packages.props",
        "PublicAPI.Shipped.txt", "CMakeLists.txt", "BUILD", "BUILD.bazel",
        "WORKSPACE", "WORKSPACE.bazel", "MODULE.bazel", "compile_commands.json",
        "AndroidManifest.xml", "web.xml", "spring.factories",
        "sfdx-project.json", "cumulusci.yml", "dbt_project.yml",
        "default.project.json", ".luaurc", "project.godot", "plugin.cfg",
        "serverless.yml", "serverless.yaml", "template.yml", "template.yaml",
        "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
        # docs-site markers that change sibling files' code origin
        "mkdocs.yml", "mkdocs.yaml", "_config.yml", "book.toml", "antora.yml",
        "docfx.json", "writerside.cfg",
    )
)

# Globs over the lowercased file name.
_READ_GLOBS = (
    "tsconfig*.json", "vitest.config.*", "vite.config.*", "jest.config.*",
    "playwright.config.*", "*.mdx",
    "*.csproj", "*.vbproj", "*.fsproj", "*.sln", "*.props", "*.targets",
    "*.gemspec", "phpstan*.neon", "phpstan*.neon.dist", "*.tf",
    "*.uid", "*.pro", "*.vcxproj", "*.rc", "*.cmake", "*.bzl",
    "*.dproj", "*.lpi", "*.dpr", "*.dpk", "*.lpr",
    "*.xml", "*.xaml", "*.axaml", "*.asax", "*.aspx", "*.ascx", "*.ashx", "*.asmx", "*.master",
    "*.imports", "*.properties", "*.flexipage", "*.quickaction", "*.tab",
    # API specs are read whole to flag the file itself
    "*openapi*", "*swagger*", "api.json", "api.yaml", "api.yml",
)

# Directory segments under which steps read files by directory, not by name.
_READ_DIR_SEGMENTS = frozenset(
    {"meta-inf", "web-inf", ".cmake", ".repowise", "res", "resources",
     "flexipages", "quickactions", "tabs", "experiences"}
)

# Languages whose files may be data no step reads: an edit to one is then
# inert like prose, provided its parse is empty and it is not read by name.
DATA_LANGUAGES = frozenset({"json", "yaml", "toml"})


@cache
def _registry_names() -> frozenset[str]:
    """Every manifest or descriptor name a language spec declares, lowercased."""
    from repowise.core.ingestion.languages.registry import REGISTRY

    names = (
        REGISTRY.manifest_filenames()
        | REGISTRY.package_manifest_filenames()
        | REGISTRY.descriptor_filenames()
    )
    return frozenset(name.lower() for name in names)


def presence_read(path: str) -> bool:
    """Whether some graph step opens *path* or checks that it exists."""
    pure = PurePosixPath(path.lower())
    name = pure.name
    if name in _READ_NAMES or name in _registry_names():
        return True
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in _READ_GLOBS):
        return True
    if _READ_DIR_SEGMENTS.intersection(pure.parts[:-1]):
        return True
    from repowise.core.ingestion.framework_edges.test_runner_setup import is_runner_config

    return is_runner_config(path)


# --- what the build reads from the manifests it does open --------------------

# Top-level package.json fields no graph step reads. ``dependencies`` and
# ``devDependencies`` feed only the tech stack, which is compared on its own.
_NPM_UNREAD = frozenset(
    {
        "version", "description", "keywords", "author", "contributors", "maintainers",
        "license", "repository", "homepage", "bugs", "funding", "private",
        "publishConfig", "files", "packageManager", "dependencies", "devDependencies",
        "peerDependencies", "peerDependenciesMeta", "optionalDependencies",
        "bundledDependencies", "bundleDependencies", "overrides", "resolutions",
        "volta", "lint-staged", "prettier", "eslintConfig", "browserslist",
    }
)
# pyproject.toml tables and fields no graph step reads (the tech stack scans
# the root file's text, and is compared on its own).
_PYPROJECT_UNREAD_TOP = frozenset({"build-system", "dependency-groups"})
_PYPROJECT_UNREAD_PROJECT = frozenset(
    {
        "version", "description", "readme", "requires-python", "license", "license-files",
        "authors", "maintainers", "keywords", "classifiers", "urls", "dependencies",
        "optional-dependencies", "dynamic",
    }
)
_PYPROJECT_UNREAD_TOOLS = frozenset(
    {
        "ruff", "mypy", "coverage", "black", "isort", "pyright", "codespell", "pylint",
        "bumpversion", "towncrier", "hatch", "setuptools", "setuptools_scm", "flit",
        "commitizen", "check-manifest", "interrogate", "pydocstyle", "bandit", "cibuildwheel",
    }
)


def _package_json(text: str, _path: Path, _root: Path) -> object:
    from repowise.core.ingestion.framework_edges.test_runner_setup import (
        leaves_its_directory,
        setup_specs,
    )

    data = json.loads(text)
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k not in _NPM_UNREAD}
    # The test-runner handler scans the raw text, at any depth.
    raw = text.encode()
    return [data, setup_specs(raw), leaves_its_directory(raw)]


def _without(table: object, unread: frozenset[str]) -> object:
    if isinstance(table, dict):
        return {k: v for k, v in table.items() if k not in unread}
    return table


def _pyproject(text: str, _path: Path, _root: Path) -> object:
    data = tomllib.loads(text)
    out = {k: v for k, v in data.items() if k not in _PYPROJECT_UNREAD_TOP}
    if "project" in out:
        out["project"] = _without(out["project"], _PYPROJECT_UNREAD_PROJECT)
    if "tool" in out:
        out["tool"] = _without(out["tool"], _PYPROJECT_UNREAD_TOOLS)
    return out


def _go_mod(_text: str, path: Path, root: Path) -> object:
    # The Go resolver's own readers: whatever they return is all it uses.
    from repowise.core.ingestion.resolvers.go import (
        _read_local_replacements,
        _read_module_directive,
    )

    return [_read_module_directive(path), _read_local_replacements(path, root)]


_PROJECTORS: dict[str, Callable[[str, Path, Path], object]] = {
    "package.json": _package_json,
    "pyproject.toml": _pyproject,
    "go.mod": _go_mod,
}


def has_projection(path: str) -> bool:
    """Whether an edit to *path* can be cleared by comparing what its readers read."""
    return PurePosixPath(path).name in _PROJECTORS


def _digest(root: Path, path: str) -> str | None:
    """Digest of what the build reads from *path*; ``None`` when it does not exist.

    A file its readers cannot decode or parse digests by its bytes, so any
    edit to it counts as a change.
    """
    file = root / path
    try:
        raw = file.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        return "unreadable"
    try:
        text = raw.decode("utf-8")
        view = _PROJECTORS[PurePosixPath(path).name](text, file, root)
        blob = json.dumps(view, sort_keys=True, default=str)
    except Exception:
        return "raw:" + hashlib.sha256(raw).hexdigest()
    return "view:" + hashlib.sha256(blob.encode()).hexdigest()


def stack_names(root: Path) -> list[str]:
    """The tech stack names that pick the framework-edge handlers."""
    from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

    return sorted(item.name for item in detect_tech_stack(root))


def record_inputs(root: Path, paths: Iterable[str]) -> dict[str, Any] | None:
    """What the build reads from its manifests, for the next patch to compare.

    Best effort: ``None`` (no record, so manifest edits fall back) on any error.
    """
    root = Path(root)
    try:
        files = {path: _digest(root, path) for path in sorted(set(paths)) if has_projection(path)}
        return {"files": files, "stack": stack_names(root)}
    except Exception:
        return None


def inputs_refusal(
    current: dict[str, Any] | None, record: dict[str, Any] | None, views: Collection[str]
) -> str | None:
    """Why the manifests now differ from what the last build read, or ``None``.

    Every recorded manifest is compared, not just the changed ones: an edit
    the update's diff missed (uncommitted, or before its base) must not be
    taken as already seen.
    """
    if current is None or record is None:
        return f"{min(views)}: no record of what the last build read from it" if views else None
    before, now = record.get("files") or {}, current.get("files") or {}
    for path in sorted(set(before) | set(now)):
        if before.get(path, "absent") != now.get(path, "absent"):
            return f"{path}: a field the graph build reads changed"
    missing = sorted(set(views) - set(now))
    if missing:
        return f"{missing[0]}: no record of what the last build read from it"
    if record.get("stack") != current.get("stack"):
        return "the detected tech stack changed"
    return None


# --- the gate ---------------------------------------------------------------

_LOCKFILE_NAMES = frozenset(
    {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.sum", "cargo.lock", "poetry.lock", "uv.lock"}
)


def _unread_unindexed(path: str) -> bool:
    """Whether an unindexed file, edited in place, is one no graph step reads.

    Live walks read unindexed files of known source extensions (an oversized
    ``.cs``, a gitignored ``.rb``), so only files the language registry cannot
    name, and lockfiles, qualify.
    """
    from repowise.core.ingestion.languages.registry import REGISTRY

    if presence_read(path):
        return False
    pure = PurePosixPath(path)
    name = pure.name.lower()
    if name in _LOCKFILE_NAMES or name.endswith(".lock"):
        return True
    language = REGISTRY.from_filename(pure.name) or REGISTRY.from_extension(pure.suffix.lower())
    return language in (None, "unknown")


def _unindexed_refusal(
    unparsed: Collection[str], presence_changed: Collection[str] | None, views: list[str]
) -> str | None:
    """Why an unindexed change could reach the graph; manifests go to *views*."""
    for path in sorted(unparsed):
        if has_projection(path):
            views.append(path)
        elif presence_changed is None or path in presence_changed:
            return f"{path}: an unindexed file was added or removed"
        elif not _unread_unindexed(path):
            return f"{path}: the graph build reads this file"
    return None


def _cleared_edits(
    changed: dict[str, Any],
    previous_parse: Callable[[str, Any], Any],
    config_reads: Collection[str],
    views: list[str],
) -> set[str]:
    """Indexed data files whose edit no step can see; manifests also go to *views*."""
    from .surface import parse_is_empty

    cleared: set[str] = set()
    for path, parsed in changed.items():
        if parsed.file_info.language not in DATA_LANGUAGES or not parse_is_empty(parsed):
            continue
        before = previous_parse(path, parsed)
        if before is None or not parse_is_empty(before):
            continue
        if has_projection(path):
            views.append(path)
            cleared.add(path)
        elif not presence_read(path) and path not in config_reads:
            cleared.add(path)
    return cleared


def plan_non_source_changes(
    *,
    unparsed: Collection[str],
    changed: dict[str, Any],
    previous_parse: Callable[[str, Any], Any],
    presence_changed: Collection[str] | None,
    config_reads: Collection[str],
    current: dict[str, Any] | None,
    record: dict[str, Any] | None,
) -> tuple[str | None, frozenset[str]]:
    """``(refusal, cleared)`` for the non-source part of an update.

    *unparsed* are changed paths the walk does not index now and did not
    before. Each must be a file no graph step opens, edited in place (not in
    *presence_changed*, the added, deleted and renamed paths; ``None`` when
    unknown), or a manifest whose recorded view is unchanged.

    *cleared* are the indexed data and manifest files in *changed* whose edit
    no graph step can see: an empty parse before and after, and either no
    reader at all (not read by name, not in *config_reads*, the files the
    tsconfig resolver loaded) or an unchanged recorded view.

    *current* and *record* are :func:`record_inputs` for this tree and for
    the last build; when both exist they must match whatever changed.
    """
    views: list[str] = []
    refusal = _unindexed_refusal(unparsed, presence_changed, views)
    if refusal is not None:
        return refusal, frozenset()
    cleared = _cleared_edits(changed, previous_parse, config_reads, views)
    reason = inputs_refusal(current, record, views)
    return reason, (frozenset() if reason else frozenset(cleared))
