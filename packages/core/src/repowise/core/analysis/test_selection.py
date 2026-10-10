"""Pick the tests a change needs to run, or say why it needs them all.

``repowise impacted-tests`` names the tests that cover or reach each changed
file. A CI job can only trust that as a subset when every part of the answer
is positively known, so :func:`select_tests` fails closed: anything it cannot
vouch for answers "run everything", and the answer always says why. A full run
is chosen when any of these hold:

1. there is no change to select from;
2. a changed or deleted file can change any test: a dependency lock or
   manifest no ecosystem scope covers, build configuration (a Makefile, a
   Dockerfile), runtime or package manager configuration (``.nvmrc``,
   ``.tool-versions``), a shared test helper, CI config, Repowise's own
   config, or a path listed in ``tests.full_run_on``;
3. a changed or deleted file sits in a test tree but is not code (data, a
   snapshot, a golden file), or is a helper module no test imports;
4. a changed file is neither code nor documentation and has no scope (below),
   or its scope finds no test, or a file naming it has no known test;
5. a changed code file has no known test, only a filename guess names one, or
   a route to it passes through a test helper no test imports (unless every
   known test is selected anyway), or any Python
   test helper while a conftest or pytest config loads plugins by name (its
   users are unknown);
6. the index is missing, disagrees with itself about its commit, or its graph
   could not be read; or the files changed between its commit and the base are
   unknown, too many to trace, or include a manifest, lockfile or build config;
7. the per-test map hit its stored row cap;
8. a deleted code file has no known test, or a test the index names is
   missing from the checkout.

Otherwise the subset is the covering tests, the tests the graph shows reaching
the changed files (a changed test, the call graph, the import graph) and
``tests.always_run``, plus every test the graph cannot see into: one not
indexed or with no resolved edge, and one the indexer found listing and reading
files under a source directory or running the project's own command or module
in a child process (it imports one module, but exercises far more). The tests
reaching files whose imports moved between the indexed commit and the base
(:func:`plan_gap`) run too. A test package's ``__init__.py`` or a
``conftest.py`` (changed, deleted, or on a route to a changed file) stands for
every test under its directory. A helper module tests import stands for the
tests that import it, directly or through other helpers (basis
``helper-importers``), which are the files that run it: Python runs the
package file for each module in it, and pytest loads a conftest for each test
at or below it. Only documentation (``docs/`` and the root README, CHANGELOG,
LICENSE and the like, never code) that no code names is skipped without a test.

A file whose reach is known runs a scope (:mod:`.selection_scopes`) in place of
everything: a manifest or lockfile its ecosystem's tests, a production
``__init__.py`` the tests reaching the modules under it, an asset the tests
reaching the code naming it or else its package's tests.

:func:`runner_args` renders a selection as arguments for one test runner; a
full run renders as the :data:`RUN_ALL` sentinel. pytest and go reject it as a
path; jest and vitest treat arguments as patterns and would match nothing, so
a pipeline must branch on the run-all flag rather than rely on the sentinel.
"""

from __future__ import annotations

import functools
import re
import shlex
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import pathspec

from ..pytest_roots import PYTEST_CONFIG_NAMES, PytestRoots
from ..support_paths import DOC_EXTENSIONS
from ..test_paths import is_test_path, is_test_related_path, is_test_support_path

if TYPE_CHECKING:
    from .selection_scopes import Scope

#: Printed alone instead of arguments when every test must run.
RUN_ALL = ":all"

RUNNERS = ("auto", "pytest", "go", "jest", "files")

SHARED_TEST_DATA_REASON = "shared test data can change any test"

# (why it forces a full run, gitwildmatch patterns). The config extends these,
# never replaces them. Ceiling: no opt-out until someone needs one.
_FULL_RUN_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "dependencies can change any test",
        (
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            "poetry.lock",
            "uv.lock",
            "Pipfile.lock",
            "Pipfile",
            "requirements*.txt",
            "constraints*.txt",
            "requirements*.in",
            "constraints*.in",
            "**/requirements/**",
            "go.mod",
            "go.sum",
            "Cargo.lock",
            "Cargo.toml",
            "Gemfile.lock",
            "Gemfile",
            "composer.lock",
            "composer.json",
            "pnpm-workspace.yaml",
            "go.work",
            "go.work.sum",
        ),
    ),
    (
        "build or test configuration can change any test",
        (
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "package.json",
            "tsconfig*.json",
            "jest.config.*",
            "vitest.config.*",
            "vite.config.*",
            "pytest.ini",
            "tox.ini",
            "noxfile.py",
            "Makefile",
            "CMakeLists.txt",
            "build.gradle*",
            "settings.gradle*",
            "pom.xml",
            "Dockerfile*",
            "docker-compose*.y*ml",
        ),
    ),
    (
        "runtime or package manager configuration can change any test",
        (
            ".nvmrc",
            ".node-version",
            ".npmrc",
            ".yarnrc",
            ".yarnrc.yml",
            ".python-version",
            ".tool-versions",
        ),
    ),
    (SHARED_TEST_DATA_REASON, ("**/testdata/**", "**/fixtures/**")),
    (
        "CI configuration changed",
        (
            ".github/workflows/**",
            ".gitlab-ci.yml",
            "**/.gitlab-ci.yml",
            ".gitlab/**",
            ".circleci/**",
            "Jenkinsfile*",
            "azure-pipelines*.y*ml",
            ".azure-pipelines/**",
            ".buildkite/**",
            "bitbucket-pipelines.yml",
            ".travis.yml",
            ".drone.y*ml",
            ".woodpecker/**",
            ".woodpecker.y*ml",
            "appveyor.y*ml",
            ".teamcity/**",
        ),
    ),
    ("Repowise's configuration changed", (".repowise/config.yaml",)),
)

_DEFAULT_SPECS = tuple(
    (why, pathspec.PathSpec.from_lines("gitwildmatch", patterns))
    for why, patterns in _FULL_RUN_GROUPS
)

# Documentation only: ``docs/`` and root-level project meta. A doc or image
# anywhere else may be package data a test reads, so it is not skipped.
_DOCS_SPEC = pathspec.PathSpec.from_lines("gitwildmatch", ["/docs/**"])
_ROOT_META = (
    "readme",
    "changelog",
    "changes",
    "history",
    "license",
    "licence",
    "copying",
    "notice",
    "authors",
    "contributors",
    "contributing",
    "code_of_conduct",
    "security",
)
# A root file only reads as project meta with no extension or a prose one:
# ``history.json`` or ``changes.yaml`` is data a test may load.
_DOC_SUFFIXES = DOC_EXTENSIONS | {"", ".markdown"}

