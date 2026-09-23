"""What each framework loads by convention, named once.

A framework recognised here is recognised the same way everywhere: tech-stack
detection reads :func:`detect_php_framework`, and the framework-edge handlers
anchor :attr:`FrameworkFacts.entry_globs` to a ``framework:`` node so dead-code
liveness sees the files the runtime loads without any importer. Angular names
its entry files in its workspace config instead (:func:`angular_entry_files`).
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from functools import cache

from .composer import ComposerManifest


@dataclass(frozen=True, slots=True)
class FrameworkFacts:
    name: str  # tech-stack display name
    anchor: str = ""  # graph node the convention edges hang off
    composer_packages: tuple[str, ...] = ()  # any of these in require marks it
    composer_types: tuple[str, ...] = ()  # or a composer ``type`` value
    #: Files the runtime loads with no importer, relative to the app (or
    #: extension) root. ``*`` spans ``/``, as in the dead-code never-flag globs.
    entry_globs: tuple[str, ...] = ()

    def matches(self, manifest: ComposerManifest) -> bool:
        if manifest.type in self.composer_types:
            return True
        requires = manifest.requires
        return any(pkg in requires for pkg in self.composer_packages)

    def entry_files(self, root: str, paths: Iterable[str]) -> list[str]:
        """The members of *paths* that match an entry glob under *root*, sorted.

        The root is matched literally and only the part below it against the
        globs, so a directory named ``ext[old]`` is not read as a pattern.
        """
        if not self.entry_globs:
            return []
        prefix = f"{root}/" if root else ""
        pattern = _compiled(self.entry_globs)
        return sorted(
            p for p in paths if p.startswith(prefix) and pattern.match(p, len(prefix))
        )


@cache
def _compiled(globs: tuple[str, ...]) -> re.Pattern[str]:
    # Case-sensitive on every platform, like the paths it is matched against.
    return re.compile("|".join(fnmatch.translate(g) for g in globs))


TYPO3 = FrameworkFacts(
    name="TYPO3",
    anchor="framework:typo3-core",
    composer_packages=("typo3/cms-core",),
    composer_types=("typo3-cms-extension",),
    # Relative to each extension root.
    entry_globs=(
        "ext_localconf.php",
        "ext_emconf.php",
        "ext_tables.php",  # legacy v11-v13; absent in v14
        "ext_tables.sql",
        "Configuration/JavaScriptModules.php",
        "Configuration/ContentSecurityPolicies.php",
        "Configuration/RequestMiddlewares.php",
        "Configuration/Icons.php",
        "Configuration/Services.php",
        "Configuration/Services.yaml",
        "Configuration/Services.yml",
        "Configuration/TCA/*.php",
        "Configuration/Backend/*.php",
        "Configuration/RTE/*.yaml",
        "Configuration/RTE/*.yml",
    ),
)

SYMFONY = FrameworkFacts(
    name="Symfony",
    composer_packages=("symfony/framework-bundle", "symfony/symfony"),
)

LARAVEL = FrameworkFacts(
    name="Laravel",
    anchor="framework:laravel",
    composer_packages=("laravel/framework",),
    # Jobs, middleware, events, listeners and policies are left out on
    # purpose: code names or registers them (``Job::dispatch``, an alias
    # array, ``$listen``, a typed ``handle`` in ``app/Listeners``, the model a
    # policy is named after), and ``framework_edges/laravel.py`` links them
    # from there, so one nothing names really is unused.
    entry_globs=(
        "routes/*.php",
        "bootstrap/app.php",
        "bootstrap/providers.php",
        "config/*.php",
        "app/Providers/*.php",
        "app/Console/Kernel.php",
        "app/Console/Commands/*.php",  # auto-discovered artisan commands
        "app/Http/Kernel.php",
        "app/Exceptions/Handler.php",
        "database/migrations/*.php",
        "database/factories/*.php",  # resolved from the model by HasFactory
        "database/seeders/*.php",
        "lang/*.php",  # translation files, loaded by the translator
        "resources/lang/*.php",
    ),
)

#: Precedence order for tech-stack detection: TYPO3 ships Symfony components,
#: so a TYPO3 package must not read as a Symfony app.
PHP_FRAMEWORKS: tuple[FrameworkFacts, ...] = (TYPO3, SYMFONY, LARAVEL)


def detect_php_framework(manifest: ComposerManifest) -> FrameworkFacts | None:
    """The first framework in precedence order that *manifest* declares."""
    return next((facts for facts in PHP_FRAMEWORKS if facts.matches(manifest)), None)


ANGULAR = FrameworkFacts(name="Angular", anchor="framework:angular")

#: Workspace config naming what an Angular build loads: Angular CLI's
#: ``angular.json`` and an Nx project's ``project.json``.
ANGULAR_WORKSPACE_FILES = frozenset({"angular.json", "project.json"})
# Build and test options whose value is a file loaded with no importer. A
# package (`"polyfills": ["zone.js"]`) is dropped by the path check.
_ANGULAR_ENTRY_OPTIONS = frozenset({"main", "browser", "server", "polyfills", "karmaConfig", "scripts"})
# Options holding objects that name such a file: `fileReplacements: [{ with }]`,
# `ssr: { entry }`, `scripts: [{ input }]`.
_ANGULAR_ENTRY_KEYS = ("with", "entry", "input")


def _nx_root(config_path: str, paths: AbstractSet[str]) -> str:
    """The nearest directory above an Nx ``project.json`` holding ``nx.json``, else the repo root."""
    parts = config_path.split("/")[:-1]
    for depth in range(len(parts), 0, -1):
        base = "/".join(parts[:depth])
        if f"{base}/nx.json" in paths:
            return base
    return ""


def angular_entry_files(config_path: str, config: object, paths: AbstractSet[str]) -> list[str]:
    """The members of *paths* the workspace config at *config_path* names as loaded.

    Entry options (``main``, ``browser``, ``polyfills``, ``scripts``,
    ``ssr.entry``, ...) and each ``fileReplacements`` ``with``
    (``environment.prod.ts``, swapped in by the build), read only from targets
    an Angular builder runs. ``angular.json`` paths are relative to its
    directory, an Nx ``project.json``'s to the workspace root; a value is kept
    only when it names a file.
    """
    values: list[object] = []

    def collect(value: object) -> None:
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict):
                values.extend(item.get(k) for k in _ANGULAR_ENTRY_KEYS)
            else:
                values.append(item)

    def walk(node: object) -> None:
        if isinstance(node, dict):
            builder = node.get("builder") or node.get("executor")
            if isinstance(builder, str) and "angular" not in builder:
                return  # an Nx target another toolchain builds (`@nx/node:build`)
            for key, value in node.items():
                if key in _ANGULAR_ENTRY_OPTIONS or key in ("fileReplacements", "ssr"):
                    collect(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(config)
    if config_path.rpartition("/")[2] == "project.json":
        base = _nx_root(config_path, paths)
    else:
        base = posixpath.dirname(config_path)
    found = {
        posixpath.normpath(posixpath.join(base, v)) for v in values if isinstance(v, str) and v
    }
    return sorted(found & paths)
