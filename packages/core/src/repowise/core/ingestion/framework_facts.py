"""What each PHP framework loads by convention, named once.

A framework recognised here is recognised the same way everywhere: tech-stack
detection reads :func:`detect_php_framework`, and the framework-edge handlers
anchor :attr:`FrameworkFacts.entry_globs` to a ``framework:`` node so dead-code
liveness sees the files the runtime loads without any importer.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable
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
    # Jobs, middleware and events are left out on purpose: code names them
    # (``Job::dispatch``, the middleware list, ``event(new ...)``), so one
    # with no importer really is unused.
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
        "app/Listeners/*.php",  # event discovery
        "app/Policies/*.php",  # policy discovery by model name
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