# Extensions a test runner collects tests from; anything else in a test tree
# (data, snapshots, golden files) is read by tests, not run.
_TEST_CODE_SUFFIXES = frozenset(
    {".py", ".go", ".java", ".kt", ".kts", ".scala", ".groovy", ".rb", ".cs", ".fs"}
    | {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
    | {".rs", ".php", ".swift", ".ex", ".exs", ".dart", ".c", ".cc", ".cpp"}
)

PYTHON_SUFFIXES = (".py",)
JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
# coverage.py names each phase of a test as its own context.
_PHASES = ("|run", "|setup", "|teardown")


@dataclass(frozen=True)
class TestSelectionConfig:
    """``tests.full_run_on`` and ``tests.always_run`` from ``.repowise/config.yaml``."""

    __test__ = False  # not a pytest class, whatever its name says

    full_run_on: tuple[str, ...] = ()
    always_run: tuple[str, ...] = ()

    @classmethod
    def from_repo_config(cls, raw: Mapping[str, Any]) -> TestSelectionConfig:
        """Parse the ``tests`` block; raises ``ValueError`` naming what is wrong.

        A misspelt key is refused rather than ignored: a full-run trigger that
        silently stops applying would skip tests the change needs.
        """
        block = raw.get("tests")
        if block is None:
            return cls()
        if not isinstance(block, Mapping):
            raise ValueError(f"tests must be a mapping, got {type(block).__name__}.")
        unknown = sorted(set(block) - {"full_run_on", "always_run"})
        if unknown:
            raise ValueError(
                f"tests.{unknown[0]} is not a setting; use tests.full_run_on or "
                "tests.always_run."
            )
        return cls(
            full_run_on=_string_list(block, "full_run_on"),
            always_run=_string_list(block, "always_run"),
        )


def _string_list(block: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = block.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"tests.{key} must be a list of non-empty strings, got {value!r}.")
    return tuple(v.strip() for v in value)


@dataclass(frozen=True)
class Selection:
    """What to run for a change, and why.

    ``tests`` are what a runner that takes test ids (pytest) is given: covering
    test ids where coverage named them, else whole test files. ``test_files``
    are the files behind them, for runners that take files. ``packages`` are
    the directories of changed Go files that hold tests of their own. ``basis``
    names, per changed or deleted file, what decided it: ``full-run``,
    ``no-tests-needed``, ``deleted-test``, ``test-tree``, ``test-package``,
    ``conftest``, ``helper-importers``, ``coverage``,
    ``changed-test``, ``call-graph``, ``import-graph``, ``conftest-fixture``
    (a test using a conftest fixture that reaches the change),
    ``conftest-import-check`` (one test that loads a conftest the change reaches
    only through its imports), ``filename-pattern``, ``unknown``, ``none`` when
    nothing was asked (no index), or a scope's ``ecosystem``,
    ``package-importers``, ``named-by`` or ``owner-package``. ``why`` says, per
    selected test file, what put it in: the changed file and evidence that
    reached it, or the reason it runs with every subset.
    """

    run_all: bool
    reasons: tuple[str, ...]
    tests: tuple[str, ...] = ()
    test_files: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()
    always_run: tuple[str, ...] = ()
    skipped_files: tuple[str, ...] = ()
    basis: Mapping[str, str] = field(default_factory=dict)
    why: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_all": self.run_all,
            "reasons": list(self.reasons),
            "tests": list(self.tests),
            "test_files": list(self.test_files),
            "packages": list(self.packages),
            "always_run": list(self.always_run),
            "skipped_files": list(self.skipped_files),
            "basis": dict(self.basis),
            "why": dict(self.why),
        }


def is_documentation(path: str) -> bool:
    """``docs/**`` or a root-level README, CHANGELOG, LICENSE and the like.

    Never code: a ``docs/conf.py``, an example a test imports, or a root
    ``history.py`` is a module like any other, so the graph decides it.
    """
    if is_code_file(path):
        return False
    if _DOCS_SPEC.match_file(path):
        return True
    p = PurePosixPath(path)
    return (
        len(p.parts) == 1
        and p.suffix.lower() in _DOC_SUFFIXES
        and p.name.lower().startswith(_ROOT_META)
    )


# ``pytest_plugins = [...]`` or ``-p name`` (not ``-p no:name``, which disables one).
_PLUGIN_DECLARATION = re.compile(r"\bpytest_plugins\b|(?:^|[\s\"'=])-p\s*(?!no:)[A-Za-z_]")


def is_scan_source(path: str) -> bool:
    """Code or pytest config: the files :func:`doc_readers` and :func:`plugin_loader` read."""
    name = PurePosixPath(path).name
    return is_code_file(path) or name in PYTEST_CONFIG_NAMES


def doc_readers(docs: Collection[str], sources: Iterable[tuple[str, str]]) -> dict[str, str]:
    """``{doc: a file naming it}`` for each of *docs* some code or config names."""
    return {doc: names[0] for doc, names in file_namers(docs, sources).items()}


