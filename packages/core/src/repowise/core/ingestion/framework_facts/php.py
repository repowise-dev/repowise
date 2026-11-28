"""PHP frameworks, read from ``composer.json``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import FrameworkFacts

if TYPE_CHECKING:
    from ..composer import ComposerManifest

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
