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
"""

from __future__ import annotations

import fnmatch
import json
import posixpath
import re
import shlex
from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path

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
    #: npm packages whose presence in a ``package.json`` marks the framework.
    npm_packages: tuple[str, ...] = ()
    #: Files the framework imports by name when a template uses them (Nuxt
    #: auto-imports). Not entries, since an unused one is never loaded, but no
    #: importer is not proof of disuse either, so none is deletion-ready.
    review_globs: tuple[str, ...] = ()

    def matches(self, manifest: ComposerManifest) -> bool:
        if manifest.type in self.composer_types:
            return True
        requires = manifest.requires
        return any(pkg in requires for pkg in self.composer_packages)

    def matches_npm(self, manifest: NpmManifest) -> str | None:
        """The first of :attr:`npm_packages` *manifest* depends on, or None."""
        return next((pkg for pkg in self.npm_packages if pkg in manifest.dependencies), None)

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


#: The Salesforce org, which places what flexipages, quick actions, tabs,
#: email services and Experience sites name.
SALESFORCE = FrameworkFacts(name="Salesforce", anchor="framework:salesforce")

#: The runtime that starts the components an ``AndroidManifest.xml`` registers.
ANDROID = FrameworkFacts(name="Android", anchor="framework:android")

ANGULAR = FrameworkFacts(name="Angular", anchor="framework:angular")

#: The Lightning runtime that places the LWC bundles a ``.js-meta.xml`` exposes.
LWC = FrameworkFacts(name="Lightning Web Components", anchor="framework:lwc")

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


# --- JavaScript frameworks, read from package.json ---------------------------

_JS_EXTS = ("tsx", "ts", "jsx", "js", "mjs", "cjs", "mts", "cts")


def _js(*stems: str, exts: tuple[str, ...] = _JS_EXTS) -> tuple[str, ...]:
    """Each glob stem with every JS/TS extension (``*`` spans ``/``)."""
    return tuple(f"{stem}.{ext}" for stem in stems for ext in exts)


def _under(prefixes: tuple[str, ...], *globs: str) -> tuple[str, ...]:
    return tuple(f"{prefix}{glob}" for prefix in prefixes for glob in globs)


#: The React Email preview server (``email dev``) renders the default export of
#: every file in its emails directory; nothing imports a preview component.
REACT_EMAIL = FrameworkFacts(
    name="React Email",
    anchor="framework:react-email",
    npm_packages=("react-email", "@react-email/preview-server"),
    entry_globs=_js("emails/*"),  # re-rooted at a ``--dir`` (:func:`js_frameworks`)
)

NEXT_PAGES = FrameworkFacts(
    name="Next.js",
    anchor="framework:nextjs",
    npm_packages=("next",),
    # The Pages Router: every file under ``pages/`` is a route or API handler.
    # App Router files are named by basename in the dead-code never-flag globs.
    entry_globs=_js("pages/*", "src/pages/*", exts=(*_JS_EXTS, "mdx")),
)

REMIX = FrameworkFacts(
    name="Remix",
    anchor="framework:remix",
    # React Router v7's framework mode is Remix renamed.
    npm_packages=("@remix-run/dev", "@remix-run/react", "@react-router/dev"),
    entry_globs=_js("app/root", "app/routes", "app/routes/*"),
)

#: Nuxt 3 keeps app code at the root; Nuxt 4's default ``srcDir`` is ``app/``.
_NUXT_SRC = ("", "app/")
NUXT = FrameworkFacts(
    name="Nuxt",
    anchor="framework:nuxt",
    npm_packages=("nuxt", "nuxt3"),
    entry_globs=(
        *_under(_NUXT_SRC, "pages/*", "layouts/*", "middleware/*", "plugins/*"),
        *_under(_NUXT_SRC, "app.vue", "error.vue", *_js("app.config", "router.options")),
        "server/*",
    ),
    review_globs=(*_under(_NUXT_SRC, "components/*", "composables/*", "utils/*"), "shared/*"),
)

SVELTEKIT = FrameworkFacts(
    name="SvelteKit",
    anchor="framework:sveltekit",
    npm_packages=("@sveltejs/kit",),
    entry_globs=(
        "src/routes/+*",
        "src/routes/*/+*",
        "src/params/*",
        *_js("src/hooks", "src/hooks.server", "src/hooks.client", "src/service-worker"),
        "src/service-worker/*",
    ),
)

ASTRO = FrameworkFacts(
    name="Astro",
    anchor="framework:astro",
    npm_packages=("astro",),
    entry_globs=(
        "src/pages/*",
        *_js("src/middleware", "src/middleware/index", "src/content/config"),
        *_js("src/content.config", "src/actions/index"),
    ),
)

#: Expo Router: every file under ``app/`` is a route, layout or API route.
EXPO_ROUTER = FrameworkFacts(
    name="Expo Router",
    anchor="framework:expo-router",
    npm_packages=("expo-router",),
    entry_globs=("app/*", "src/app/*"),
)

JS_FRAMEWORKS: tuple[FrameworkFacts, ...] = (
    REACT_EMAIL, NEXT_PAGES, REMIX, NUXT, SVELTEKIT, ASTRO, EXPO_ROUTER,
)

#: The ``package.json`` fields that name dependencies.
NPM_DEP_FIELDS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


@dataclass(frozen=True, slots=True)
class NpmManifest:
    """The fields of one ``package.json`` the convention rules read."""

    rel_dir: str  # directory holding the manifest, '' for the repo root
    dependencies: frozenset[str] = frozenset()
    scripts: Mapping[str, str] = field(default_factory=dict)
    #: ``i18n.langDir`` values written in a ``nuxt.config.*`` beside the manifest.
    nuxt_lang_dirs: tuple[str, ...] = ()


def npm_dependency_names(data: object) -> frozenset[str]:
    """Every package a decoded ``package.json`` depends on, in any dependency field."""
    if not isinstance(data, dict):
        return frozenset()
    return frozenset(
        name
        for key in NPM_DEP_FIELDS
        if isinstance(block := data.get(key), dict)
        for name in block
        if isinstance(name, str)
    )


def parse_npm_manifest(data: object, rel_dir: str = "") -> NpmManifest | None:
    """A manifest from decoded ``package.json`` JSON, or None when it is not an object."""
    if not isinstance(data, dict):
        return None
    scripts = data.get("scripts")
    return NpmManifest(
        rel_dir=rel_dir,
        dependencies=npm_dependency_names(data),
        scripts={k: v for k, v in scripts.items() if isinstance(k, str) and isinstance(v, str)}
        if isinstance(scripts, dict)
        else {},
    )


def npm_manifests(repo_root: str | Path, paths: Iterable[str]) -> list[NpmManifest]:
    """The readable ``package.json`` in the repo root and every directory above *paths*.

    Only directories holding an indexed file are asked, so the cost follows
    the paths, and a ``node_modules`` tree the walk pruned is never read.
    """
    root = Path(repo_root)
    dirs = {""}
    for path in paths:
        parts = path.split("/")[:-1]
        dirs.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    found: list[NpmManifest] = []
    for rel in sorted(dirs):
        try:
            data = json.loads((root / rel / "package.json").read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            continue
        if (manifest := parse_npm_manifest(data, rel)) is not None:
            if NUXT_I18N in manifest.dependencies:
                manifest = replace(manifest, nuxt_lang_dirs=_nuxt_lang_dirs(root / rel))
            found.append(manifest)
    return found


NUXT_I18N = "@nuxtjs/i18n"
_NUXT_CONFIGS = tuple(f"nuxt.config.{ext}" for ext in ("ts", "js", "mjs", "mts"))
_LANG_DIR_RE = re.compile(r"""\blangDir\s*:\s*["'`]([^"'`\n]+)["'`]""")


def _nuxt_lang_dirs(app_dir: Path) -> tuple[str, ...]:
    """The literal ``langDir`` values in the Nuxt config in *app_dir*.

    Read, not evaluated: a computed value is missed, and only the default
    locale directories are anchored then.
    """
    for name in _NUXT_CONFIGS:
        try:
            text = (app_dir / name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        return tuple(dict.fromkeys(_LANG_DIR_RE.findall(text)))
    return ()


def _inside(path: str) -> str | None:
    """*path* normalised, or None when it leaves the package."""
    norm = posixpath.normpath(path)
    return None if norm == "." or norm.startswith(("..", "/")) else norm


def _nuxt_i18n_globs(manifest: NpmManifest) -> tuple[str, ...]:
    """Locale files and the vue-i18n config ``@nuxtjs/i18n`` loads by path.

    v9 and later resolve ``langDir`` under ``<rootDir>/i18n`` (default
    ``locales``); v8 resolved it under the source directory. Every reading is
    anchored, since an extra anchor only spares a file that is not there.
    """
    dirs = {"i18n/locales", "locales", "app/locales"}
    for lang_dir in manifest.nuxt_lang_dirs:
        for base in ("i18n", "", "app"):
            if (inside := _inside(posixpath.join(base, lang_dir))) is not None:
                dirs.add(inside)
    configs = _js("i18n.config", "i18n/i18n.config", "app/i18n.config")
    return (*(f"{d}/*" for d in sorted(dirs)), *configs)


_EMAIL_COMMANDS = frozenset({"dev", "build", "export"})


def _email_script_dir(args: list[str]) -> str:
    """The ``--dir`` among one ``email`` command's *args*, else the default ``emails``."""
    for j, arg in enumerate(args):
        if arg in ("--dir", "-d") and j + 1 < len(args):
            return posixpath.normpath(args[j + 1])
        if arg.startswith("--dir="):
            return posixpath.normpath(arg.removeprefix("--dir="))
    return "emails"


def _react_email_dirs(manifest: NpmManifest) -> list[str]:
    """Every directory an ``email dev|build|export`` script reads, sorted.

    A command without ``--dir`` reads the default ``emails``, as does a
    package with no such script. A directory outside the package is dropped:
    nothing under the package can be anchored to it.
    """
    dirs: set[str] = set()
    for script in manifest.scripts.values():
        try:
            words = shlex.split(script)
        except ValueError:
            continue
        for i in range(len(words) - 1):
            if words[i] == "email" and words[i + 1] in _EMAIL_COMMANDS:
                dirs.add(_email_script_dir(words[i + 2 :]))
    return sorted(
        d for d in (dirs or {"emails"}) if d != "." and not d.startswith(("..", "/"))
    )


def js_frameworks(manifest: NpmManifest) -> list[tuple[FrameworkFacts, str]]:
    """``(facts, the dependency that marks it)`` for each JS framework *manifest* uses.

    The facts' globs are relative to the manifest's directory. A React Email
    project's are rooted at every directory its ``email`` scripts read.
    """
    out: list[tuple[FrameworkFacts, str]] = []
    for facts in JS_FRAMEWORKS:
        pkg = facts.matches_npm(manifest)
        if pkg is None:
            continue
        if facts is NUXT and NUXT_I18N in manifest.dependencies:
            facts = replace(facts, entry_globs=facts.entry_globs + _nuxt_i18n_globs(manifest))
        if facts is REACT_EMAIL:
            dirs = _react_email_dirs(manifest)
            if not dirs:
                continue
            facts = replace(facts, entry_globs=_js(*(f"{d}/*" for d in dirs)))
        out.append((facts, pkg))
    return out


def convention_load_reasons(repo_root: str | Path, paths: Iterable[str]) -> dict[str, str]:
    """For each of *paths* a JS framework may load by convention, why.

    A file a framework declared in a ``package.json`` at or above it anchors
    (an entry glob) or imports by name when used (a review glob). The dead-code
    deletion gate reads this: the framework, not an importer, decides whether
    such a file runs.
    """
    wanted = sorted(set(paths))
    reasons: dict[str, str] = {}
    for manifest in npm_manifests(repo_root, wanted):
        for facts, pkg in js_frameworks(manifest):
            loaded = replace(facts, entry_globs=facts.entry_globs + facts.review_globs)
            for path in loaded.entry_files(manifest.rel_dir, wanted):
                reasons.setdefault(
                    path,
                    f"{facts.name} loads files at this path by convention "
                    f"({manifest.rel_dir or '.'}/package.json depends on {pkg}), "
                    "so no importer is not proof of disuse",
                )
    return reasons