def file_namers(files: Collection[str], sources: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    """``{file: the code naming it}`` for each of *files* some code or config names.

    Code that reads a file names it (``ROOT / "README.md"``), and a
    ``--doctest-glob`` turns every doc into a test, so the config declaring
    one names each doc (first). One substring test per distinct file name and
    source, and every namer is listed: a caller decides what too many means.
    Ceiling: code that globs a directory names no file.
    """
    by_name: dict[str, list[str]] = {}
    for f in files:
        by_name.setdefault(PurePosixPath(f).name, []).append(f)
    docs = [f for f in files if PurePosixPath(f).suffix.lower() in _DOC_SUFFIXES]
    found: dict[str, list[str]] = {}
    out: dict[str, list[str]] = {}
    for path, text in sources:
        # Config only for the glob, code only for names: pyproject's
        # ``readme = "README.md"`` is packaging, not a test.
        if not is_code_file(path):
            if "doctest-glob" in text:
                for doc in docs:
                    out.setdefault(doc, []).insert(0, path)
            continue
        for name in by_name:
            if name in text:
                found.setdefault(name, []).append(path)
    for name, namers in found.items():
        for f in by_name[name]:
            out.setdefault(f, []).extend(n for n in namers if n != f)
    return {f: n for f, n in out.items() if n}


def plugin_loader(sources: Iterable[tuple[str, str]]) -> str | None:
    """The first conftest or pytest config that loads a plugin module by name.

    Such a plugin's fixtures reach tests that never import it, so while one is
    declared, a test helper's importers are not all of its users.
    """
    for path, text in sources:
        name = PurePosixPath(path).name
        pytest_file = name == "conftest.py" or name in PYTEST_CONFIG_NAMES
        if pytest_file and _PLUGIN_DECLARATION.search(text):
            return path
    return None


PACKAGE_INIT_REASON = "every import of the package runs it, and those are not all tracked"


# Path predicates the selection asks hundreds of thousands of times on a large
# change (once per test per changed file); each is pure in the path.
_PATH_MEMO = 1 << 16


@functools.lru_cache(maxsize=_PATH_MEMO)
def scope_kind(path: str) -> str | None:
    """``test-package`` or ``conftest`` for a file every test under its directory runs.

    A test tree's ``__init__.py`` runs for each module in the package, and
    pytest loads a ``conftest.py`` for each test at or below its directory, so
    those tests are exactly its users.
    """
    name = PurePosixPath(path).name
    if name == "conftest.py":
        return "conftest"
    if name == "__init__.py" and is_test_related_path(path):
        return "test-package"
    return None


def is_code_file(path: str) -> bool:
    """A file with an extension a test runner loads, test or not."""
    return PurePosixPath(path).suffix.lower() in _TEST_CODE_SUFFIXES


def is_test_helper(path: str) -> bool:
    """Code in a test tree that is neither a test nor a directory-scoped file."""
    return (
        is_code_file(path)
        and is_test_related_path(path)
        and not is_runnable_test(path)
        and scope_kind(path) is None
    )


def is_runnable_test(path: str, roots: PytestRoots | None = None) -> bool:
    """A test-shaped file name with an extension a runner collects tests from.

    With *roots*, a Python file pytest's config leaves out of collection
    (``core/test_paths.py``) is not one: the same rule that stamps ``is_test``.
    """
    return _runnable_name(path) and (roots is None or is_test_path(path, roots=roots))


@functools.lru_cache(maxsize=_PATH_MEMO)
def _runnable_name(path: str) -> bool:
    p = PurePosixPath(path)
    return p.suffix.lower() in _TEST_CODE_SUFFIXES and is_test_path(p.name)


@functools.lru_cache(maxsize=8)
def full_run_on_spec(patterns: tuple[str, ...]) -> pathspec.PathSpec:
    """``tests.full_run_on`` compiled once per pattern list, not once per path."""
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def full_run_reason(path: str, extra: Iterable[str] = ()) -> str | None:
    """Why a change to *path* can change any test, or ``None``."""
    specs = list(_DEFAULT_SPECS)
    if extra := tuple(extra):
        specs.append(("it matches tests.full_run_on", full_run_on_spec(extra)))
    for why, spec in specs:
        if spec.match_file(path):
            return why
    if PurePosixPath(path).name == "__init__.py" and not is_test_related_path(path):
        return PACKAGE_INIT_REASON
    if is_test_support_path(path) and scope_kind(path) is None:
        return "a shared test helper can change any test"
    return None


@dataclass(frozen=True)
class SelectionInput:
    """Everything :func:`select_tests` decides from.

    *changed* are the paths present after the change, *deleted* those it
    removed, *label* names the change; *tiers* is ``impacted-tests``' result
    (``covered``, ``inferred``, ``unknown``, ``helper_importers``).
    *map_current* is false when the per-test map was measured at neither end of
    the change, so its tests were matched by file; *map_truncated* when it holds
    as many rows as the store keeps. *index_gap* lists the files that changed
    between the indexed commit and the change, ``None`` when that cannot be
    told; *index_problem* says why the index's own commit cannot be trusted.
    *graph_error* names a graph read that failed. *missing* are test files the
    tiers name that the checkout does not have; *go_test_dirs* the directories
    holding ``_test.go`` files; *known_tests* every runnable test in the
    checkout, which a test package's ``__init__.py`` or a ``conftest.py``
    expands to. *doc_readers* maps a changed doc to a file naming it
    (:func:`doc_readers`); *plugin_loader* is a file loading pytest plugins by
    name (:func:`plugin_loader`); *unplaced_tests* are tests the graph cannot
    see into (not indexed, or with no resolved edge), and *always_run_tests*
    maps each test the indexer found walking the source tree or running the
    project in a child process to why; every subset runs both.
    """

    changed: Collection[str]
    deleted: Collection[str]
    tiers: Mapping[str, Any]
    config: TestSelectionConfig = field(default_factory=TestSelectionConfig)
    index_available: bool = True
    label: str = "the base"
    map_current: bool = True
    map_truncated: bool = False
    index_gap: Collection[str] | None = ()
    index_problem: str | None = None
    graph_error: str | None = None
    missing: Collection[str] = ()
    go_test_dirs: Collection[str] = ()
    known_tests: Collection[str] = ()
    doc_readers: Mapping[str, str] = field(default_factory=dict)
    plugin_loader: str | None = None
    unplaced_tests: Collection[str] = ()
    # :func:`plan_gap` for *index_gap*, with the targets whose rows *tiers* also
    # holds; None when the caller did not trace it, so any code there runs all.
    gap: GapPlan | None = None
    always_run_tests: Mapping[str, str] = field(default_factory=dict)
    # :func:`~.selection_scopes.trigger_scopes`' answer; *tiers* holds each route's rows.
    scopes: Mapping[str, Scope] = field(default_factory=dict)


@dataclass
class _Triage:
    """The changed paths sorted by what decides them, before any test is asked."""

    run_all: list[str] = field(default_factory=list)
    basis: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    code: list[str] = field(default_factory=list)


def select_tests(inp: SelectionInput) -> Selection:
    """Decide what a change must run (see the module docstring for the rules)."""
    deleted = set(inp.deleted)
    paths = sorted(set(inp.changed) | deleted)
    scoped = [p for p in paths if p in inp.scopes]
    triage = _triage([p for p in paths if p not in inp.scopes], inp.config, inp.doc_readers)
    run_all = [*_empty_change_reasons(paths, inp.label), *triage.run_all]
    walks = bool(triage.code) or any(inp.scopes[p].routes for p in scoped)
    run_all += _index_reasons(inp, has_code=walks)

    evidence = _Evidence.of(inp, deleted)
    per_file, file_reasons = _tests_per_file(triage.code, evidence)
    traced = bool(triage.code and inp.gap and inp.gap.targets)
    gap_tests, route_reasons, gap_why = (
        _gap_route_tests(inp.gap.targets, paths, evidence) if traced else ([], [], {})
    )
    in_scope, scope_reasons, scope_notes, scope_why = _scoped_tests(
        scoped, inp.scopes, evidence
    )
    run_all += scope_reasons
    if inp.index_available:
        run_all += file_reasons + route_reasons
    basis = {**triage.basis, **{path: per_file[path][1] for path in triage.code}}
    basis.update({path: inp.scopes[path].basis for path in scoped})

    # The graph cannot say what these tests reach, so they always run.
    always = _always_running(inp) if triage.code or scoped else {}
    tests, test_files = _runnable(
        [
            *(t for path in triage.code for t in per_file[path][0]),
            *gap_tests,
            *in_scope,
            *((t, t) for t in always),
        ]
    )
    notes = _notes(inp, triage.skipped, always, traced)
    return Selection(
        run_all=bool(run_all),
        reasons=tuple(run_all + evidence.notes + scope_notes + notes),
        tests=tests,
        test_files=test_files,
        packages=_go_packages(triage.code, deleted, inp.go_test_dirs),
        always_run=inp.config.always_run,
        skipped_files=tuple(triage.skipped),
        basis=basis,
        why={**scope_why, **gap_why, **_why(triage.code, per_file, evidence), **always},
    )


_UNPLACED_REASON = "the graph has no edge from it into the repository's code"
_EVERY_SUBSET = "runs with every subset: "


def _always_running(inp: SelectionInput) -> dict[str, str]:
    """``{test file: why it runs with every subset}``; a detected reason wins.

    A detected test the checkout no longer has is left out, like any test.
    """
    known = set(inp.known_tests)
    out = {t: f"{_EVERY_SUBSET}{_UNPLACED_REASON}" for t in inp.unplaced_tests}
    for test, reason in sorted(inp.always_run_tests.items()):
        if not known or test in known:
            out[test] = f"{_EVERY_SUBSET}{reason}"
    return out


def _why(
    code: list[str], per_file: Mapping[str, tuple[list[_TestRef], str]], ev: _Evidence
) -> dict[str, str]:
    """``{test file: the first changed file that selected it, and the evidence}``.

    Only the first: a test several changed files reach names the first of them
    in sorted order.
    """
    out: dict[str, str] = {}
    for path in code:
        tests, basis = per_file[path]
        vias = {f: via for f, via in ev.inferred.get(path, ())}
        covered = {f for _, f in ev.covered.get(path, ())}
        for _, test_file in tests:
            if test_file and test_file not in out:
                via = "coverage" if test_file in covered else vias.get(test_file, basis)
                out[test_file] = f"{path} changed ({_VIA_WHY.get(via, via)})"
    return out


# The two vias a narrowed conftest route adds (``conftest_routes``), spelled out.
_VIA_WHY = {
    "conftest-fixture": (
        "conftest-fixture: it asks for a conftest fixture whose code runs into the change"
    ),
    "conftest-import-check": (
        "conftest-import-check: the change reaches a conftest above it only through the "
        "conftest's imports, and this test is that conftest's import check"
    ),
}


def selected_by_change(selection: Selection, test: str) -> bool:
    """Whether a changed file (not a rule) put *test* in the selection."""
    why = selection.why.get(test.split("::", 1)[0])
    return bool(why) and not why.startswith(_EVERY_SUBSET)


def explain_test(selection: Selection, test: str) -> tuple[bool, list[str]]:
    """Whether *test* (a file or node id) runs for this selection, and why, in plain lines."""
    test_file = test.split("::", 1)[0]
    if selection.run_all:
        return True, ["Every test runs:", *selection.reasons]
    if why := selection.why.get(test_file):
        return True, [f"Selected: {why}."]
    if test in selection.always_run or test_file in selection.always_run:
        return True, ["Selected: it is listed in tests.always_run."]
    return False, [
        "Not selected: no changed file reaches it through coverage, the call graph or "
        "the import graph, and it is not a test that runs with every subset."
    ]


def _triage(
    paths: list[str], config: TestSelectionConfig, doc_readers: Mapping[str, str]
) -> _Triage:
    """Full-run triggers, documentation and test-tree data first; code for the tiers."""
    out = _Triage()
    for path in paths:
        if why := full_run_reason(path, config.full_run_on):
            out.run_all.append(f"{path} changed: {why}.")
            out.basis[path] = "full-run"
        elif is_documentation(path) and (reader := doc_readers.get(path)):
            out.run_all.append(
                f"{path} is named by {reader}, so a test may read it; the tests "
                "that do are not tracked."
            )
            out.basis[path] = "full-run"
        elif is_documentation(path):
            out.skipped.append(path)
            out.basis[path] = "no-tests-needed"
        elif is_test_related_path(path) and not is_code_file(path):
            out.run_all.append(
                f"{path} is in a test tree but is not code (data, a snapshot or a "
                "golden file); the tests that read it are not tracked."
            )
            out.basis[path] = "test-tree"
        else:
            out.code.append(path)
    return out


def _scoped_tests(
    scoped: list[str], scopes: Mapping[str, Scope], ev: _Evidence
) -> tuple[list[_TestRef], list[str], list[str], dict[str, str]]:
    """Each scoped file's tests, the run-all reasons found, a note per file, and ``why``.

    A route shared by several files (one namer, one package) is decided once.
    """
    routes: dict[str, tuple[list[_TestRef], list[str]]] = {}
    tests: list[_TestRef] = []
    reasons: dict[str, None] = {}
    notes: list[str] = []
    why_run: dict[str, str] = {}
    for path in scoped:
        scope = scopes[path]
        if scope.run_all:
            reasons[f"{path} changed: {scope.run_all}."] = None
            continue
        mine = [(t, t) for t in _scope_tests(scope, ev)]
        before = len(reasons)
        for route in scope.routes:
            if route not in routes:
                found, _, why = _file_tests(route, ev, route_only=True)
                routes[route] = (found, why)
            found, why = routes[route]
            mine += found
            reasons.update(dict.fromkeys(f"{path} changed; {r}" for r in why))
            if route in scope.namers and not (found or why):
                reasons[f"{path} is named by {route}, and no test is known to reach it."] = None
        if not mine and len(reasons) == before:
            reasons[f"{path} changed: {scope.why}, and no such test is known."] = None
        tests += mine
        files = {f for _, f in mine if f}
        for f in sorted(files - set(why_run)):
            why_run[f] = f"{path} changed ({scope.basis}: {scope.why})"
        notes.append(f"{path} changed: {scope.why}; {len(files)} test file(s) run for it.")
    if len(notes) > _MAX_SCOPE_NOTES:
        more = len(notes) - _MAX_SCOPE_NOTES
        notes = [*notes[:_MAX_SCOPE_NOTES], f"{more} more changed file(s) run a scoped set."]
    return tests, list(reasons), notes, why_run


_MAX_SCOPE_NOTES = 5


def _scope_tests(scope: Scope, ev: _Evidence) -> list[str]:
    """Known tests under *scope*'s root in its suffixes; a test this change deletes is gone."""
    if scope.root is None:
        return []
    return [
        t
        for t in _known_under(ev, {scope.root})
        if (not scope.suffixes or t.lower().endswith(scope.suffixes)) and t not in ev.deleted
    ]


def _empty_change_reasons(paths: list[str], label: str) -> list[str]:
    return [] if paths else [f"No change against {label}; nothing to select from."]


def _index_reasons(inp: SelectionInput, *, has_code: bool) -> list[str]:
    """Why the index cannot vouch for the tiers; nothing to say without code."""
    if not has_code:
        return []
    if not inp.index_available:
        return [
            "No index: nothing records which tests reach the changed code "
            "(run `repowise init`, or restore a cached .repowise directory)."
        ]
    out = [inp.index_problem] if inp.index_problem else _gap_reasons(inp)
    if inp.graph_error:
        out.append(
            f"The graph could not be read ({inp.graph_error}), so the tests reaching "
            "the changed files are unknown."
        )
    if inp.map_truncated:
        out.append(
            "The per-test map is at its stored row cap, so some tests' coverage "
            "was dropped when it was ingested."
        )
    return out


def _notes(
    inp: SelectionInput, skipped: list[str], always: Mapping[str, str], traced: bool
) -> list[str]:
    """Reasons that explain the selection without forcing a full run."""
    out = []
    if traced:
        rewired = len(inp.gap.rewired) if inp.gap else 0
        out.append(
            f"The index predates {rewired} changed file(s) outside this change; "
            "the tests reaching them run too."
        )
    if always:
        detected = sorted(t for t in always if t in inp.always_run_tests)
        unseen = len(always) - len(detected)
        if unseen:
            example = sorted(t for t in always if t not in inp.always_run_tests)[0]
            out.append(
                f"{unseen} test file(s) the graph cannot see into run with every selection "
                f"(e.g. {example})."
            )
        if detected:
            out.append(
                f"{len(detected)} test file(s) that list source files or run the project in "
                f"a child process run with every selection (e.g. {detected[0]}: "
                f"{inp.always_run_tests[detected[0]]})."
            )
    if not inp.map_current and inp.tiers.get("covered"):
        out.append(
            "The per-test map was measured at another commit, so covering tests "
            "are matched by file, not by changed line."
        )
    if skipped:
        out.append(f"{len(skipped)} changed file(s) are documentation: no tests needed.")
    # How each conftest on a route was decided (``conftest_routes``).
    out.extend(inp.tiers.get("conftest_notes") or ())
    return out


def _go_packages(
    code: list[str], deleted: set[str], go_test_dirs: Collection[str]
) -> tuple[str, ...]:
    """Directories of changed Go files that hold same-package tests."""
    dirs = {
        str(PurePosixPath(p).parent)
        for p in code
        if p.endswith(".go") and not p.endswith("_test.go") and p not in deleted
    }
    return tuple(sorted(dirs & set(go_test_dirs)))


def _gap_reasons(inp: SelectionInput) -> list[str]:
    if inp.index_gap is None:
        return [
            "Cannot tell what changed since the index was built (its commit is not "
            "recorded, or not in this clone); run `repowise update` before selecting."
        ]
    if inp.gap is not None:
        return list(inp.gap.reasons)
    unseen = sorted(p for p in inp.index_gap if not is_documentation(p))
    if not unseen:
        return []
    return [
        f"The index predates {len(unseen)} changed file(s) outside this change "
        f"(e.g. {unseen[0]}), so its graph cannot see them; run `repowise update` "
        "before selecting."
    ]


# Past this many files changed since the index was built, tracing them would
# select nearly every test, so a full run is cheaper to decide. Ceiling: a file
# count, not a measure of how much of the suite they reach.
MAX_INDEX_GAP = 1000

# Read by tests or CI, never imported, so they cannot add a route to a change.
_GAP_INERT = frozenset({"CI configuration changed", "shared test data can change any test"})
_HELPER_REASON = "a shared test helper can change any test"
_GAP_TRACED = (None, "route", "package")


def _gap_kind(path: str, config: TestSelectionConfig) -> str | None:
    """``"route"``, ``"package"``, ``None`` (cannot reach a change), or a full-run reason."""
    if is_documentation(path):
        return None
    why = full_run_reason(path, config.full_run_on)
    if why == PACKAGE_INIT_REASON:
        return "package"
    if why is None or why == _HELPER_REASON or (why in _GAP_INERT and is_code_file(path)):
        return "route"
    return None if why in _GAP_INERT else why


@dataclass(frozen=True)
class GapPlan:
    """What the files changed between the indexed commit and the base mean for selection.

    *reasons* say why they force a full run, if they do. Otherwise
    *candidates* are the code files among them whose edges may have moved,
    *packages* the production ``__init__.py`` files among those, and, once
    :func:`with_rewired` has run, *rewired* the candidates whose edges did move
    and *targets* the files whose tests join the selection.
    """

    reasons: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    packages: frozenset[str] = frozenset()
    rewired: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()


def plan_gap(
    index_gap: Collection[str], config: TestSelectionConfig, change: Collection[str] = ()
) -> GapPlan:
    """Classify each file changed since the index was built, once.

    Nothing is traced when a file in *change* already forces a full run.

    A full run is forced when there are more than :data:`MAX_INDEX_GAP` of them
    (documentation aside), or when one is configuration that decides how every
    import resolves or what is built (a manifest, a lockfile, build or test
    config, ``tests.full_run_on``). Data and prose import nothing, so only code
    is a candidate.
    """
    from .import_drift import may_carry_edges

    counted = sorted(p for p in index_gap if not is_documentation(p))
    if len(counted) > MAX_INDEX_GAP:
        return GapPlan(
            reasons=(
                f"The index predates {len(counted)} changed file(s) outside this change "
                f"(e.g. {counted[0]}), more than the {MAX_INDEX_GAP} worth tracing; "
                "run `repowise update` before selecting.",
            )
        )
    kinds = {p: _gap_kind(p, config) for p in counted}
    triggers = [(p, k) for p, k in kinds.items() if k not in _GAP_TRACED]
    if triggers:
        path, why = triggers[0]
        return GapPlan(
            reasons=(
                f"{path} changed after the index was built ({len(triggers)} such): {why}; "
                "run `repowise update` before selecting.",
            )
        )
    if any(full_run_reason(p, config.full_run_on) for p in change):
        return GapPlan()
    candidates = tuple(p for p, k in kinds.items() if k is not None and may_carry_edges(p))
    return GapPlan(
        candidates=candidates,
        packages=frozenset(p for p in candidates if kinds[p] == "package"),
    )


def with_rewired(
    plan: GapPlan, rewired: Collection[str], indexed_files: Collection[str]
) -> GapPlan:
    """*plan* with the candidates whose edges moved, and the files standing for them.

    A route the index cannot see from a test to the change must pass through a
    rewired file: up to the first such file on it, every file kept its edges,
    so the index has that part of the route. Selecting the tests that reach
    each rewired file therefore covers every new route. A production
    ``__init__.py`` runs for every module under its directory, an import the
    graph does not record, so it stands for all of them. Ceiling: an unchanged
    file whose import starts resolving to a file added since is not traced.
    """
    out = set(rewired)
    for path in set(rewired) & plan.packages:
        out.update(_files_under(path, indexed_files))
    return replace(plan, rewired=tuple(sorted(rewired)), targets=tuple(sorted(out)))


def _files_under(init: str, files: Collection[str]) -> list[str]:
    parent = str(PurePosixPath(init).parent)
    return [f for f in files if parent == "." or f.startswith(f"{parent}/")]


def _gap_route_tests(
    targets: Collection[str], paths: list[str], ev: _Evidence
) -> tuple[list[_TestRef], list[str], dict[str, str]]:
    """The tests reaching each gap target, run-all reasons found on those routes, and why each runs."""
    tests: list[_TestRef] = []
    reasons: list[str] = []
    why: dict[str, str] = {}
    for path in sorted(set(targets) - set(paths)):
        found, _, route_reasons = _file_tests(path, ev, route_only=True)
        tests += found
        reasons += [f"Changed after the index was built: {r}" for r in route_reasons]
        for _, test_file in found:
            if test_file:
                why.setdefault(test_file, f"{path} changed after the index was built")
    return tests, reasons, why


_TestRef = tuple[str, str | None]  # (test id or file, the file it lives in)

# Bases that need no test of their own to be safe.
_SELF_SUFFICIENT = ("deleted-test", "test-package", "conftest")


@dataclass(frozen=True)
class _Evidence:
    """The tiers indexed by changed file, and what the checkout lacks."""

    covered: Mapping[str, list[_TestRef]]
    inferred: Mapping[str, list[tuple[str, str]]]
    unknown: frozenset[str]
    importers: Mapping[str, Collection[str]]
    missing: frozenset[str]
    deleted: frozenset[str]
    known_tests: tuple[str, ...]
    plugin_loader: str | None
    gap: frozenset[str] = frozenset()
    # Tests every subset runs (unplaced and detected always-run).
    every_subset: frozenset[str] = frozenset()
    # Explanations that do not force a full run, gathered while deciding.
    notes: list[str] = field(default_factory=list)
    # Known tests under each directory set (:func:`_known_under`), per selection.
    under_memo: dict[frozenset[str], list[str]] = field(default_factory=dict, compare=False)

    @classmethod
    def of(cls, inp: SelectionInput, deleted: set[str]) -> _Evidence:
        covered: dict[str, list[_TestRef]] = {}
        for test_id, info in (inp.tiers.get("covered") or {}).items():
            for source in info.get("source_files", ()):
                covered.setdefault(source, []).append((test_id, info.get("test_file") or None))
        inferred: dict[str, list[tuple[str, str]]] = {}
        for row in inp.tiers.get("inferred") or ():
            inferred.setdefault(row["source_file"], []).append((row["test_file"], row["via"]))
        return cls(
            covered=covered,
            inferred=inferred,
            unknown=frozenset(inp.tiers.get("unknown") or ()),
            importers=inp.tiers.get("helper_importers") or {},
            missing=frozenset(inp.missing),
            deleted=frozenset(deleted),
            known_tests=tuple(inp.known_tests),
            plugin_loader=inp.plugin_loader,
            gap=frozenset(inp.index_gap or ()),
            every_subset=frozenset(inp.unplaced_tests) | frozenset(inp.always_run_tests),
        )

    def found(self, path: str) -> list[_TestRef]:
        """What the graph named for *path*, a name-shaped guess excluded."""
        return [(t, t) for t, via in self.inferred.get(path, ()) if via != "filename-pattern"]

    def guesses(self, path: str) -> list[str]:
        return [t for t, via in self.inferred.get(path, ()) if via == "filename-pattern"]

    def present(self, tests: list[_TestRef]) -> list[_TestRef]:
        """*tests* the checkout still has; a test this change deletes is gone."""
        return [(t, f) for t, f in tests if f not in self.missing and f not in self.deleted]


def _tests_per_file(
    code: list[str], ev: _Evidence
) -> tuple[dict[str, tuple[list[_TestRef], str]], list[str]]:
    """``{path: ([(test id or file, test file)], basis)}`` and the run-all reasons."""
    reasons = _stale_index_reasons(ev)
    out: dict[str, tuple[list[_TestRef], str]] = {}
    for path in code:
        tests, basis, file_reasons = _file_tests(path, ev)
        out[path] = (tests, basis)
        reasons += file_reasons
    return out, reasons


def _stale_index_reasons(ev: _Evidence) -> list[str]:
    # A missing test that changed since the index was built was deleted then.
    stale = sorted(ev.missing - ev.deleted - ev.gap)
    if not stale:
        return []
    return [
        f"{stale[0]} is a test the index names but the checkout does not have "
        f"({len(stale)} such), so the index is out of date; run `repowise update`."
    ]


def _file_tests(
    path: str, ev: _Evidence, *, route_only: bool = False
) -> tuple[list[_TestRef], str, list[str]]:
    """One changed file's tests, the evidence behind them, and any run-all reasons.

    With *route_only* the file only links tests to the change (it changed before
    the base), so having no test of its own is not a reason.
    """
    found = ev.found(path)
    tests = ev.present([*ev.covered.get(path, ()), *found])
    basis = _basis(path, ev.covered, ev.inferred, ev.unknown)
    scopes, basis = _scope_files(path, found, basis)
    tests = _expand_scopes(tests, scopes, ev)
    helpers = sorted(
        {f for _, f in found if not is_runnable_test(f)} - ev.deleted - scopes - {path}
    )
    drop = {*helpers, path} if is_test_helper(path) else set(helpers)
    tests = [(t, f) for t, f in tests if f is None or f not in drop]
    if is_test_helper(path):
        return tests, "helper-importers", _own_helper_reasons(path, ev)

    reasons = _helper_route_reasons(path, helpers, tests, ev) + _unfiled_reasons(path, tests)
    # A deleted test needs no run of its own; the tests importing it do.
    if path in ev.deleted and is_runnable_test(path):
        basis = "deleted-test"
    if not (route_only or tests or helpers or basis in _SELF_SUFFICIENT):
        reasons.append(_no_test_reason(path, basis, ev))
    return tests, basis, reasons


def _scope_files(path: str, found: list[_TestRef], basis: str) -> tuple[set[str], str]:
    """Test-package ``__init__.py`` / ``conftest.py`` files on *path*'s routes, or itself.

    Each runs for every test under its directory, so it stands for those tests
    rather than for itself.
    """
    scopes = {f for _, f in found if f and scope_kind(f)}
    if kind := scope_kind(path):
        scopes.add(path)
        basis = kind
    return scopes, basis


def _expand_scopes(tests: list[_TestRef], scopes: set[str], ev: _Evidence) -> list[_TestRef]:
    kept = [(t, f) for t, f in tests if f not in scopes]
    dirs = {str(PurePosixPath(i).parent) for i in scopes}
    return kept + [(t, t) for t in _known_under(ev, dirs) if t not in ev.deleted]


def _known_under(ev: _Evidence, dirs: Collection[str]) -> list[str]:
    """Known tests below any of *dirs*, worked out once per directory set per selection."""
    key = frozenset(dirs)
    if key not in ev.under_memo:
        ev.under_memo[key] = _under(key, ev.known_tests)
    return ev.under_memo[key]


def _own_helper_reasons(path: str, ev: _Evidence) -> list[str]:
    """A changed helper stands for its importers; one no test imports is unknown."""
    if plugin := _plugin_reason(path, ev):
        return [plugin]
    if ev.importers.get(path):
        return []
    return [
        f"{path} is a test helper no test imports; tests that use it without an "
        "import are not tracked."
    ]


def _plugin_reason(helper: str, ev: _Evidence) -> str | None:
    """A Python helper may be a plugin, whose fixtures reach tests that never import it."""
    if not (ev.plugin_loader and helper.endswith(PYTHON_SUFFIXES)):
        return None
    return (
        f"{helper} is a test helper and {ev.plugin_loader} loads pytest plugins by "
        "name, so tests that use its fixtures without an import are not tracked."
    )


def _helper_route_reasons(
    path: str, helpers: list[str], tests: list[_TestRef], ev: _Evidence
) -> list[str]:
    """A helper on a route stands for its importers, which the walk adds beside it.

    One no test imports is used some other way (a fixture, a plugin), so its
    users are unknown, unless every known test already runs for *path*: then
    no user it could have is left out. Any language counts, since a test can
    start a helper in another language by path.
    """
    plugins = [r for h in helpers if (r := _plugin_reason(h, ev))]
    if plugins:
        return [f"{path} is reached through a test helper: {plugins[0]}"]
    unimported = [h for h in helpers if not ev.importers.get(h)]
    if unimported and _all_tests_selected(tests, ev):
        ev.notes.extend(
            f"{path} is reached through the test helper {h}, which no test imports, "
            "but every known test already runs for that change."
            for h in unimported
        )
        return []
    if not unimported:
        return []
    return [
        f"{path} is reached through the test helper {unimported[0]}, and no test "
        "imports that helper; tests that use it without an import are not tracked."
    ]


def _all_tests_selected(tests: list[_TestRef], ev: _Evidence) -> bool:
    """Whether *tests*, with the tests every selection runs, hold every known test."""
    selected = {f for _, f in tests if f} | ev.every_subset
    return bool(ev.known_tests) and set(ev.known_tests) <= selected


def _unfiled_reasons(path: str, tests: list[_TestRef]) -> list[str]:
    return [
        f"Coverage names {test_id} for {path}, but not its file."
        for test_id, test_file in tests
        if test_file is None
    ]


def _no_test_reason(path: str, basis: str, ev: _Evidence) -> str:
    """Why a changed file with no test left forces a full run."""
    if path in ev.deleted:
        return f"{path} was deleted and no test is known to have used it."
    if guesses := ev.guesses(path):
        return f"{path}: only a filename guess names a test ({guesses[0]})."
    if basis in ("unknown", "none"):
        return f"{path}: no coverage, no test reaching it in the graph, no paired test."
    return f"{path}: its only known tests are not in the checkout."


def _tests_under(inits: Collection[str], known_tests: Collection[str]) -> list[str]:
    """Every known test below the directory of each scope file."""
    return _under({str(PurePosixPath(i).parent) for i in inits}, known_tests)


def _under(dirs: Collection[str], files: Collection[str]) -> list[str]:
    return [f for f in files if any(d == "." or f.startswith(f"{d}/") for d in dirs)]


def expand_test_scopes(tests: Iterable[str], test_files: Collection[str]) -> list[str]:
    """*tests* as a run list: each scope file replaced by the tests under its directory.

    A graph walk reports a ``conftest.py`` or test-package ``__init__.py`` it
    stopped at, but pytest collects nothing from either; the tests they run for
    are the runnable ones among *test_files* below their directory, the same
    expansion the selection makes. Order is kept and duplicates dropped; a scope
    with no runnable test under it drops out.
    """
    tests = list(tests)
    if not any(scope_kind(t) for t in tests):
        return list(dict.fromkeys(tests))
    runnable = sorted(t for t in test_files if is_runnable_test(t))
    out: dict[str, None] = {}
    for test in tests:
        for expanded in _tests_under([test], runnable) if scope_kind(test) else (test,):
            out.setdefault(expanded)
    return list(out)


_VIAS = (
    "changed-test",
    "call-graph",
    "import-graph",
    "conftest-fixture",
    "conftest-import-check",
    "filename-pattern",
)


def _basis(
    path: str,
    covered: Mapping[str, Any],
    inferred: Mapping[str, list[tuple[str, str]]],
    unknown: set[str],
) -> str:
    """The strongest evidence behind *path*'s tests."""
    if path in covered:
        return "coverage"
    vias = {via for _, via in inferred.get(path, ())}
    for via in _VIAS:
        if via in vias:
            return via
    return "unknown" if path in unknown else "none"


def _runnable(selected: list[_TestRef]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(tests, test_files)``: node ids and whole files, deduplicated, stable.

    A node id whose whole file is selected anyway is dropped.
    """
    files: dict[str, None] = {}
    whole: set[str] = set()
    ids: dict[str, None] = {}
    for test, test_file in selected:
        if test_file is None:
            continue
        files[test_file] = None
        node = _node_id(test, test_file)
        if node == test_file:
            whole.add(test_file)
        ids[node] = None
    tests = [t for t in ids if t in whole or t.split("::", 1)[0] not in whole]
    return tuple(tests), tuple(files)


def _node_id(test_id: str, test_file: str) -> str:
    """A runnable id: ``file::name`` rebased on the resolved file, else the file."""
    for phase in _PHASES:
        if test_id.endswith(phase):
            test_id = test_id[: -len(phase)]
            break
    if "::" not in test_id:
        return test_file
    return f"{test_file}::{test_id.split('::', 1)[1]}"


# The test files each runner takes; ``files`` takes every one.
_RUNNER_SUFFIXES = {"pytest": PYTHON_SUFFIXES, "go": (".go",), "jest": JS_SUFFIXES}


def _takes(runner: str, test: str) -> bool:
    """Whether *test* (a file or node id) is in *runner*'s language; case-blind."""
    return test.split("::", 1)[0].lower().endswith(_RUNNER_SUFFIXES[runner])


def _all_python(files: tuple[str, ...], packages: tuple[str, ...]) -> bool:
    return bool(files) and all(_takes("pytest", f) for f in files)


def _all_go(files: tuple[str, ...], packages: tuple[str, ...]) -> bool:
    # A changed Go file's own package counts even with no selected test file.
    return bool(files or packages) and all(f.endswith("_test.go") for f in files)


def _all_js(files: tuple[str, ...], packages: tuple[str, ...]) -> bool:
    return bool(files) and all(_takes("jest", f) for f in files)


# ``auto`` picks the first runner every selected test file belongs to.
_AUTO_RUNNERS = (("pytest", _all_python), ("go", _all_go), ("jest", _all_js))


def resolve_runner(selection: Selection, runner: str) -> str:
    """*runner*, or for ``auto`` the one every selected test file belongs to."""
    if runner != "auto":
        return runner
    files, packages = selection.test_files, selection.packages
    return next((name for name, fits in _AUTO_RUNNERS if fits(files, packages)), "files")


def _pytest_args(selection: Selection) -> list[str]:
    return [t for t in selection.tests if _takes("pytest", t)]


def _go_args(selection: Selection) -> list[str]:
    go_tests = [f for f in selection.test_files if f.endswith("_test.go")]
    dirs = {str(PurePosixPath(f).parent) for f in go_tests} | set(selection.packages)
    return sorted("." if d == "." else f"./{d}" for d in dirs)


def _jest_args(selection: Selection) -> list[str]:
    return [f for f in selection.test_files if _takes("jest", f)]


def _file_args(selection: Selection) -> list[str]:
    return list(selection.test_files)


_RUNNER_ARGS = {"pytest": _pytest_args, "go": _go_args, "jest": _jest_args}


def runner_args(selection: Selection, runner: str) -> list[str]:
    """Arguments for *runner* (already resolved); ``[RUN_ALL]`` for a full run.

    Each runner gets only the tests it can run, so a job per language can share
    one selection; :func:`runner_notes` names the rest. ``tests.always_run``
    entries are appended as written, but one in another runner's language is
    left to that runner. jest and vitest get file paths, meant for
    ``--runTestsByPath``.
    """
    if selection.run_all:
        return [RUN_ALL]
    args = _RUNNER_ARGS.get(runner, _file_args)(selection)
    always = [t for t in selection.always_run if _runs(t, runner)]
    return list(dict.fromkeys([*args, *always]))


def _runs(test: str, runner: str) -> bool:
    """Whether *runner* can run *test*; a directory or glob it cannot tell is kept."""
    if runner not in _RUNNER_SUFFIXES or not is_code_file(test.split("::", 1)[0]):
        return True
    return _takes(runner, test)


def left_out(selection: Selection, runner: str) -> dict[str, list[str]]:
    """Selected tests *runner*'s arguments leave out, keyed by the runner that takes them.

    A test no named runner takes (Kotlin, Swift) is keyed ``files``. Empty on a
    full run, where every runner runs everything of its own.
    """
    if selection.run_all:
        return {}
    out: dict[str, list[str]] = {}
    for test in dict.fromkeys((*selection.test_files, *selection.always_run)):
        if not _runs(test, runner):
            owner = next((r for r in _RUNNER_SUFFIXES if _takes(r, test)), "files")
            out.setdefault(owner, []).append(test)
    return out


def runner_notes(selection: Selection, runner: str) -> list[str]:
    """One line naming what :func:`left_out` found, so nothing is dropped silently.

    A pipeline running one runner needs a job per runner for these.
    """
    left = left_out(selection, runner)
    if not left:
        return []
    counts = ", ".join(f"{len(tests)} for {owner}" for owner, tests in sorted(left.items()))
    first = next(iter(left.values()))[0]
    return [
        f"Selected tests for other runners are not in these {runner} arguments ({counts}; "
        f"e.g. {first}); run them in a job per runner."
    ]


def format_args(args: Iterable[str]) -> str:
    """One shell-quoted line."""
    return shlex.join(args)
