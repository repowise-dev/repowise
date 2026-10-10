"""Scopes: what a changed file runs when every test is more than it can reach.

:func:`~.test_selection.select_tests` runs everything for a file whose tests
are unknown. For three kinds the reach is known, and :func:`trigger_scopes`
names it as a :class:`Scope`:

- A manifest, lockfile or runner config runs its ecosystem's tests. A JS
  package (``package.json``, its lockfile) or a Go module runs those under its
  directory plus every test reaching code there; the rest run the whole
  ecosystem, because one environment serves every package (a venv, a bundle)
  or the file applies far from its directory (tsconfig ``extends``, a runner
  config whose globs reach other trees). Cargo stays a full run: Rust unit
  tests live inside source files, which no selection lists.
- A production ``__init__.py`` runs the tests reaching any module under its
  directory: importing one runs it, whatever the diff.
- An asset runs the tests reaching the code that names it. A doc, template,
  stylesheet or image nothing names runs the tests of the package holding it;
  a query or data file nothing names keeps its full run (a test may glob it).
  Other non-code files, dotfiles and root-level data configure tools, not
  code, and keep their full run too.

A scope also runs the tests reaching each file that names the changed one, so
a test reading ``package.json`` as data is kept. An ecosystem scope already
holds its own ecosystem's code under its root, so only the namers outside it
are traced; more than :data:`MAX_NAMERS` of those (or of an asset's namers)
run everything, since tracing that many costs what a full run saves. Package
roots are read from the tracked paths the caller already holds, which list
every manifest (the indexed file list misses some; see
:mod:`~repowise.core.ingestion.package_roots`). Ceilings: code that globs a
directory instead of naming a file, and code reading a manifest through a
name it builds, are not seen.
"""

from __future__ import annotations

import functools
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

import pathspec

from ..test_paths import is_test_related_path
from .test_selection import (
    JS_SUFFIXES,
    PACKAGE_INIT_REASON,
    PYTHON_SUFFIXES,
    SHARED_TEST_DATA_REASON,
    TestSelectionConfig,
    full_run_on_spec,
    full_run_reason,
    is_code_file,
    is_documentation,
    is_runnable_test,
)

# Namers past this many make a full run cheaper to decide than to trace.
MAX_NAMERS = 50

_JVM = (".java", ".kt", ".kts", ".scala", ".groovy")
# (ecosystem, test suffixes, scoped to the file's directory, gitwildmatch
# patterns). Each pattern is also a full-run trigger (``_FULL_RUN_GROUPS``),
# so a file no scope covers still runs everything; a test pins the two lists.
ECOSYSTEMS: tuple[tuple[str, tuple[str, ...], bool, tuple[str, ...]], ...] = (
    (
        "JavaScript",
        JS_SUFFIXES,
        True,
        ("package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"),
    ),
    (
        "JavaScript",
        JS_SUFFIXES,
        False,
        (
            *("tsconfig*.json", "jest.config.*", "vitest.config.*", "vite.config.*"),
            *("pnpm-workspace.yaml", ".nvmrc", ".node-version", ".npmrc", ".yarnrc"),
            ".yarnrc.yml",
        ),
    ),
    (
        "Python",
        PYTHON_SUFFIXES,
        False,
        (
            *("pyproject.toml", "setup.py", "setup.cfg", "poetry.lock", "uv.lock", "Pipfile"),
            *("Pipfile.lock", "requirements*.txt", "constraints*.txt", "requirements*.in"),
            *("constraints*.in", "**/requirements/**", "pytest.ini", "tox.ini", "noxfile.py"),
            ".python-version",
        ),
    ),
    ("Go", (".go",), True, ("go.mod", "go.sum")),
    ("Go", (".go",), False, ("go.work", "go.work.sum")),
    ("Ruby", (".rb",), False, ("Gemfile", "Gemfile.lock")),
    ("PHP", (".php",), False, ("composer.json", "composer.lock")),
    ("JVM", _JVM, False, ("build.gradle*", "settings.gradle*", "pom.xml")),
)
_ECOSYSTEM_SPECS = tuple(
    (lang, suffixes, scoped, pathspec.PathSpec.from_lines("gitwildmatch", globs))
    for lang, suffixes, scoped, globs in ECOSYSTEMS
)

