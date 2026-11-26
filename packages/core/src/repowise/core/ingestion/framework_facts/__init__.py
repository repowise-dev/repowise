"""What each framework loads by convention, named once.

A framework recognised here is recognised the same way everywhere: tech-stack
detection reads :func:`detect_php_framework`, and the framework-edge handlers
anchor :attr:`FrameworkFacts.entry_globs` to a ``framework:`` node so dead-code
liveness sees the files the runtime loads without any importer. Angular names
its entry files in its workspace config instead (:func:`angular_entry_files`).
A JS framework is read from the ``package.json`` that depends on it
(:func:`js_frameworks`), and the dead-code deletion gate asks
:func:`convention_load_reasons`, so a file such a framework may load is never
deletion-ready on "no importer" alone.

One module per framework (``nuxt``, ``sveltekit``, ``php``, ...) holds its
facts and the config it reads; ``base`` holds :class:`FrameworkFacts`, ``npm``
the ``package.json`` reader, and ``js`` the per-manifest answer.
"""

from __future__ import annotations

from .angular import ANGULAR, ANGULAR_WORKSPACE_FILES, angular_entry_files
from .astro import ASTRO
from .base import FrameworkFacts
from .expo_router import EXPO_ROUTER
from .js import JS_FRAMEWORKS, convention_load_reasons, js_frameworks
from .next import NEXT_PAGES
from .npm import (
    NPM_DEP_FIELDS,
    NpmManifest,
    npm_dependency_names,
    npm_manifests,
    parse_npm_manifest,
)
from .nuxt import NUXT, NUXT_CONFIGS, NUXT_I18N, nuxt_src_dir
from .php import LARAVEL, PHP_FRAMEWORKS, SYMFONY, TYPO3, detect_php_framework
from .platforms import ANDROID, LWC, SALESFORCE
from .react_email import REACT_EMAIL
from .remix import REACT_ROUTER, REMIX
from .sveltekit import SVELTE_CONFIGS, SVELTEKIT, KitFiles, kit_files, sveltekit_lib_dir

__all__ = [
    "ANDROID",
    "ANGULAR",
    "ANGULAR_WORKSPACE_FILES",
    "ASTRO",
    "EXPO_ROUTER",
    "JS_FRAMEWORKS",
    "LARAVEL",
    "LWC",
    "NEXT_PAGES",
    "NPM_DEP_FIELDS",
    "NUXT",
    "NUXT_CONFIGS",
    "NUXT_I18N",
    "PHP_FRAMEWORKS",
    "REACT_EMAIL",
    "REACT_ROUTER",
    "REMIX",
    "SALESFORCE",
    "SVELTEKIT",
    "SVELTE_CONFIGS",
    "SYMFONY",
    "TYPO3",
    "FrameworkFacts",
    "KitFiles",
    "NpmManifest",
    "angular_entry_files",
    "convention_load_reasons",
    "detect_php_framework",
    "js_frameworks",
    "kit_files",
    "npm_dependency_names",
    "npm_manifests",
    "nuxt_src_dir",
    "parse_npm_manifest",
    "sveltekit_lib_dir",
]
