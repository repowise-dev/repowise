"""Pick the tests a change needs to run, or say why it needs them all.

``repowise impacted-tests`` names the tests that cover or reach each changed
file. A CI job can only trust that as a subset when every part of the answer
is positively known, so :func:`select_tests` fails closed: anything it cannot
vouch for answers "run everything", and the answer always says why. A full run
is chosen when any of these hold:

1. there is no change to select from;
2. a changed or deleted file can change any test: a dependency lock or
   manifest, build or test configuration, a production package's
   ``__init__.py``, a shared test helper, CI config, Repowise's own config, or
   a path listed in ``tests.full_run_on``;
3. a changed or deleted file sits in a test tree but is not code (data, a
   snapshot, a golden file), or is a helper module no test imports;
4. a changed file is neither code the index knows nor documentation;
5. a changed code file has no known test, only a filename guess names one, or
   a route to it passes through a test helper no test imports (its users are
   unknown);
6. the index is missing, was built before other files changed, disagrees with
   itself about its commit, or its graph could not be read;
7. the per-test map hit its stored row cap;
8. a deleted code file has no known test, or a test the index names is
   missing from the checkout.

Otherwise the subset is the covering tests, the tests the graph shows reaching
the changed files (a changed test, the call graph, the import graph) and
``tests.always_run``. A test package's ``__init__.py`` or a ``conftest.py``
(changed, deleted, or on a route to a changed file) stands for every test under
its directory. A helper module tests import stands for the tests that import
it, directly or through other helpers (basis ``helper-importers``), which are the files that run it: Python runs the package file
for each module in it, and pytest loads a conftest for each test at or below
it. Only documentation (``docs/`` and the root README,
CHANGELOG, LICENSE and the like) is skipped without a test.

:func:`runner_args` renders a selection as arguments for one test runner; a
full run renders as the :data:`RUN_ALL` sentinel. pytest and go reject it as a
path; jest and vitest treat arguments as patterns and would match nothing, so
a pipeline must branch on the run-all flag rather than rely on the sentinel.
"""

from __future__ import annotations

import shlex
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import pathspec

from ..test_paths import is_test_path, is_test_related_path, is_test_support_path

#: Printed alone instead of arguments when every test must run.
RUN_ALL = ":all"

RUNNERS = ("auto", "pytest", "go", "jest", "files")

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
            "go.mod",
            "go.sum",
            "Cargo.lock",
            "Cargo.toml",
            "Gemfile.lock",
            "Gemfile",
            "composer.lock",
            "composer.json",
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
    ("shared test data can change any test", ("**/testdata/**", "**/fixtures/**")),
    ("CI configuration changed", (".github/workflows/**", ".gitlab-ci.yml")),
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