# Assets the package holding them stands for when no code names them. ``.txt``
# counts only as documentation (``docs/``, root README-like files): elsewhere
# it may be a pinned dependency list.
_OWNED = frozenset(
    {".md", ".mdx", ".rst", ".adoc", ".markdown", ".html", ".htm"}
    | {".j2", ".jinja", ".jinja2", ".tmpl", ".tpl", ".hbs", ".mustache", ".ejs", ".njk"}
    | {".css", ".scss", ".sass", ".less"}
    | {".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}
)
# Assets only a naming file stands for: a test may glob a query or data file.
# Data only below the root: a root-level ``*.yaml`` or ``*.json`` configures a tool.
_NAMED_ONLY = frozenset({".scm", ".graphql", ".gql", ".sql"})
_DATA = frozenset({".json", ".yaml", ".yml", ".csv", ".sha256"})


@dataclass(frozen=True)
class Scope:
    """The tests a changed file runs in place of every test.

    Every known test under *root* whose suffix is in *suffixes* (any test when
    empty; none when *root* is ``None``), plus the tests reaching each file in
    *reach* and in *namers*. *reach* is production code under *root*, which
    may have no test of its own; a namer is code naming the file, so a namer no
    test reaches leaves the file's tests unknown. *run_all* says why the file
    runs everything after all.
    """

    why: str
    basis: str
    root: str | None = None
    suffixes: tuple[str, ...] = ()
    reach: tuple[str, ...] = ()
    namers: tuple[str, ...] = ()
    run_all: str | None = None

    @property
    def routes(self) -> tuple[str, ...]:
        """The files whose reaching tests the selection needs from the graph."""
        return () if self.run_all else (*self.reach, *self.namers)


def needs_namers(path: str, config: TestSelectionConfig) -> bool:
    """Whether :func:`trigger_scopes` needs to know who names *path*."""
    if is_test_related_path(path) or _configured(path, config):
        return False
    return _ecosystem(path) is not None or (full_run_reason(path) is None and _is_asset(path))


def keeps_full_run(path: str, config: TestSelectionConfig) -> bool:
    """Whether *path* runs everything whatever names it, so no namer search can help."""
    if full_run_reason(path, config.full_run_on) is None:
        return False
    if is_test_related_path(path) or _configured(path, config):
        return True
    return full_run_reason(path) != PACKAGE_INIT_REASON and _ecosystem(path) is None


def trigger_scopes(
    paths: Iterable[str],
    tracked: Collection[str],
    namers: Mapping[str, Collection[str]],
    config: TestSelectionConfig,
) -> dict[str, Scope]:
    """The :class:`Scope` each of *paths* runs instead of every test, where it has one.

    *tracked* are the checkout's files; *namers* maps a file to every file
    naming it (:func:`~.test_selection.file_namers`). A path with no scope
    keeps its rule.
    """
    tree = _Tree(tuple(tracked))
    out: dict[str, Scope] = {}
    for path in paths:
        if scope := _trigger_scope(path, tree, tuple(namers.get(path, ())), config):
            out[path] = scope
    return out


class _Tree:
    """The checkout's production code and package roots, each worked out once and only if asked."""

    def __init__(self, tracked: tuple[str, ...]) -> None:
        self.tracked = tracked
        self._under: dict[tuple[str, tuple[str, ...]], tuple[str, ...]] = {}

    @functools.cached_property
    def code(self) -> tuple[str, ...]:
        return tuple(
            sorted(p for p in self.tracked if is_code_file(p) and not is_test_related_path(p))
        )

    @functools.cached_property
    def roots(self) -> set[str]:
        from ..ingestion.package_roots import package_roots_from_paths

        return package_roots_from_paths(set(self.tracked))

    def code_under(self, root: str, suffixes: tuple[str, ...]) -> tuple[str, ...]:
        key = (root, suffixes)
        if key not in self._under:
            self._under[key] = tuple(
                p
                for p in self.code
                if _is_under(p, root) and (not suffixes or p.lower().endswith(suffixes))
            )
        return self._under[key]

    def owner(self, path: str) -> str | None:
        """The deepest package root holding *path*, never the repository root."""
        from ..ingestion.package_roots import module_for

        owner = module_for(path, self.roots)
        return owner if owner in self.roots else None


def _trigger_scope(
    path: str, tree: _Tree, namers: tuple[str, ...], config: TestSelectionConfig
) -> Scope | None:
    if is_test_related_path(path) or full_run_reason(path) == SHARED_TEST_DATA_REASON:
        return None  # read by tests anywhere: the test-tree rule decides
    if _configured(path, config):
        return None  # the config asked for a full run
    if eco := _ecosystem(path):
        return _ecosystem_scope(path, eco, tree, namers)
    why = full_run_reason(path)
    if why == PACKAGE_INIT_REASON:
        return _package_scope(path, tree)
    if why is not None or not _is_asset(path):
        return None
    return _named_scope(path, tree, namers)


def _configured(path: str, config: TestSelectionConfig) -> bool:
    return bool(config.full_run_on) and full_run_on_spec(config.full_run_on).match_file(path)


def _ecosystem(path: str) -> tuple[str, tuple[str, ...], bool] | None:
    for lang, suffixes, scoped, spec in _ECOSYSTEM_SPECS:
        if spec.match_file(path):
            return lang, suffixes, scoped
    return None


def _suffix(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def _is_asset(path: str) -> bool:
    p = PurePosixPath(path)
    if p.name.startswith(".") or is_code_file(path):
        return False
    suffix = _suffix(path)
    return (
        suffix in _OWNED
        or suffix in _NAMED_ONLY
        or (suffix in _DATA and len(p.parts) > 1)
        or is_documentation(path)
    )


def _too_many(namers: tuple[str, ...]) -> str | None:
    if len(namers) <= MAX_NAMERS:
        return None
    return f"{len(namers)} files name it, more than the {MAX_NAMERS} worth tracing"


def _ecosystem_scope(
    path: str, eco: tuple[str, tuple[str, ...], bool], tree: _Tree, namers: tuple[str, ...]
) -> Scope:
    lang, suffixes, scoped = eco
    root = str(PurePosixPath(path).parent) if scoped else "."
    if root == ".":
        why = f"it can change any {lang} test"
        reach: tuple[str, ...] = ()
    else:
        why = f"it can change the {lang} tests under {root}/ and those reaching its code"
        reach = tree.code_under(root, suffixes)
    outside = tuple(n for n in namers if not _holds(root, suffixes, n))
    if outside:
        why += f", and {_some(outside)} names it"
    return Scope(why, "ecosystem", root, suffixes, reach, outside, _too_many(outside))


def _holds(root: str, suffixes: tuple[str, ...], namer: str) -> bool:
    """Whether an ecosystem scope runs *namer*'s tests already: its own code or test under *root*.

    A test helper there is not held: only the tests importing it run it.
    """
    if not (_is_under(namer, root) and namer.lower().endswith(suffixes)):
        return False
    return is_runnable_test(namer) or not is_test_related_path(namer)


def _package_scope(path: str, tree: _Tree) -> Scope | None:
    root = str(PurePosixPath(path).parent)
    if root == ".":
        return None
    return Scope(
        f"every import of a module under {root}/ runs it",
        "package-importers",
        root,
        PYTHON_SUFFIXES,
        tree.code_under(root, PYTHON_SUFFIXES),
    )


def _named_scope(path: str, tree: _Tree, namers: tuple[str, ...]) -> Scope | None:
    """Code naming *path* reads it; else the package holding it is the closest owner.

    A namer inside the owning package is trusted alone. A name written only
    outside it may be another file's (``layout.css`` is a common name), so the
    owner's tests run beside the namers'. Only a doc, template, stylesheet or
    image falls back to its owner when nothing names it.
    """
    if namers and not all(is_code_file(n) for n in namers):
        return None  # a doctest glob: the doc is a test
    if not namers and is_documentation(path):
        return None  # a doc no code names needs no tests
    if too_many := _too_many(namers):
        return Scope(f"it is named by {_some(namers)}", "named-by", run_all=too_many)
    owner = tree.owner(path)
    if namers and (owner is None or any(_is_under(n, owner) for n in namers)):
        return Scope(f"it is named by {_some(namers)}", "named-by", namers=namers)
    if owner is None or not (namers or _suffix(path) in _OWNED):
        return None
    why = f"it belongs to the package at {owner}/"
    if namers:
        why += f" and is named by {_some(namers)}"
    return Scope(why, "owner-package", owner, (), tree.code_under(owner, ()), namers)


def _some(paths: tuple[str, ...]) -> str:
    return paths[0] if len(paths) == 1 else f"{paths[0]} and {len(paths) - 1} more"


def _is_under(path: str, root: str) -> bool:
    return root == "." or path.startswith(f"{root}/")