# Extensions a test runner collects tests from; anything else in a test tree
# (data, snapshots, golden files) is read by tests, not run.
_TEST_CODE_SUFFIXES = frozenset(
    {".py", ".go", ".java", ".kt", ".kts", ".scala", ".groovy", ".rb", ".cs", ".fs"}
    | {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
    | {".rs", ".php", ".swift", ".ex", ".exs", ".dart", ".c", ".cc", ".cpp"}
)

_PYTHON = (".py",)
_JS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
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
    ``changed-test``, ``call-graph``, ``import-graph``, ``filename-pattern``,
    ``unknown``, or ``none`` when nothing was asked (no index).
    """

    run_all: bool
    reasons: tuple[str, ...]
    tests: tuple[str, ...] = ()
    test_files: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()
    always_run: tuple[str, ...] = ()
    skipped_files: tuple[str, ...] = ()
    basis: Mapping[str, str] = field(default_factory=dict)

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
        }


def is_documentation(path: str) -> bool:
    """``docs/**`` or a root-level README, CHANGELOG, LICENSE and the like."""
    if _DOCS_SPEC.match_file(path):
        return True
    p = PurePosixPath(path)
    return len(p.parts) == 1 and p.name.lower().startswith(_ROOT_META)


_PACKAGE_INIT_REASON = "every import of the package runs it, and those are not all tracked"


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


def is_runnable_test(path: str) -> bool:
    """A test-shaped file name with an extension a runner collects tests from."""
    p = PurePosixPath(path)
    return p.suffix.lower() in _TEST_CODE_SUFFIXES and is_test_path(p.name)


def full_run_reason(path: str, extra: Iterable[str] = ()) -> str | None:
    """Why a change to *path* can change any test, or ``None``."""
    specs = list(_DEFAULT_SPECS)
    if extra := tuple(extra):
        spec = pathspec.PathSpec.from_lines("gitwildmatch", extra)
        specs.append(("it matches tests.full_run_on", spec))
    for why, spec in specs:
        if spec.match_file(path):
            return why
    if PurePosixPath(path).name == "__init__.py" and not is_test_related_path(path):
        return _PACKAGE_INIT_REASON
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
    expands to.
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
    triage = _triage(paths, inp.config)
    run_all = [*_empty_change_reasons(paths, inp.label), *triage.run_all]
    run_all += _index_reasons(inp, has_code=bool(triage.code))

    evidence = _Evidence.of(inp, deleted)
    per_file, file_reasons = _tests_per_file(triage.code, evidence)
    if inp.index_available:
        run_all += file_reasons
    basis = {**triage.basis, **{path: per_file[path][1] for path in triage.code}}

    tests, test_files = _runnable([t for path in triage.code for t in per_file[path][0]])
    return Selection(
        run_all=bool(run_all),
        reasons=tuple(run_all + _notes(inp, triage.skipped)),
        tests=tests,
        test_files=test_files,
        packages=_go_packages(triage.code, deleted, inp.go_test_dirs),
        always_run=inp.config.always_run,
        skipped_files=tuple(triage.skipped),
        basis=basis,
    )


def _triage(paths: list[str], config: TestSelectionConfig) -> _Triage:
    """Full-run triggers, documentation and test-tree data first; code for the tiers."""
    out = _Triage()
    for path in paths:
        if why := full_run_reason(path, config.full_run_on):
            out.run_all.append(f"{path} changed: {why}.")
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
    out = [inp.index_problem] if inp.index_problem else _gap_reasons(inp.index_gap)
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


def _notes(inp: SelectionInput, skipped: list[str]) -> list[str]:
    """Reasons that explain the selection without forcing a full run."""
    out = []
    if not inp.map_current and inp.tiers.get("covered"):
        out.append(
            "The per-test map was measured at another commit, so covering tests "
            "are matched by file, not by changed line."
        )
    if skipped:
        out.append(f"{len(skipped)} changed file(s) are documentation: no tests needed.")
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


def _gap_reasons(index_gap: Collection[str] | None) -> list[str]:
    if index_gap is None:
        return [
            "Cannot tell what changed since the index was built (its commit is not "
            "recorded, or not in this clone); run `repowise update` before selecting."
        ]
    unseen = sorted(p for p in index_gap if not is_documentation(p))
    if not unseen:
        return []
    return [
        f"The index predates {len(unseen)} changed file(s) outside this change "
        f"(e.g. {unseen[0]}), so its graph cannot see them; run `repowise update` "
        "before selecting."
    ]


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
    stale = sorted(ev.missing - ev.deleted)
    if not stale:
        return []
    return [
        f"{stale[0]} is a test the index names but the checkout does not have "
        f"({len(stale)} such), so the index is out of date; run `repowise update`."
    ]


def _file_tests(path: str, ev: _Evidence) -> tuple[list[_TestRef], str, list[str]]:
    """One changed file's tests, the evidence behind them, and any run-all reasons."""
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

    reasons = _helper_route_reasons(path, helpers, ev) + _unfiled_reasons(path, tests)
    # A deleted test needs no run of its own; the tests importing it do.
    if path in ev.deleted and is_runnable_test(path):
        basis = "deleted-test"
    if not (tests or helpers or basis in _SELF_SUFFICIENT):
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
    return kept + [(t, t) for t in _tests_under(scopes, ev.known_tests) if t not in ev.deleted]


def _own_helper_reasons(path: str, ev: _Evidence) -> list[str]:
    """A changed helper stands for its importers; one no test imports is unknown."""
    if ev.importers.get(path):
        return []
    return [
        f"{path} is a test helper no test imports; tests that use it without an "
        "import are not tracked."
    ]


def _helper_route_reasons(path: str, helpers: list[str], ev: _Evidence) -> list[str]:
    """A helper on a route stands for its importers, which the walk adds beside it.

    One no test imports is used some other way (a fixture, a plugin), so its
    users are unknown.
    """
    unimported = [h for h in helpers if not ev.importers.get(h)]
    if not unimported:
        return []
    return [
        f"{path} is reached through the test helper {unimported[0]}, and no test "
        "imports that helper; tests that use it without an import are not tracked."
    ]


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
    dirs = {str(PurePosixPath(i).parent) for i in inits}
    return [
        t for t in known_tests if any(d == "." or t.startswith(f"{d}/") for d in dirs)
    ]


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
    for via in ("changed-test", "call-graph", "import-graph", "filename-pattern"):
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


def _all_python(files: tuple[str, ...], packages: tuple[str, ...]) -> bool:
    return bool(files) and all(f.endswith(_PYTHON) for f in files)


def _all_go(files: tuple[str, ...], packages: tuple[str, ...]) -> bool:
    # A changed Go file's own package counts even with no selected test file.
    return bool(files or packages) and all(f.endswith("_test.go") for f in files)


def _all_js(files: tuple[str, ...], packages: tuple[str, ...]) -> bool:
    return bool(files) and all(f.endswith(_JS) for f in files)


# ``auto`` picks the first runner every selected test file belongs to.
_AUTO_RUNNERS = (("pytest", _all_python), ("go", _all_go), ("jest", _all_js))


def resolve_runner(selection: Selection, runner: str) -> str:
    """*runner*, or for ``auto`` the one every selected test file belongs to."""
    if runner != "auto":
        return runner
    files, packages = selection.test_files, selection.packages
    return next((name for name, fits in _AUTO_RUNNERS if fits(files, packages)), "files")


def _pytest_args(selection: Selection) -> list[str]:
    return [t for t in selection.tests if t.split("::", 1)[0].endswith(_PYTHON)]


def _go_args(selection: Selection) -> list[str]:
    go_tests = [f for f in selection.test_files if f.endswith("_test.go")]
    dirs = {str(PurePosixPath(f).parent) for f in go_tests} | set(selection.packages)
    return sorted("." if d == "." else f"./{d}" for d in dirs)


def _jest_args(selection: Selection) -> list[str]:
    return [f for f in selection.test_files if f.endswith(_JS)]


def _file_args(selection: Selection) -> list[str]:
    return list(selection.test_files)


_RUNNER_ARGS = {"pytest": _pytest_args, "go": _go_args, "jest": _jest_args}


def runner_args(selection: Selection, runner: str) -> list[str]:
    """Arguments for *runner* (already resolved); ``[RUN_ALL]`` for a full run.

    Each runner gets only the tests it can run, so a job per language can share
    one selection. ``tests.always_run`` entries are appended as written. jest
    and vitest get file paths, meant for ``--runTestsByPath``.
    """
    if selection.run_all:
        return [RUN_ALL]
    args = _RUNNER_ARGS.get(runner, _file_args)(selection)
    return list(dict.fromkeys([*args, *selection.always_run]))


def format_args(args: Iterable[str]) -> str:
    """One shell-quoted line."""
    return shlex.join(args)
