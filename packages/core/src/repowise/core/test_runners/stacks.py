"""Coverage stacks: what each build unit runs to write a coverage report repowise reads.

The one table of coverage commands. A :class:`Stack` knows, for one unit of a
repository (the root package, a workspace member, a Go module), its test command
with coverage, the tools that command needs first, where it writes the report,
and how to add coverage to a test command a CI workflow already runs
(:meth:`Stack.edit`). Every command writes a format repowise parses: lcov,
Cobertura, JaCoCo, Clover or a Go cover profile.

:func:`detect_stacks` is pure: it takes a tree listing and the text of the
manifests :func:`files_to_read` names, so a caller holding a remote tree and no
checkout can call it. :func:`detect_repo_stacks` reads a checkout. Units come
from the build-unit index when the caller has one, else from the language
registry's package manifests in the listing. A unit whose ecosystem builds its
members from an ancestor (a Cargo or uv workspace, a Maven reactor, a Gradle
multi-project build, a .NET solution) is run by that ancestor's stack.

Commands run from the unit's directory (:attr:`Stack.root`). They need no
elevated privileges and run in sh, bash and PowerShell; ``./mvnw`` and
``./gradlew`` are not cmd.exe spellings (cmd runs ``mvnw.cmd``), and the ``&&``
of a c8 or nyc report step needs PowerShell 7, not Windows PowerShell 5.1. A stack whose
command only works inside the CI step it ships with says so in
:attr:`Stack.shell`. Ceiling: Python commands assume pytest, and a .NET stack
runs one solution or one test project; anything else is a note, not a guess.
"""

from __future__ import annotations

import configparser
import fnmatch
import json
import posixpath
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import cache, partial
from pathlib import Path
from typing import Any

from ..build_units.model import BuildUnitIndex
from ..code_origin import code_origin, path_origin
from ..fs_walk import INDEX_BLOCKED_DIRS, walk_repo
from ..ingestion.languages.registry import REGISTRY
from ..ingestion.resolvers.pnpm_workspace import PNPM_WORKSPACE_FILENAME
from ..ingestion.workspace_members import parse_toml_or_none
from ..pytest_roots import PYTEST_CONFIG_NAMES, read_pytest_roots
from ..test_paths import is_test_related_path
from .packages import quote_arg

#: Where to look when the stack is unknown.
GENERIC_REPORTS = (
    "coverage.xml coverage/lcov.info lcov.info coverage.out **/jacoco.xml "
    "**/cobertura*.xml **/clover.xml"
)
# ceiling: pinned so the generated command is reproducible; bump with JaCoCo releases.
JACOCO = "org.jacoco:jacoco-maven-plugin:0.8.15"


@dataclass(frozen=True)
class CommandEdit:
    """A test command with coverage added, and where it writes the report."""

    text: str
    reports: str
    #: Workflow steps (dash at column 0) that must run before the command.
    prepare: tuple[str, ...] = ()
    #: Sent too on pushes only: coverage.py's ``.coverage`` with per-test contexts.
    push_reports: str = ""


@dataclass(frozen=True)
class Stack:
    """How one unit runs its tests with coverage.

    ``language`` is ``python``, ``node``, ``go``, ``java``, ``rust``,
    ``dotnet``, ``ruby``, ``php`` or ``unknown``. ``test`` is empty when
    repowise cannot tell the command; ``notes`` then say why. ``reports`` are
    space-separated globs relative to ``root``.
    """

    language: str
    runner: str | None = None
    package_manager: str | None = None
    reports: str = GENERIC_REPORTS
    #: See :attr:`CommandEdit.push_reports`.
    push_reports: str = ""
    #: Standalone CI toolchain and install steps (GitHub Actions, dash at column 0).
    setup: tuple[str, ...] = ()
    #: Standalone test command with coverage, run from ``root``; empty when unknown.
    test: str = ""
    #: CI steps ``test`` needs first (GitHub Actions, dash at column 0).
    prepare: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    #: Commands to run once, from ``root``, before ``test`` works on a workstation.
    requires: tuple[str, ...] = ()
    #: ``""`` when ``test`` runs in any shell; ``github-actions`` when it needs its CI step.
    shell: str = ""
    #: The command a workstation runs instead, when ``test`` needs its CI step.
    workstation: str = ""
    #: The unit's directory, repo-relative POSIX; ``""`` is the repository root.
    root: str = ""
    #: The test paths ``test`` names, relative to ``root``; empty when the runner picks.
    test_paths: tuple[str, ...] = ()
    #: Where the runner writes JUnit XML (a file, or a directory of files), relative to
    #: ``root``; empty when repowise does not know how this runner writes it.
    junit_report: str = ""
    #: What to add to the test command for that report; empty when the runner writes it by default.
    junit_flag: str = ""
    #: ``NAME=value`` words the test command needs in its environment for that report.
    junit_env: str = ""
    #: The variable that starts each test path in the report with the unit's directory, for a
    #: runner that writes paths relative to where it runs; repowise reads them repo-relative.
    junit_path_prefix: str = ""
    #: Finds this stack's test command in a workflow ``run`` line.
    pattern: re.Pattern | None = field(default=None, compare=False, repr=False)
    #: A matching line that is not a full test run (``-DskipTests``, one file).
    skip: re.Pattern | None = field(default=None, compare=False, repr=False)
    #: The test job must match this before its command is edited (PHP's driver).
    job_requires: re.Pattern | None = field(default=None, compare=False, repr=False)
    editor: Callable[[str, re.Match], CommandEdit | None] | None = field(
        default=None, compare=False, repr=False
    )

    def edit(self, line: str) -> CommandEdit | None:
        """``line`` with coverage added to its test command, or None if it cannot be."""
        m = self.pattern.search(line) if self.pattern else None
        return self.editor(line, m) if m and self.editor else None

    def junit_vars(self) -> str:
        """:attr:`junit_env`, plus the unit's directory under :attr:`junit_path_prefix` below the root."""
        prefix = f"{self.junit_path_prefix}={quote_arg(self.root + '/')}" if self.junit_path_prefix and self.root else ""
        return " ".join(v for v in (self.junit_env, prefix) if v)

    def report_paths(self) -> tuple[str, ...]:
        """The report globs, repo-relative."""
        return tuple(posixpath.join(self.root, r) if self.root else r for r in self.reports.split())

    def to_dict(self) -> dict[str, Any]:
        """What a person or agent needs to collect this unit's coverage, JSON-ready."""
        # A command that needs its CI step is no command on a workstation, unless it has its own.
        command = (self.test if not self.shell else self.workstation) or None
        return {
            "root": self.root,
            "stack": self.language,
            "runner": self.runner,
            "package_manager": self.package_manager,
            "command": command,
            "prerequisites": list(self.requires),
            "reports": list(self.report_paths()),
            "shell": None if command else self.shell or None,
            "notes": list(self.notes),
            # Each builder puts why it has no command last.
            "reason": None if command else (self.notes[-1] if self.notes else _UNKNOWN_REASON),
        }


def _run_lines(prepare: Iterable[str]) -> tuple[str, ...]:
    """The commands of ``- run:`` steps in *prepare*."""
    return tuple(line[len("- run: ") :] for line in prepare if line.startswith("- run: "))


# ---------------------------------------------------------------------------
# Shell text helpers
# ---------------------------------------------------------------------------

_SEP = re.compile(r"&&|\|\||[;|]|\s#")


def _segment(line: str, m: re.Match) -> tuple[int, int, bool]:
    """Start and end of the simple command holding ``m``, and whether the line continues."""
    start = 0
    for sep in _SEP.finditer(line, 0, m.start()):
        start = sep.end()
    nxt = _SEP.search(line, m.end())
    end = len(line[: nxt.start() if nxt else len(line)].rstrip())
    continued = nxt is None and line[:end].endswith("\\")
    if continued:
        end = len(line[: end - 1].rstrip())
    return start, end, continued


def _insert(line: str, *edits: tuple[int, str]) -> str:
    for pos, text in sorted(edits, reverse=True):
        line = line[:pos] + text + line[pos:]
    return line


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

_PYTEST = re.compile(r"(?<![\w./-])(?:python[\d.]*\s+-m\s+)?(?:pytest|py\.test)(?![\w.-])")
_PYTEST_ONE_FILE = re.compile(r"(?<![\w./-])(?:python[\d.]*\s+-m\s+)?pytest\s+(?:-\S+\s+)*\S+(?:\.py|::\S+)(?:\s|$)")
_UV_RUN = re.compile(r"\buv\s+run\b")
_ENV_RUN = re.compile(r"\b(poetry|pdm|hatch|pipenv)\s+run\b")


def _install_pytest_cov(tool: str | None) -> tuple[str, ...]:
    if tool == "pdm":
        cmd = "pdm run python -m ensurepip && pdm run python -m pip install pytest-cov"
    elif tool:
        cmd = f"{tool} run pip install pytest-cov"
    else:
        cmd = "python -m pip install pytest-cov"
    return (f"- run: {cmd}",)


def _edit_pytest(line: str, m: re.Match, *, sources: tuple[str, ...], declared: bool) -> CommandEdit | None:
    start, end, continued = _segment(line, m)
    after = line[m.end():end]
    xml = re.search(r"--cov-report[= ]xml(?::(\S+))?", after)
    has_cov = re.search(r"(?<![\w-])--cov(?:=|\s|$)", after)
    contexts = re.search(r"--cov-context[= ]test(?!\S)", after)
    if has_cov and xml:
        return CommandEdit(line, xml.group(1) or "coverage.xml", push_reports=".coverage" if contexts else "")
    flags = " --cov-report=xml" if has_cov else "".join(f" --cov={s}" for s in sources) + " --cov-report=xml"
    # Per-test contexts in .coverage, sent on pushes for the per-test map.
    flags += "" if contexts else " --cov-context=test"
    # At the end, where people put flags; right after pytest when the line continues.
    edits = [(m.end() if continued else end, flags)]
    prepare: tuple[str, ...] = ()
    if not declared and not has_cov:
        prefix = line[start:m.start()]
        uv = _UV_RUN.search(prefix)
        if uv:
            edits.append((start + uv.end(), " --with pytest-cov"))
        else:
            env = _ENV_RUN.search(prefix)
            prepare = _install_pytest_cov(env.group(1) if env else None)
    return CommandEdit(_insert(line, *edits), "coverage.xml", prepare, ".coverage")


def _python_sources(paths: list[str]) -> tuple[str, ...]:
    py = [p for p in paths if p.endswith(".py")]
    for top in ("src", "packages"):
        if any(p.startswith(top + "/") for p in py):
            return (top,)
    skip = {"tests", "test", "docs", "doc", "examples", "scripts", "benchmarks", "bench"}
    tops = sorted({p.split("/")[0] for p in paths if p.count("/") == 1 and p.endswith("/__init__.py")} - skip)
    return tuple(tops) or (".",)


def _testpaths(files: dict[str, str]) -> tuple[str, ...]:
    """``testpaths`` of the config governing the unit's directory, in pytest's precedence."""
    roots = read_pytest_roots((n, t) for n, t in files.items() if n in PYTEST_CONFIG_NAMES)
    governing = roots.by_dir.get("")
    return governing.testpaths if governing else ()


_UNIT_DIRS = ("unit", "unit_tests")
_SLOW_DIRS = ("integration", "integration_tests", "e2e")


def _pytest_args(paths: list[str], files: dict[str, str]) -> str:
    """``tests/unit`` (or ``tests/unit_tests``) when unit and integration tests sit side by side;
    ``tests`` when test files outside it would be collected too (a script folder's or another
    package's); else pytest's own choice."""
    for base in ("tests", "test"):
        unit = next((d for d in _UNIT_DIRS if any(p.startswith(f"{base}/{d}/") for p in paths)), None)
        other = any(p.startswith(f"{base}/{d}/") for d in _SLOW_DIRS for p in paths)
        if unit and other:
            testpaths = _testpaths(files)
            if testpaths and base not in testpaths:
                return ""  # pytest's testpaths already decide what runs
            return f" {base}/{unit}"
    return "" if _testpaths(files) else _tests_dir(paths, files)


def _tests_dir(paths: list[str], files: dict[str, str]) -> str:
    """``tests`` (or ``test``) when it holds test files and a bare run would collect others too.

    Only our own files count (:func:`~repowise.core.code_origin.path_origin`
    production or test): a vendored or example ``test_*.py`` is no reason to
    narrow. A unit that keeps a ``tests/`` directory inside a package as well
    (``pkg/tests/``), or a test file at its root, is left to pytest, so those
    tests keep running. Ceiling: a test file inside a package outside any
    tests directory is left out once ``tests`` is named.
    """
    roots = read_pytest_roots((n, t) for n, t in files.items() if n in PYTEST_CONFIG_NAMES)
    collected = [p for p in paths if p.endswith(".py") and roots.may_collect_name(p)
                 and path_origin(p) in ("production", "test")]
    for base in ("tests", "test"):
        outside = [p for p in collected if not p.startswith(f"{base}/")]
        if len(outside) == len(collected) or not outside:
            continue
        if any("/" not in p or f"/{d}/" in f"/{p}" for p in outside for d in ("tests", "test")):
            return ""
        return f" {base}"
    return ""


#: Extra and dependency-group names that carry test dependencies, best first.
_TEST_SETS = ("test", "tests", "testing")
#: ``requirements.txt``, and a requirements file naming dev or test anywhere
#: (``requirements-dev.txt``, ``test-requirements.txt``, ``requirements/test.txt``).
_TEST_REQUIREMENTS = re.compile(r"requirements\.txt"
                                r"|(?=[\w.-]*(?:dev|test))[\w.-]*requirements[\w.-]*\.txt"
                                r"|requirements/[\w.-]*(?:dev|test)[\w.-]*\.txt", re.IGNORECASE)


def _first(names: Iterable[str], wanted: Iterable[str]) -> str | None:
    have = set(names)
    return next((n for n in wanted if n in have), None)


def _keys(table: Any) -> list[str]:
    return [k for k in table if isinstance(k, str)] if isinstance(table, dict) else []


def _extras(pyproject: dict, setup_cfg: str) -> list[str]:
    """Extras the unit declares: ``[project.optional-dependencies]``, else setup.cfg's ``extras_require``.

    Ceiling: extras declared only in ``setup.py`` code are not read.
    """
    found = _keys((pyproject.get("project") or {}).get("optional-dependencies"))
    if found or not setup_cfg:
        return found
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(setup_cfg)
    except configparser.Error:
        return []
    return parser.options("options.extras_require") if parser.has_section("options.extras_require") else []


def _installable(root: set[str], paths: list[str], pyproject: dict) -> bool:
    """Whether ``pip install -e .`` builds the unit.

    pip needs ``setup.py`` or ``pyproject.toml``; a setup.cfg alone is refused.
    A pyproject that only configures tools still builds through setuptools
    when setup.cfg holds the metadata or a ``src/`` layout makes discovery
    unambiguous; in a flat tree setuptools may find several top-level
    packages and refuse, so no install is planned there.
    """
    if "setup.py" in root:
        return True
    if "pyproject.toml" not in root:
        return False
    return bool(pyproject.keys() & {"project", "build-system"}) or "setup.cfg" in root or any(
        p.startswith("src/") for p in paths
    )


#: An exact pin in a requirements file: ``name==version`` (extras and markers allowed).
_PIN = re.compile(r"(?m)^\s*([A-Za-z0-9][\w.-]*)\s*(?:\[[^\]]*\])?\s*===?\s*([^\s;#,]+)")


def _pins(text: str) -> dict[str, str]:
    """The exact pins of one requirements file, by normalized name (PEP 503)."""
    return {re.sub(r"[-_.]+", "-", m.group(1)).lower(): m.group(2) for m in _PIN.finditer(text)}


def _conflicting_pins(requirements: list[str], files: dict[str, str]) -> list[str]:
    """Packages two of *requirements* pin to different versions, sorted.

    Ceiling: only exact pins in the files themselves; a range that excludes
    another file's pin, and files they include with ``-r``, are not read.
    """
    seen: dict[str, set[str]] = {}
    for path in requirements:
        for name, version in _pins(files.get(path, "")).items():
            seen.setdefault(name, set()).add(version)
    return sorted(name for name, versions in seen.items() if len(versions) > 1)


def _pip_install(root: set[str], paths: list[str], files: dict[str, str], pyproject: dict) -> tuple[str, ...]:
    """The ``pip install`` arguments a unit's tests need: the project with its test extra,
    its requirements and test requirements files, then pytest and pytest-cov.

    One install, unless two requirements files pin a package to different
    versions: pip refuses that as one resolve, so each file installs on its
    own, ``requirements.txt`` first and the test files after it, whose pins
    the tests were written against and so win.
    """
    parts = []
    if _installable(root, paths, pyproject):
        extra = _first(_extras(pyproject, files.get("setup.cfg", "")), (*_TEST_SETS, "dev"))
        parts.append(f'-e ".[{extra}]"' if extra else "-e .")
    requirements = [p for p in paths if _TEST_REQUIREMENTS.fullmatch(p)]
    if not _conflicting_pins(requirements, files):
        return (" ".join([*parts, *(f"-r {p}" for p in requirements), "pytest pytest-cov"]),)
    ordered = sorted(requirements, key=lambda p: (p != "requirements.txt", p))
    # A conflict needs two files, so ``rest`` is never empty.
    first, *rest = (f"-r {p}" for p in ordered)
    return (" ".join([*parts, first]), *rest[:-1], f"{rest[-1]} pytest pytest-cov")


def _python_tool(
    root: set[str], paths: list[str], files: dict[str, str], args: str
) -> tuple[str, tuple[str, ...], str, tuple[str, ...]]:
    """The unit's Python tool, its CI setup, its plain test command, and its workstation installs."""
    pyproject = files.get("pyproject.toml", "")
    toml = parse_toml_or_none(pyproject) or {}
    version = (
        ("  with:", "    python-version-file: .python-version")
        if ".python-version" in root else ("  with:", '    python-version: "3.12"   # adjust to your version')
    )
    if "uv.lock" in root:
        sync = "uv sync --all-packages" if "[tool.uv.workspace]" in pyproject else "uv sync"
        setup = (*("- uses: astral-sh/setup-uv@v6", "  with:", "    enable-cache: true"), f"- run: {sync}")
        # ``uv run`` syncs only the ``dev`` group; a ``test`` group must be named.
        group = _first(_keys(toml.get("dependency-groups")), _TEST_SETS)
        return "uv", setup, f"uv run{f' --group {group}' if group else ''} pytest{args}", ()
    if "poetry.lock" in root or "[tool.poetry" in pyproject:
        setup = ("- uses: actions/setup-python@v5", *version, "- run: pipx install poetry", "- run: poetry install")
        return "poetry", setup, f"poetry run pytest{args}", ()
    if "pdm.lock" in root or "[tool.pdm" in pyproject:
        return "pdm", ("- uses: pdm-project/setup-pdm@v4", "- run: pdm install"), f"pdm run pytest{args}", ()
    if "hatch.toml" in root or "[tool.hatch.envs" in pyproject:
        setup = ("- uses: actions/setup-python@v5", *version, "- run: pipx install hatch",
                 "- run: hatch run pip install pytest pytest-cov")
        # Hatch's environment installs the project; it still needs the test tools.
        return "hatch", setup, f"hatch run pytest{args}", ("hatch run python -m pip install pytest pytest-cov",)
    installs = _pip_install(root, paths, files, toml)
    setup = ("- uses: actions/setup-python@v5", *version,
             *(f"- run: pip install {i}   # adjust to your install" for i in installs))
    # The CI install lives in ``setup``; a workstation needs the same install.
    return "pip", setup, f"pytest{args}", tuple(f"python -m pip install {i}" for i in installs)


def _has_pytest_suite(root: set[str], paths: list[str], files: dict[str, str]) -> bool:
    """Whether a unit below the repository root has a pytest suite, or declares a distribution.

    A directory whose only manifests are requirements files often holds a deploy's
    or a script's dependencies; a ``conftest.py``, pytest in its requirements, a
    pytest config or a ``tests/`` directory says it has a suite.
    """
    spec = REGISTRY.get("python")
    # A pytest.ini is pytest's own file even when it does not parse.
    marked = root & {*(spec.manifest_files if spec else ()), "conftest.py", "pytest.ini"}
    return bool(marked) or _requires_pytest(root, files) or _configures_pytest(files) or any(
        p.startswith(("tests/", "test/")) and p.endswith(".py") for p in paths
    )


def _requires_pytest(root: set[str], files: dict[str, str]) -> bool:
    """Whether the unit's requirements files name pytest or a pytest plugin."""
    requirements = "\n".join(files.get(p, "") for p in root if _REQUIREMENTS.fullmatch(p))
    # Packaging spells ``pytest_mock`` and ``pytest-mock`` alike.
    return re.search(r"(?im)^\s*pytest(?:[-_.]|\b)", requirements) is not None


def _configures_pytest(files: dict[str, str]) -> bool:
    """Whether a pytest config with a pytest section governs the unit's directory."""
    return "" in read_pytest_roots((n, t) for n, t in files.items() if n in PYTEST_CONFIG_NAMES).by_dir


def _installs_xdist(paths: list[str], files: dict[str, str]) -> bool:
    """Whether the unit's printed install brings pytest-xdist.

    Read from the requirements files the pip line names and the pyproject
    tables each tool installs by default: the project's dependencies, the
    test or dev extra pip installs, uv's dev and test groups, Poetry's groups
    and PDM's dev dependencies. Ceiling: a Hatch environment's dependencies
    are not read, so a Hatch unit never gets the flag.
    """
    toml = parse_toml_or_none(files.get("pyproject.toml", "")) or {}
    project = toml.get("project") or {}
    extras = project.get("optional-dependencies") or {}
    groups = toml.get("dependency-groups") or {}
    tool = toml.get("tool") or {}
    poetry = tool.get("poetry") or {}
    chosen = _first(_keys(extras), (*_TEST_SETS, "dev"))
    tables = [
        project.get("dependencies"),
        extras.get(chosen) if chosen else None,
        *(groups.get(g) for g in ("dev", *_TEST_SETS)),
        poetry.get("dev-dependencies"),
        *((g or {}).get("dependencies") for g in (poetry.get("group") or {}).values() if isinstance(g, dict)),
        (tool.get("pdm") or {}).get("dev-dependencies"),
        *(files.get(p, "") for p in paths if _TEST_REQUIREMENTS.fullmatch(p)),
    ]
    return any(re.search(r"pytest[-_]xdist", json.dumps(t)) for t in tables if t)


def _python(root: set[str], paths: list[str], files: dict[str, str]) -> Stack:
    reqs = sorted(p for p in root if _REQUIREMENTS.fullmatch(p))
    texts = "\n".join(files.get(p, "") for p in (*sorted(_python_config_names()), *reqs))
    declared = bool(re.search(r"pytest[-_]cov", texts))
    sources = _python_sources(paths)
    args = _pytest_args(paths, files)
    # pytest-xdist where the printed install brings it spreads the coverage run over
    # every core (pytest-cov combines the workers' data), unless the config already
    # picks a worker count.
    xdist = _installs_xdist(paths, files) and not re.search(
        r"(?<![\w-])(?:-n|--numprocesses)(?:=|\s|\d)", texts)
    tool, setup, base, installs = _python_tool(root, paths, files, args + (" -n auto" if xdist else ""))
    notes: list[str] = []
    if tool == "pip" and (pinned := _conflicting_pins([p for p in paths if _TEST_REQUIREMENTS.fullmatch(p)], files)):
        notes.append(f"Requirements files pin {', '.join(pinned)} to different versions, so pip installs "
                     "them one at a time and the test requirements' pins win.")
    if sources == (".",):
        notes.append("Repowise found no `src/` or package directory, so coverage measures the whole "
                     "checkout; narrow `--cov` to your source directory if you like.")
    editor = partial(_edit_pytest, sources=sources, declared=declared)
    # The standalone pip and hatch install lines already bring pytest-cov.
    test = partial(_edit_pytest, sources=sources, declared=declared or tool in ("pip", "hatch"))(base, _PYTEST.search(base))
    requires = _run_lines(test.prepare) + installs
    return Stack(
        "python", "pytest", tool, "coverage.xml", test.push_reports, setup=setup, test=test.text, prepare=test.prepare,
        notes=tuple(notes), requires=requires, test_paths=tuple(args.split()), pattern=_PYTEST,
        skip=_PYTEST_ONE_FILE, editor=editor, junit_report="junit.xml", junit_flag="--junitxml=junit.xml",
    )


# ---------------------------------------------------------------------------
# JavaScript / TypeScript
# ---------------------------------------------------------------------------

_NODE = re.compile(
    r"(?<![\w@/.-])(?:(npm|pnpm|yarn|bun)\s+(run\s+)?(test[\w:-]*)(?![\w:.-])"
    r"|(?:bunx\s+|npx\s+)?(jest|vitest)(?:\s+run)?(?![\w.-]))"
)
#: Package-manager flags that run a script across workspaces (before any ``--``).
_MONOREPO_ARGS = re.compile(r"(?<![\w-])(--workspaces?|-ws|-w|--filter|-F|-r|--recursive)(?![\w-])")
_DELEGATES = re.compile(
    r"\b(turbo|nx|lerna|wsrun|ultra)\b|\bworkspaces\s+foreach\b|--workspaces?\b"
    r"|\bpnpm\s+(-r|--recursive|--filter)\b"
)
_C8 = "npx --yes c8 --reporter=lcov "
_JEST_FLAGS = "--coverage --coverageReporters=lcov"
# reportOnFailure: Vitest skips the report when one test fails, losing the whole run's coverage.
# reportsDirectory: the command line beats a config's own directory, so the report lands where
# the printed ingest path looks.
_VITEST_FLAGS = (
    "--coverage.enabled --coverage.reporter=lcov --coverage.reportOnFailure --coverage.reportsDirectory=coverage"
)
_BUN_FLAGS = "--coverage --coverage-reporter=lcov"
_COVERED_REPORTS = "coverage/lcov.info coverage/clover.xml"
#: The flag that makes each runner write ``junit.xml`` beside its other output, naming each
#: test's file. Jest needs the ``jest-junit`` package and :data:`_JEST_JUNIT_ENV`.
_NODE_JUNIT_FLAGS = {
    "jest": "--reporters=default --reporters=jest-junit",
    "vitest": "--reporter=default --reporter=junit --outputFile.junit=junit.xml",
}
#: jest-junit names a test's file only through its config: the classname becomes the test
#: file's path (as Vitest's is), relative to where Jest runs. The report lands at ``junit.xml``.
_JEST_JUNIT_ENV = "JEST_JUNIT_CLASSNAME={filepath}"
_JEST_JUNIT_PREFIX = "JEST_JUNIT_FILE_PATH_PREFIX"
# ceiling: the jest-junit major whose options those are; bump when a release renames them.
_JEST_JUNIT_MAJOR = 16
#: What each package manager runs a package's own binary with.
_NODE_EXEC = {"npm": "npx", "pnpm": "pnpm exec", "yarn": "yarn", "bun": "bunx"}
#: A script that is Jest or Vitest and plain arguments alone, so the package manager's exec
#: runs it as it is: no environment, chain, quoting, expansion, redirection, comment, or a bare
#: ``--`` that would turn the flags appended after it into test patterns.
_PLAIN_RUNNER = re.compile(r"(?:jest|vitest)(?:\s+(?!--(?:\s|$))[^\s'\"`$\\<>&|;()#]+)*")
#: The test script ``npm init`` writes, which runs nothing.
_NO_TESTS = "no test specified"
#: Where a trailing shell comment starts: the arguments a package manager appends land inside it.
_SCRIPT_COMMENT = re.compile(r"(?:^|\s)#")
#: ``packageManager`` naming Yarn 2 or later (Berry).
_YARN_BERRY = re.compile(r"yarn@(?:[2-9]|\d\d)")
_YARN_LOCK = "yarn.lock"
#: The block a Yarn 2 or later lockfile opens with; a classic one has none.
_YARN_BERRY_LOCK = re.compile(r"^__metadata:", re.MULTILINE)
#: How much of a ``yarn.lock`` is read: the ``__metadata`` block follows a short comment header.
_YARN_LOCK_HEAD_CHARS = 1024


def _uncommented(script: str) -> tuple[str, bool]:
    """*script* without a trailing shell comment, and whether it had one.

    Ceiling: a ``#`` after any quote is left alone, since it may sit inside the quotes.
    """
    m = _SCRIPT_COMMENT.search(script)
    if m is None or re.search(r"['\"`]", script[: m.start()]):
        return script.rstrip(), False
    return script[: m.start()].rstrip(), True


_LCOV_REPORTER = re.compile(r"(?<![\w-])(?:--reporter|-r)(?:=|\s+)lcov(?![\w-])")
#: ``npx``, ``bunx`` and any ``<tool> exec`` (``pnpm exec``, ``nub exec --node``) run the command
#: after their own flags. A flag that takes a value (``--prefix pkg``) leaves the value where the
#: runner would be, so the line names no runner. ``-p``/``--package`` stop the match outright: their
#: value is a package name, often the runner's own, and is not the command.
_EXEC_WRAPPER = re.compile(r"^(?:npx|bunx|[\w.-]+\s+exec)(?:\s+-(?!(?:p|-package)(?![\w-]))\S+)*\s+")


def _node_runner(script: str) -> str | None:
    """What a test script runs: jest | vitest | bun | c8 (wrap it) | covered (already measures)
    | report:c8 or report:nyc (measures, but writes no lcov) | None.

    In ``a && b`` the runner must be the last command: npm, pnpm, yarn and bun add a
    script's extra arguments at its end, so the coverage flags reach only that command.
    """
    script = _uncommented(script)[0]
    if not script or re.search(r"\|\||;", script) or _DELEGATES.search(script):
        return None
    *before, last = script.split("&&")
    runner = _command_runner(last)
    return None if runner is None or any(_command_runner(cmd) for cmd in before) else runner


def _command_words(command: str) -> list[str]:
    """One simple command's words from the runner on: environment and exec wrapper left out."""
    return _EXEC_WRAPPER.sub("", re.sub(r"^\s*(?:cross-env\s+)?(?:\w+=\S+\s+)*", "", command)).split()


def _command_runner(command: str) -> str | None:
    """:func:`_node_runner` for one simple command."""
    words = _command_words(command)
    head = words[0] if words else ""
    if head in ("c8", "nyc"):
        # Both default to text and html reports; without lcov, their report step writes it.
        return "covered" if _LCOV_REPORTER.search(command) else f"report:{head}"
    return "covered" if "--coverage" in words else _runner_of(head, words)


def _runner_of(head: str, words: list[str]) -> str | None:
    """The runner a script's first word names: jest | vitest | bun | c8 (wrap it) | None."""
    if head == "jest" or (head in ("react-scripts", "craco") and words[1:2] == ["test"]):
        return "jest"
    # ``vp test`` is Vite+'s front end for Vitest; it passes Vitest's flags through.
    if head == "vitest" or (head == "vp" and words[1:2] == ["test"]):
        return "vitest"
    if head == "bun" and words[1:2] == ["test"]:
        return "bun"
    if head in ("mocha", "ava", "tap", "uvu", "tape") or (head == "node" and "--test" in words):
        return "c8"
    return None


def _dev_install(pm: str, pkg: str, workspace: str) -> tuple[str, ...]:
    """The workflow step that installs *pkg* for the run (npm leaves ``package.json`` as it is).

    *workspace* is the flag the package manager needs to add to a workspace root
    (:func:`_root_add_flag`), ``""`` elsewhere.
    """
    add = f"add -D {workspace}" if workspace else "add -D"
    cmd = {"npm": f"npm install --no-save {pkg}", "bun": f"bun add -d {pkg}"}.get(pm, f"{pm} {add} {pkg}")
    return (f"- run: {cmd}",)


def _yarn_berry(root: set[str], package: dict, lock: str) -> bool:
    """Whether Yarn here is Berry (2 or later).

    Its config file, ``packageManager`` naming it, or the ``__metadata`` block a Berry
    lockfile opens with (*lock* is the head of the governing ``yarn.lock``).
    """
    return (
        ".yarnrc.yml" in root
        or bool(_YARN_BERRY.match(str(package.get("packageManager") or "")))
        or bool(_YARN_BERRY_LOCK.search(lock))
    )


def _root_add_flag(pm: str, workspace: bool, berry: bool) -> str:
    """The flag *pm* needs to add a package to a workspace root: pnpm and Yarn classic refuse without it."""
    if not workspace:
        return ""
    return {"pnpm": "-w", "yarn": "" if berry else "-W"}.get(pm, "")


def _vitest_provider(pm: str, deps: dict, workspace: str = "") -> tuple[str, ...]:
    if "@vitest/coverage-v8" in deps or "@vitest/coverage-istanbul" in deps:
        return ()
    spec = str(deps.get("vitest") or "")
    pkg = f'"@vitest/coverage-v8@{spec}"' if re.fullmatch(r"[\^~]?\d[\w.-]*", spec) else "@vitest/coverage-v8"
    return _dev_install(pm, pkg, workspace)


_NODE_FLAGS = {"jest": _JEST_FLAGS, "vitest": _VITEST_FLAGS, "bun": _BUN_FLAGS}


def _edit_direct_runner(line: str, m: re.Match, runner: str, pm: str, deps: dict, workspace: str) -> CommandEdit:
    """Coverage flags right after a runner called directly (``npx jest``, ``bun test``)."""
    prepare = _vitest_provider(pm, deps, workspace) if runner == "vitest" else ()
    return CommandEdit(_insert(line, (m.end(), " " + _NODE_FLAGS[runner])), "coverage/lcov.info", prepare)


def _edit_script(
    line: str, m: re.Match, runner: str, *, end: int, after: str, pm: str, deps: dict, workspace: str
) -> CommandEdit:
    """Coverage for a package script whose *runner* is known."""
    if runner == "c8":
        return CommandEdit(_insert(line, (m.start(), _C8)), "coverage/lcov.info")
    if runner.startswith("report:"):
        # The tool keeps its raw data after the run, so its report command writes lcov from it.
        report = f" && npx --yes {runner[len('report:'):]} report --reporter=lcov"
        return CommandEdit(_insert(line, (end, report)), "coverage/lcov.info")
    # npm hands arguments to the script only after ``--``, which it drops. pnpm passes a ``--`` on
    # (Vitest and Jest stop reading options there) and reads ``pnpm test --flag`` as its own
    # option, so it gets ``pnpm run <script> <flags>``. yarn and bun pass arguments as they are.
    tool, run = m.group(1), m.group(2)
    dashes = re.search(r"(?<!\S)--(?!\S)", after)
    sep = " --" if tool == "npm" and not dashes else ""
    # pnpm: before a ``--`` the line already has, where the runner still reads them.
    at = m.end() + dashes.start() - 1 if tool == "pnpm" and dashes else end
    # A script that measures already gets no flags, only the form that hands the script
    # the JUnit flag appended later.
    covered = runner == "covered"
    edits = [(at, sep + ("" if covered else f" {_NODE_FLAGS[runner]}"))]
    if tool == "pnpm" and not run:
        edits.append((m.start(3), "run "))
    prepare = _vitest_provider(pm, deps, workspace) if runner == "vitest" else ()
    return CommandEdit(_insert(line, *edits), _COVERED_REPORTS if covered else "coverage/lcov.info", prepare)


def _edit_node(
    line: str, m: re.Match, *, pm: str, scripts: dict, deps: dict, workspace: str = ""
) -> CommandEdit | None:
    _, end, continued = _segment(line, m)
    after = line[m.end():end]
    tool, run, script, direct = m.groups()
    if "--coverage" in after:
        return CommandEdit(line, _COVERED_REPORTS)
    if direct or (tool == "bun" and not run):  # the runner itself, or bun's own runner
        return _edit_direct_runner(line, m, direct or "bun", pm, deps, workspace)
    text = str(scripts.get(script) or "")
    runner = _node_runner(text)
    if continued or _MONOREPO_ARGS.search(after.split(" -- ")[0]) or runner is None:
        return None
    if runner == "covered" and _measuring_runner(text) not in _NODE_JUNIT_FLAGS:
        # Measures already, and no JUnit flag is appended later: the line stays as it is.
        return CommandEdit(line, _COVERED_REPORTS)
    return _edit_script(line, m, runner, end=end, after=after, pm=pm, deps=deps, workspace=workspace)


def _node_package(files: dict[str, str]) -> tuple[dict, dict, dict]:
    """``package.json`` as a dict, its scripts, and its dependencies of both kinds."""
    try:
        package = json.loads(files.get("package.json") or "{}")
    except ValueError:
        package = {}
    package = package if isinstance(package, dict) else {}
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    deps: dict = {}
    for key in ("dependencies", "devDependencies"):
        if isinstance(package.get(key), dict):
            deps.update(package[key])
    return package, scripts, deps


def _node_manager(root: set[str], berry: bool) -> tuple[str, str]:
    """The package manager the lockfile names, and its install command (*berry*: Yarn 2 or later)."""
    if "bun.lockb" in root or "bun.lock" in root:
        return "bun", "bun install --frozen-lockfile"
    if "pnpm-lock.yaml" in root:
        return "pnpm", "pnpm install --frozen-lockfile"
    if "yarn.lock" in root:
        return "yarn", "yarn install --immutable" if berry else "yarn install --frozen-lockfile"
    return "npm", "npm ci" if "package-lock.json" in root else "npm install"


def _node_setup(root: set[str], package: dict, pm: str, install: str) -> tuple[str, ...]:
    setup: list[str] = []
    if pm == "pnpm":
        setup.append("- uses: pnpm/action-setup@v4")
        if not package.get("packageManager"):
            setup += ["  with:", "    version: latest"]
    if pm == "bun":
        setup.append("- uses: oven-sh/setup-bun@v2")
    else:
        version_file = next((f for f in (".nvmrc", ".node-version") if f in root), None)
        setup += ["- uses: actions/setup-node@v4", "  with:",
                  f"    node-version-file: {version_file}" if version_file else "    node-version: lts/*",
                  f"    cache: {pm}"]
    setup.append(f"- run: {install}")
    return tuple(setup)


def _node_workspace(package: dict, pnpm: bool) -> bool:
    """Whether a package is a workspace root: npm/yarn ``workspaces``, or a pnpm workspace file beside it."""
    return bool(package.get("workspaces")) or pnpm


def _test_script(scripts: dict) -> tuple[str, str | None]:
    """The script that runs the tests and its runner.

    ``test`` when its runner is known. Else (``test`` may chain a build, a linter and
    other scripts first) the one ``test...`` script with a known runner that ``test``
    names, or the plain one of a single suite's variants (``test:vitest`` beside
    ``test:vitest:min``). Else ``test`` with no runner, or ``""`` when the package has
    no test script at all. Scripts with no known runner (Playwright, ``tsc``) write no
    coverage these flags could ask for, so they do not compete.
    """
    test = str(scripts.get("test") or "")
    test = "" if _NO_TESTS in test else test
    runner = _node_runner(test)
    if runner:
        return "test", runner
    name = _fallback_script(test, _runner_scripts(scripts))
    if name:
        return name, _node_runner(str(scripts[name]))
    return ("test" if test else ""), None


def _runner_scripts(scripts: dict) -> dict[str, str]:
    """``test...`` scripts that run a known runner, watch modes aside: name -> runner."""
    found = {}
    for name, script in scripts.items():
        if re.fullmatch(r"test[\w:-]+", name) and "watch" not in name and "--watch" not in str(script):
            runner = _node_runner(str(script))
            if runner:
                found[name] = runner
    return found


def _fallback_script(test: str, found: dict[str, str]) -> str:
    """The one of *found* that is the whole run, or ``""``."""
    named = [n for n in found if re.search(rf"(?<![\w:-]){re.escape(n)}(?![\w:-])", test)]
    if len(named) == 1:
        return named[0]
    plain = min(found, key=lambda n: (len(n), n), default="")
    variants = all(n == plain or n.startswith((f"{plain}:", f"{plain}-")) for n in found)
    # Several suites (``test:unit`` and ``test:integration``): no one script is the whole run.
    return plain if variants and len(set(found.values())) == 1 else ""


def _script_command(pm: str, name: str) -> str:
    """How *pm* runs the package script *name*."""
    if name == "test":
        return f"{pm} {'run test' if pm == 'bun' else 'test'}"
    return f"{pm} run {name}"


def _direct_base(pm: str, scripts: dict, name: str) -> tuple[str, tuple[str, ...]]:
    """The runner the script *name* is, run by *pm*'s exec, and the hooks that leaves out.

    Only for a script with ``pre``/``post`` hooks that is the runner alone: the hooks (a
    lint, a check that fails on another platform) write no coverage and can stop the run
    first. ``("", ())`` otherwise, and the script stays.
    """
    hooks = tuple(h for h in (f"pre{name}", f"post{name}") if scripts.get(h))
    # A trailing comment would swallow the flags the package manager appends, so it runs directly too.
    script, commented = _uncommented(str(scripts.get(name) or "").strip())
    if (hooks or commented) and _PLAIN_RUNNER.fullmatch(script):
        return f"{_NODE_EXEC[pm]} {script}", hooks
    return "", ()


def _node_base(pm: str, scripts: dict, deps: dict) -> tuple[str, str | None, tuple[str, ...]]:
    """The plain test command, its runner, and the lifecycle scripts it leaves out
    (:func:`_direct_base`): a test script, else the runner the package depends on;
    ``""`` when the package has neither, so it has no tests to cover."""
    name, runner = _test_script(scripts)
    if name:
        direct, hooks = _direct_base(pm, scripts, name)
        if not direct and _uncommented(str(scripts[name]))[1]:
            # ceiling: a commented script that is not the runner alone (an environment, a chain)
            # gets no command; through the script, every appended flag would land in the comment.
            runner = None
        elif runner == "covered":
            runner = _measuring_runner(str(scripts[name]))
        return direct or _script_command(pm, name), runner, hooks
    runner = "vitest" if "vitest" in deps else "jest" if "jest" in deps else None
    if runner is None:
        return "", None, ()
    return f"{_NODE_EXEC[pm]} {runner}" + (" run" if runner == "vitest" else ""), runner, ()


def _measuring_runner(script: str) -> str:
    """The runner a script that measures coverage already runs (``jest --coverage``), else ``covered``.

    Named so its test timings get the runner's JUnit flag. Ceiling: a runner behind c8 or nyc
    stays ``covered``.
    """
    words = _command_words(_uncommented(script)[0].split("&&")[-1])
    runner = _runner_of(words[0] if words else "", words)
    return runner if runner in _NODE_FLAGS else "covered"


def _reporter_install(test: CommandEdit | None, runner: str | None, pm: str, deps: dict, workspace: str) -> tuple[str, ...]:
    """The step that installs jest-junit for test timings, when Jest runs and the package lacks it."""
    if test is None or runner != "jest" or "jest-junit" in deps:
        return ()
    # The major is pinned: the environment variables setup prints are its options.
    return _dev_install(pm, f"jest-junit@{_JEST_JUNIT_MAJOR}", workspace)


def _node_junit(runner: str | None) -> dict[str, str]:
    """The :class:`Stack` JUnit fields for *runner*."""
    flag = _NODE_JUNIT_FLAGS.get(runner or "", "")
    jest = runner == "jest"
    return {
        "junit_report": "junit.xml" if flag else "",
        "junit_flag": flag,
        "junit_env": _JEST_JUNIT_ENV if jest else "",
        "junit_path_prefix": _JEST_JUNIT_PREFIX if jest else "",
    }


def _node_notes(
    test: CommandEdit | None, workspace: bool, script: str, base: str, *, reporter: bool, hooks: tuple[str, ...]
) -> tuple[str, ...]:
    """Why the unit has no command, or what its command installs and leaves out (*hooks*: :func:`_hooks_note`)."""
    if test is not None:
        notes = []
        if test.prepare:
            notes.append("Vitest needs a coverage provider, so the workflow installs `@vitest/coverage-v8` "
                         "for the run. Add it to `devDependencies` to drop that install.")
        if reporter:
            notes.append("Jest writes test timings through `jest-junit`, so the workflow installs it for "
                         "the run. Add it to `devDependencies` to drop that install.")
        return (*notes, *hooks)
    if workspace and (not script or _DELEGATES.search(script)):
        return ("This is a monorepo whose packages run their own tests, so Repowise could not add "
                "one coverage flag for all of them. Run each package's tests with an lcov reporter "
                "and list the reports in `REPORTS` (for example `packages/*/coverage/lcov.info`).",)
    return (f"Repowise could not tell which test runner `{base}` uses. Make the test command "
            f"write an lcov report (Jest: `{_JEST_FLAGS}`; Vitest: `{_VITEST_FLAGS}`; any "
            "other runner on Node: prefix it with `npx c8 --reporter=lcov`).",)


def _hooks_note(pm: str, hooks: tuple[str, ...]) -> tuple[str, ...]:
    """Why the command runs the runner, not the test script, and how to run the *hooks* it skips."""
    if not hooks:
        return ()
    pre = hooks[0].startswith("pre")
    script = _script_command(pm, hooks[0][3:] if pre else hooks[0][4:])
    named = " and ".join(f"`{h}`" for h in hooks)
    first = f" Run `{_script_command(pm, hooks[0])}` first when the tests need it." if pre else ""
    return (f"This runs the test runner directly: `{script}` would also run the {named} "
            f"script{'s' if len(hooks) > 1 else ''}.{first}",)


def _node(root: set[str], files: dict[str, str]) -> Stack | None:
    """The package's stack; None when it has no test script and no test runner to run."""
    package, scripts, deps = _node_package(files)
    berry = _yarn_berry(root, package, files.get(_YARN_LOCK, ""))
    pm, install = _node_manager(root, berry)
    base, runner, hooks = _node_base(pm, scripts, deps)
    workspace = _node_workspace(package, PNPM_WORKSPACE_FILENAME in root)
    if not base and not workspace:
        return None
    add_flag = _root_add_flag(pm, workspace, berry)
    editor = partial(_edit_node, pm=pm, scripts=scripts, deps=deps, workspace=add_flag)
    test = editor(base, _NODE.search(base)) if runner else None
    # Test timings need the reporter in node_modules, beside the runner.
    reporter = _reporter_install(test, runner, pm, deps, add_flag)
    prepare = (*test.prepare, *reporter) if test else ()
    return Stack(
        "node", runner, pm, test.reports if test else "coverage/lcov.info",
        setup=_node_setup(root, package, pm, install),
        test=test.text if test else "", prepare=prepare,
        notes=_node_notes(test, workspace, str(scripts.get("test") or ""), base,
                          reporter=bool(reporter), hooks=_hooks_note(pm, hooks)),
        # The install comes first: the runner and the coverage provider live in node_modules.
        requires=(install, *_run_lines(prepare)) if test else (), pattern=_NODE, editor=editor,
        **_node_junit(runner),
    )


# ---------------------------------------------------------------------------
# Go, Rust, JVM, .NET, Ruby, PHP
# ---------------------------------------------------------------------------

_GO = re.compile(r"(?<![\w-])go\s+test(?![\w-])")


def _edit_go(line: str, m: re.Match) -> CommandEdit:
    _, end, _ = _segment(line, m)
    have = re.search(r"-coverprofile[= ](\S+)", line[m.end():end])
    if have:
        return CommandEdit(line, have.group(1))
    return CommandEdit(_insert(line, (m.end(), " -coverprofile=coverage.out")), "coverage.out")


def _go(paths: list[str], files: dict[str, str]) -> Stack | None:
    """None for a ``go.mod`` with no ``module`` line or no Go file below it (a build helper, not a module)."""
    gomod = files.get("go.mod")  # None when the caller read no manifests: nothing to judge by
    unnamed = gomod is not None and not re.search(r"^\s*module\s+\S", gomod, re.M)
    # ``./...`` stops at a nested module, so its Go files are not this module's.
    nested = [_dir(p) + "/" for p in paths if posixpath.basename(p) == "go.mod" and "/" in p]
    if unnamed or not any(p.endswith(".go") and not p.startswith(tuple(nested)) for p in paths):
        return None
    return Stack(
        "go", "go test", "go", "coverage.out",
        setup=("- uses: actions/setup-go@v5", "  with:", "    go-version-file: go.mod"),
        test="go test -coverprofile=coverage.out ./...", pattern=_GO, editor=_edit_go,
    )


_CARGO = re.compile(r"(?<![\w-])cargo\s+(\+\S+\s+)?(test|nextest\s+run)(?![\w-])")
_LLVM_COV = ("- uses: taiki-e/install-action@cargo-llvm-cov",)


def _edit_cargo(line: str, m: re.Match) -> CommandEdit:
    _, end, _ = _segment(line, m)
    if "llvm-cov" in line[m.start():end]:
        return CommandEdit(line, "lcov.info")
    sub = "nextest" if m.group(2).startswith("nextest") else ""
    new = f"cargo {m.group(1) or ''}llvm-cov {sub + ' ' if sub else ''}--lcov --output-path lcov.info"
    return CommandEdit(line[:m.start()] + new + line[m.end():], "lcov.info", _LLVM_COV)


def _rust(root: set[str], files: dict[str, str]) -> Stack:
    setup = () if root & {"rust-toolchain", "rust-toolchain.toml"} else (
        "- uses: dtolnay/rust-toolchain@stable", "  with:", "    components: llvm-tools-preview")
    workspace = " --workspace" if "[workspace]" in files.get("Cargo.toml", "") else ""
    return Stack(
        "rust", "cargo test", "cargo", "lcov.info", setup=setup,
        test=f"cargo llvm-cov{workspace} --lcov --output-path lcov.info", prepare=_LLVM_COV,
        requires=("cargo install cargo-llvm-cov",), pattern=_CARGO, editor=_edit_cargo,
    )


_JAVA_SETUP = ("- uses: actions/setup-java@v4", "  with:", "    distribution: temurin",
                    '    java-version: "21"   # adjust to your version')
_MVN = re.compile(r"(?<![\w/.-])(?:\./)?mvnw?(?:\.cmd)?(?![\w.-])(?=[^;&|#\n]*(?<![\w:.-])(?:test|verify|install|package)(?![\w:.-]))")
_MVN_SKIP = re.compile(r"-DskipTests(?!=false)|-Dmaven\.test\.skip=true")


def _edit_maven(line: str, m: re.Match, *, jacoco: bool, reports: str) -> CommandEdit | None:
    _, end, continued = _segment(line, m)
    if jacoco or "jacoco" in line[m.end():end]:
        return CommandEdit(line, reports)
    if continued:
        return None
    return CommandEdit(_insert(line, (m.end(), f" {JACOCO}:prepare-agent"), (end, f" {JACOCO}:report")), reports)


def _maven(root: set[str], files: dict[str, str]) -> Stack:
    pom = files.get("pom.xml", "")
    jacoco = "jacoco-maven-plugin" in pom
    reports = "**/target/site/jacoco/jacoco.xml" if "<modules>" in pom else "target/site/jacoco/jacoco.xml"
    notes = []
    if jacoco:
        notes.append("Your `pom.xml` has the `jacoco-maven-plugin`; its `report` goal must run in the "
                     "build (usually bound to `verify`) so the XML report exists after the tests.")
    else:
        notes.append("Your `pom.xml` has no JaCoCo plugin, so the test command runs JaCoCo's "
                     "`prepare-agent` and `report` goals by name. Adding the `jacoco-maven-plugin` to "
                     "the pom does the same and keeps the command short.")
    if "<argLine>" in pom and "@{argLine}" not in pom:
        notes.append("Surefire's `argLine` is set without `@{argLine}`, which drops the JaCoCo agent; "
                     "prefix it with `@{argLine}`.")
    editor = partial(_edit_maven, jacoco=jacoco, reports=reports)
    mvn = "./mvnw" if "mvnw" in root else "mvn"
    test = editor(f"{mvn} -B verify", _MVN.search(f"{mvn} -B verify"))
    return Stack(
        "java", "maven", "maven", reports, setup=(*_JAVA_SETUP, "    cache: maven"), test=test.text,
        notes=tuple(notes), pattern=_MVN, skip=_MVN_SKIP, editor=editor,
        # Surefire writes one file per test class; a multi-module build writes one directory per module.
        junit_report="" if "<modules>" in pom else "target/surefire-reports",
    )


_GRADLE_TASK = re.compile(r"(?<![\w-])(test|check|build)(?![\w-])")
_GRADLE = re.compile(
    r"(?<![\w/.-])(?:\./)?gradlew?(?:\.bat)?(?![\w.-])(?=[^;&|#\n]*" + _GRADLE_TASK.pattern + ")"
)
#: Applies JaCoCo with its XML report to every Java project; shipped in the wheel for workstations.
JACOCO_INIT_SCRIPT = Path(__file__).parent / "_data" / "jacoco.gradle"


@cache
def _gradle_init() -> tuple[str, ...]:
    """The CI step writing the init script; read when a build needs it, not at import."""
    return (
        "- name: Enable JaCoCo XML reports",
        "  run: |",
        "    cat > \"$RUNNER_TEMP/repowise-jacoco.gradle\" <<'EOF'",
        *(f"    {line}" for line in JACOCO_INIT_SCRIPT.read_text(encoding="utf-8").splitlines()),
        "    EOF",
    )


def _edit_gradle(line: str, m: re.Match, *, jacoco: bool, reports: str) -> CommandEdit | None:
    _, end, _ = _segment(line, m)
    after = line[m.end():end]
    if not jacoco or re.search(r"-x\s+test\b", after):
        return None
    if "jacocoTestReport" in after:
        return CommandEdit(line, reports)
    task = _GRADLE_TASK.search(after)
    if not task:
        return None
    return CommandEdit(_insert(line, (m.end() + task.end(), " jacocoTestReport")), reports)


def _gradle(root: set[str], files: dict[str, str]) -> Stack:
    build = files.get("build.gradle", "") + files.get("build.gradle.kts", "")
    settings = files.get("settings.gradle", "") + files.get("settings.gradle.kts", "")
    jacoco = "jacoco" in build
    multi = re.search(r"^\s*include\b", settings, re.M)
    reports = ("**/" if multi else "") + "build/reports/jacoco/test/jacocoTestReport.xml"
    gradle = "./gradlew" if "gradlew" in root else "gradle"
    notes = []
    if jacoco:
        if not re.search(r"xml\.(required|enabled|isEnabled)|xml\s*\{", build):
            notes.append("Gradle writes JaCoCo's XML report only when asked: add "
                         "`jacocoTestReport { reports { xml.required = true } }` to the build file.")
        test, prepare, workstation = f"{gradle} test jacocoTestReport", (), ""
    else:
        notes.append("Your build applies no JaCoCo plugin, so the command adds it for the run with a "
                     "small init script that ships with Repowise. Applying the `jacoco` plugin in the build "
                     "(with `xml.required = true`) does the same.")
        test, prepare = f'{gradle} test jacocoTestReport --init-script "$RUNNER_TEMP/repowise-jacoco.gradle"', _gradle_init()
        # Forward slashes: Gradle takes them on Windows, and no shell reads them as escapes.
        workstation = f"{gradle} test jacocoTestReport --init-script {quote_arg(JACOCO_INIT_SCRIPT.as_posix())}"
    return Stack(
        "java", "gradle", "gradle", reports, setup=(*_JAVA_SETUP, "    cache: gradle"), test=test,
        prepare=prepare, notes=tuple(notes), shell="" if jacoco else "github-actions", workstation=workstation,
        pattern=_GRADLE,
        skip=re.compile(r"-x\s+test\b"), editor=partial(_edit_gradle, jacoco=jacoco, reports=reports),
        junit_report="" if multi else "build/test-results/test",
    )


_DOTNET = re.compile(r"(?<![\w-])dotnet\s+test(?![\w-])")
_DOTNET_FLAGS = '--collect:"XPlat Code Coverage" --results-directory coverage'


def _edit_dotnet(line: str, m: re.Match) -> CommandEdit:
    _, end, _ = _segment(line, m)
    after = line[m.end():end]
    if "XPlat Code Coverage" in after:
        return CommandEdit(line, "**/TestResults/*/coverage.cobertura.xml")
    if "--results-directory" in after:
        return CommandEdit(_insert(line, (m.end(), ' --collect:"XPlat Code Coverage"')), "**/coverage.cobertura.xml")
    return CommandEdit(_insert(line, (m.end(), " " + _DOTNET_FLAGS)), "coverage/*/coverage.cobertura.xml")


def _dotnet_setup(root: set[str], projects: str) -> tuple[str, ...]:
    if "global.json" in root:
        return ("- uses: actions/setup-dotnet@v4", "  with:", "    global-json-file: global.json")
    versions = sorted({f"{v}.x" for v in re.findall(r"net(\d+\.\d+)", projects)}) or ["8.0.x"]
    return ("- uses: actions/setup-dotnet@v4", "  with:", "    dotnet-version: |", *(f"      {v}" for v in versions))


def _dotnet_sdk(files: dict[str, str]) -> str | None:
    """The SDK ``dotnet test`` needs, when ``global.json`` pins one.

    Ceiling: without a pin the note says nothing; the target frameworks of the
    projects read are a lower bound only, so they are not named. Reading every
    project the chosen solution builds would be the upgrade.
    """
    try:
        # A file saved with a BOM is still the pin the SDK reads.
        pinned = json.loads((files.get(_DOTNET_SDK_PIN) or "{}").lstrip("\ufeff")).get("sdk", {}).get("version")
    except (ValueError, AttributeError):
        pinned = None
    if isinstance(pinned, str) and pinned:
        return (f"`{_DOTNET_SDK_PIN}` pins the .NET SDK to {pinned}: `dotnet --list-sdks` must list it, or a "
                "later one its `rollForward` allows, before `dotnet test` runs.")
    return None


def _dotnet_notes(tests: list[str], files: dict[str, str], target: str | None, found: bool) -> list[str]:
    notes = [sdk] if (sdk := _dotnet_sdk(files)) else []
    if tests and not any("coverlet.collector" in t for p, t in files.items() if p in tests):
        notes.append("`--collect:\"XPlat Code Coverage\"` needs the `coverlet.collector` package in each "
                     "test project (`dotnet add package coverlet.collector`).")
    if target:
        return notes
    if found:
        return [*notes, "Repowise found several solutions or test projects; point `dotnet test` at the one "
                        "that runs your tests."]
    return [*notes, "Repowise found no solution or test project to point `dotnet test` at."]


def _dotnet(root: set[str], paths: list[str], files: dict[str, str]) -> Stack:
    # A samples or fixture solution is not the one that runs the tests; the unit's own comes first.
    solutions = [p for p in paths if p.endswith(_SOLUTIONS) and _kept(p)]
    own = [p for p in solutions if "/" not in p]
    solutions = own if len(own) == 1 else solutions
    tests = [p for p in paths if p.endswith(".csproj") and "test" in p.lower() and p.count("/") <= 3]
    target = solutions[0] if len(solutions) == 1 else tests[0] if len(tests) == 1 and not solutions else None
    setup = _dotnet_setup(root, "\n".join(t for p, t in files.items() if p.endswith(".csproj")))
    notes = _dotnet_notes(tests, files, target, bool(solutions or tests))
    test = f"dotnet test {target} {_DOTNET_FLAGS}" if target else ""
    return Stack(
        "dotnet", "dotnet test", "dotnet", "coverage/*/coverage.cobertura.xml", setup=tuple(setup),
        test=test, notes=tuple(notes), pattern=_DOTNET, editor=_edit_dotnet,
    )


_RUBY = re.compile(r"(?<![\w-])(?:rspec|rake\s+(?:test|spec)|rails\s+test)(?![\w:-])")


def _ruby(root: set[str], paths: list[str], files: dict[str, str]) -> Stack:
    gemfile = files.get("Gemfile", "")
    if "simplecov-cobertura" in gemfile:
        reports, notes = "coverage/coverage.xml", ()
    elif "simplecov-lcov" in gemfile:
        reports, notes = "coverage/lcov/*.lcov coverage/lcov.info", ()
    else:
        reports = "coverage/coverage.xml"
        notes = ("Ruby writes coverage through SimpleCov, which has to start inside the test process. "
                 "Add `gem \"simplecov\"` and `gem \"simplecov-cobertura\"` to the Gemfile's test group, and "
                 "put this at the very top of `spec/spec_helper.rb` (or `test/test_helper.rb`):\n\n"
                 "  ```ruby\n  require \"simplecov\"\n  require \"simplecov-cobertura\"\n"
                 "  SimpleCov.formatter = SimpleCov::Formatter::CoberturaFormatter\n  SimpleCov.start\n  ```\n\n"
                 "  Until then the upload step finds no report and says so.",)
    if "rspec" in gemfile or any(p.startswith("spec/") for p in paths):
        test, runner = "bundle exec rspec", "rspec"
    elif "bin/rails" in paths:
        test, runner = "bin/rails test", "rails test"
    else:
        test, runner = "bundle exec rake test", "rake test"
    version = () if root & {".ruby-version", ".tool-versions"} else ("    ruby-version: \"3.3\"   # adjust to your version",)
    return Stack(
        "ruby", runner, "bundler", reports,
        setup=("- uses: ruby/setup-ruby@v1", "  with:", *version, "    bundler-cache: true"),
        test=test, notes=notes, pattern=_RUBY, editor=lambda line, m: CommandEdit(line, reports),
    )


_PHPUNIT = re.compile(r"(?<![\w-])(?:[\w./-]*/)?(?:phpunit|pest)(?![\w.-])")


def _edit_phpunit(line: str, m: re.Match) -> CommandEdit:
    _, end, _ = _segment(line, m)
    have = re.search(r"--coverage-clover[= ](\S+)", line[m.end():end])
    if have:
        return CommandEdit(line, have.group(1))
    return CommandEdit(_insert(line, (m.end(), " --coverage-clover clover.xml")), "clover.xml")


def _php(files: dict[str, str]) -> Stack:
    try:
        composer = json.loads(files.get("composer.json") or "{}")
    except ValueError:
        composer = {}
    composer = composer if isinstance(composer, dict) else {}
    dev = composer.get("require-dev") if isinstance(composer.get("require-dev"), dict) else {}
    php = str((composer.get("require") or {}).get("php", "")) if isinstance(composer.get("require"), dict) else ""
    found = re.search(r"\d+\.\d+", php)
    version = found.group(0) if found else "8.3"
    runner = "pest" if "pestphp/pest" in dev else "phpunit"
    return Stack(
        "php", runner, "composer", "clover.xml",
        setup=("- uses: shivammathur/setup-php@v2", "  with:", f'    php-version: "{version}"',
               "    coverage: pcov", "- run: composer install --no-interaction --no-progress"),
        test=f"vendor/bin/{runner} --coverage-clover clover.xml",
        pattern=_PHPUNIT, job_requires=re.compile(r"coverage:\s*['\"]?(pcov|xdebug)|XDEBUG_MODE"),
        editor=_edit_phpunit,
    )

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_UNKNOWN_REASON = "Repowise could not tell how this repository runs its tests."
UNKNOWN = Stack("unknown", notes=(_UNKNOWN_REASON,))

#: The stack each registry language's package manifests start.
_LANGUAGE_STACKS = {
    "python": "python",
    "go": "go",
    "rust": "rust",
    "java": "jvm",
    "kotlin": "jvm",
    "csharp": "dotnet",
    "ruby": "ruby",
    "php": "php",
    "javascript": "node",
    "typescript": "node",
}
#: Most specific first; node last, as a root ``package.json`` is often tooling.
_ORDER = ("python", "go", "rust", "jvm", "dotnet", "ruby", "php", "node")
#: A builder returns None for a unit with nothing to test (a package with no test script).
_BUILDERS: dict[str, Callable[[set[str], list[str], dict[str, str]], Stack | None]] = {
    "python": _python,
    "go": lambda names, paths, files: _go(paths, files),
    "rust": lambda names, paths, files: _rust(names, files),
    "jvm": lambda names, paths, files: (
        _maven(names, files) if "pom.xml" in names else _gradle(names, files)
    ),
    "dotnet": _dotnet,
    "ruby": _ruby,
    "php": lambda names, paths, files: _php(files),
    "node": lambda names, paths, files: _node(names, files),
}
#: What the registry's package-manifest glob matches, case and all.
_REQUIREMENTS = re.compile(r"requirements[^/]*\.txt", re.IGNORECASE)
#: pip's own spelling, which alone lets the repository root keep its pytest guess without a suite.
_PIP_REQUIREMENTS = re.compile(r"requirements[\w.-]*\.txt")
_SOLUTIONS = (".sln", ".slnx")
#: How many .csproj files detection reads at a .NET root.
_DOTNET_PROJECTS = 20
#: Gradle's settings files: runner config read on top of the registry's manifests.
_GRADLE_SETTINGS = ("settings.gradle", "settings.gradle.kts")
#: The .NET SDK's pin file: runner config read on top of the registry's manifests.
_DOTNET_SDK_PIN = "global.json"
#: A manifest of this origin is a fixture, sample or copy, not a unit to cover.
_SKIP_ORIGINS = frozenset({"test", "generated", "vendored", "docs_example", "data"})


@cache
def _manifest_stacks(name: str) -> frozenset[str]:
    """The stacks package manifest *name* starts, from the language registry."""
    found = set()
    for tag, stack in _LANGUAGE_STACKS.items():
        spec = REGISTRY.get(tag)
        if spec is None:
            continue
        exact = name in spec.manifest_files and name not in spec.build_config_manifests
        if exact or any(fnmatch.fnmatch(name.lower(), g.lower()) for g in spec.manifest_globs):
            found.add(stack)
    return frozenset(found)


@cache
def _read_names() -> frozenset[str]:
    """Every manifest and runner config detection reads at a unit root."""
    names = set(PYTEST_CONFIG_NAMES) | set(_GRADLE_SETTINGS) | {PNPM_WORKSPACE_FILENAME, _DOTNET_SDK_PIN}
    for tag in _LANGUAGE_STACKS:
        spec = REGISTRY.get(tag)
        if spec is not None:
            names.update(n for n in spec.manifest_files if n not in spec.build_config_manifests)
    return frozenset(names)


@cache
def _python_config_names() -> frozenset[str]:
    """Python's manifests plus pytest's configs: where ``pytest-cov`` would be declared."""
    spec = REGISTRY.get("python")
    return frozenset(PYTEST_CONFIG_NAMES | set(spec.manifest_files if spec else ()))


def _dir(path: str) -> str:
    return posixpath.dirname(path)


def _join(root: str, name: str) -> str:
    return f"{root}/{name}" if root else name


def _under(root: str, path: str) -> str | None:
    """*path* relative to *root*, or None when it is not below it."""
    if not root:
        return path
    return path[len(root) + 1 :] if path.startswith(root + "/") else None


#: A file name no origin rule reads, so its origin is its directory's (``testdata/`` is test).
_PLAIN_NAME = "_"


def _kept(path: str, repo_root: str | None = None) -> bool:
    """Whether a manifest at *path* can declare a unit: not pruned, not a fixture or sample.

    The directory is judged too: a manifest's own origin is ``build``, which hides a
    ``testdata/`` or ``fixtures/`` directory around it. The manifest makes its directory
    a package root, which keeps a shipped ``test-utils/`` package production, as it
    does for every file below one. *repo_root* lets the directory's origin read
    the folders around it (a data folder, a docs site); without it the path decides.
    """
    if any(part in INDEX_BLOCKED_DIRS for part in path.split("/")[:-1]) or path_origin(path) in _SKIP_ORIGINS:
        return False
    directory = _dir(path)
    plain = _join(directory, _PLAIN_NAME)
    is_test = is_test_related_path(plain, package_root=lambda d: d == directory)
    return code_origin(plain, is_test=is_test, repo_root=repo_root) not in _SKIP_ORIGINS


def _stacks_of(name: str) -> set[str]:
    """The stacks a file named *name* starts at its directory (.NET runs through solutions)."""
    found = set(_manifest_stacks(name))
    if name in _GRADLE_SETTINGS:
        found.add("jvm")
    return found - {"dotnet"}


def _roots_of(manifests: Iterable[str], repo_root: str | None = None) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for manifest in manifests:
        stacks = _stacks_of(posixpath.basename(manifest))
        if stacks and _kept(manifest, repo_root):
            found.setdefault(_dir(manifest), set()).update(stacks)
    return found


def _ancestors(root: str) -> list[str]:
    parts = root.split("/") if root else []
    return ["/".join(parts[:i]) for i in range(len(parts))]


@dataclass(frozen=True)
class _Units:
    """Unit roots and the stacks their manifests start.

    ``workspaces`` are roots the build-unit index does not list (a virtual
    Cargo workspace, a Gradle settings-only root) above roots it does: they
    run a stack only when it builds its members from there.
    """

    roots: dict[str, set[str]]
    workspaces: frozenset[str] = frozenset()


def _units(
    paths: list[str], units: BuildUnitIndex | None, repo_root: str | None = None
) -> _Units:
    """The units of a listing: from the index when there is one, else every kept
    directory holding a registry package manifest."""
    listed = _roots_of(paths, repo_root)
    workspaces: set[str] = set()
    if units is None:
        roots = listed
    else:
        roots = _roots_of((u.manifest for u in units.units.values()), repo_root)
        above = {a for root in roots for a in _ancestors(root)} - roots.keys()
        workspaces = above & listed.keys()
        roots.update({root: set(listed[root]) for root in workspaces})
    if any("/" not in p and _PIP_REQUIREMENTS.fullmatch(p) for p in paths):
        roots.setdefault("", set()).add("python")
        workspaces.discard("")
    solutions = {_dir(p) for p in paths if p.endswith(_SOLUTIONS) and _kept(p, repo_root)}
    has_projects = any(p.endswith(".csproj") and p.count("/") <= 2 for p in paths)
    for root in solutions or ({""} if has_projects else set()):
        roots.setdefault(root, set()).add("dotnet")
    return _Units(roots, frozenset(workspaces))


def _aggregates(stack: str, root: str, files: Mapping[str, str]) -> bool:
    """Whether the *stack* at *root* builds and tests the same stack's units below it."""
    cargo, pyproject = files.get(_join(root, "Cargo.toml"), ""), files.get(_join(root, "pyproject.toml"), "")
    if stack == "rust":
        return "[workspace]" in cargo
    if stack == "python":
        return "[tool.uv.workspace]" in pyproject
    if stack == "jvm":
        settings = "".join(files.get(_join(root, name), "") for name in _GRADLE_SETTINGS)
        pom = files.get(_join(root, "pom.xml"), "")
        return "<modules>" in pom or bool(re.search(r"^\s*include\b", settings, re.M))
    if stack == "node":
        # A workspace root whose test script calls a known runner runs its packages' tests. Any
        # other script (turbo, ``yarn workspaces run``, an unknown tool) leaves the packages theirs.
        package, scripts, _ = _node_package({"package.json": files.get(_join(root, "package.json"), "")})
        runner = _test_script(scripts)[1]
        workspace = _node_workspace(package, _join(root, PNPM_WORKSPACE_FILENAME) in files)
        return workspace and runner is not None
    return stack == "dotnet"


def _covered(stack: str, root: str, roots: Mapping[str, set[str]], files: Mapping[str, str]) -> bool:
    """Whether an ancestor unit's *stack* already runs the unit at *root*."""
    return any(stack in roots.get(a, ()) and _aggregates(stack, a, files) for a in _ancestors(root))


def _runs_at(stack: str, root: str, found: _Units, files: Mapping[str, str]) -> bool:
    if root in found.workspaces and not _aggregates(stack, root, files):
        return False
    return not _covered(stack, root, found.roots, files)


@cache
def _js_lock_names() -> frozenset[str]:
    """The registry's JavaScript lockfiles, and Yarn's config, which picks its install flag."""
    spec = REGISTRY.get("javascript")
    return frozenset((*(spec.lock_files if spec else ()), ".yarnrc.yml"))


def _lock_dir(root: str, present: set[str]) -> str | None:
    """The nearest directory at or above *root* holding a JavaScript lockfile.

    A workspace member has none of its own: its root's lockfile picks the package manager.
    """
    locks = _js_lock_names() - {".yarnrc.yml"}
    return next(
        (d for d in (root, *reversed(_ancestors(root))) if any(_join(d, n) in present for n in locks)), None
    )


def _governing_lock(root: str, present: set[str]) -> set[str]:
    """The lockfile names of :func:`_lock_dir`, with Yarn's config when it sits beside them."""
    directory = _lock_dir(root, present)
    return set() if directory is None else {n for n in _js_lock_names() if _join(directory, n) in present}


def _governing_yarn_lock(root: str, present: set[str]) -> str | None:
    """The path of the ``yarn.lock`` that governs *root*, when its lockfile is Yarn's."""
    directory = _lock_dir(root, present)
    path = None if directory is None else _join(directory, _YARN_LOCK)
    return path if path in present else None


def _sdk_pin(root: str, present: set[str]) -> str | None:
    """The ``global.json`` the .NET SDK uses at *root*: the nearest one at or above it, as dotnet walks up."""
    return next(
        (p for d in (root, *reversed(_ancestors(root))) if (p := _join(d, _DOTNET_SDK_PIN)) in present), None
    )


def _listing_at(root: str, stack: str, paths: list[str], roots: Mapping[str, set[str]]) -> list[str]:
    """*paths* below *root*, rebased; a .NET root leaves out nested .NET roots' files."""
    rel = [r for p in paths if (r := _under(root, p)) is not None]
    if stack != "dotnet":
        return rel
    inner = [r for other, s in roots.items() if "dotnet" in s and other != root and (r := _under(root, other))]
    return [p for p in rel if not any(p.startswith(i + "/") for i in inner)]


def detect_stacks(
    paths: Iterable[str],
    files: Mapping[str, str],
    units: BuildUnitIndex | None = None,
    repo_root: str | None = None,
) -> list[Stack]:
    """Every unit's stack, the root's first, then by depth; ``[UNKNOWN]`` when none.

    *paths* are the repository's files, repo-relative POSIX. *files* holds the
    text of the paths :func:`files_to_read` names (missing ones read as empty).
    *units* is the build-unit index, when the caller has one. A ``package.json``
    whose runner is unclear beside another stack is tooling and starts none.
    *repo_root* is the checkout, when there is one: a manifest in a data folder
    is known only from the folder's contents.
    """
    paths = sorted(set(paths))
    present = set(paths)
    found = _units(paths, units, repo_root)
    stacks: list[Stack] = []
    for root in sorted(found.roots, key=lambda r: (r.count("/") + bool(r), r)):
        texts = {r: text for p, text in files.items() if (r := _under(root, p)) is not None}
        here: list[Stack] = []
        for stack in _ORDER:
            if stack in found.roots[root] and _runs_at(stack, root, found, files):
                rel = _listing_at(root, stack, paths, found.roots)
                names = {p for p in rel if "/" not in p}
                if stack == "node":
                    names |= _governing_lock(root, present)
                if stack == "node" and (lock := _governing_yarn_lock(root, present)) in files:
                    texts = {**texts, _YARN_LOCK: files[lock]}
                if stack == "dotnet" and (pin := _sdk_pin(root, present)) in files:
                    texts = {**texts, _DOTNET_SDK_PIN: files[pin]}
                # The repository root keeps its guess when pip's own spelling names its requirements.
                pip_root = not root and any(_PIP_REQUIREMENTS.fullmatch(n) for n in names)
                if stack == "python" and not pip_root and not _has_pytest_suite(names, rel, texts):
                    continue
                built = _BUILDERS[stack](names, rel, texts)
                if built is not None:
                    here.append(replace(built, root=root))
        tooling = len(here) > 1 and here[-1].language == "node" and not here[-1].test
        stacks += here[:-1] if tooling else here
    return stacks or [UNKNOWN]


def files_to_read(
    paths: Iterable[str], units: BuildUnitIndex | None = None, repo_root: str | None = None
) -> list[str]:
    """The manifests and runner configs :func:`detect_stacks` reads, besides the listing."""
    paths = sorted(set(paths))
    present = set(paths)
    roots = _units(paths, units, repo_root).roots
    names = sorted(_read_names())
    wanted = [path for root in sorted(roots) for n in names if (path := _join(root, n)) in present]
    wanted += [
        path
        for root, stacks in sorted(roots.items())
        if "python" in stacks
        for path in paths
        if (rel := _under(root, path)) is not None and _REQUIREMENTS.fullmatch(rel)
    ]
    for root, stacks in sorted(roots.items()):
        if "dotnet" in stacks:
            rel = _listing_at(root, "dotnet", paths, roots)
            projects = [r for r in rel if r.endswith(".csproj") and r.count("/") <= 3]
            tests = [r for r in projects if "test" in r.lower()]
            # Test projects first (coverlet is checked there), then the rest for the SDK versions.
            # Ceiling: _DOTNET_PROJECTS projects; a larger solution may need its SDK named in global.json.
            others = [r for r in projects if r not in tests]
            wanted += [_join(root, r) for r in (tests + others)[:_DOTNET_PROJECTS]]
            if (pin := _sdk_pin(root, present)) is not None:
                wanted.append(pin)
        if "node" in stacks and (lock := _governing_yarn_lock(root, present)) is not None:
            wanted.append(lock)
    return list(dict.fromkeys(wanted))


def detect_repo_stacks(repo_path: str | Path, units: BuildUnitIndex | None = None) -> list[Stack]:
    """:func:`detect_stacks` over a checkout: one pruned walk, then the manifests it names.

    Pass the stored build units (``load_build_units``) when there are any; an
    empty index counts as none. Blocking file reads.
    """
    root = Path(repo_path)
    paths = [
        (Path(dirpath) / name).relative_to(root).as_posix()
        for dirpath, _, filenames in walk_repo(root, prune_dirs=INDEX_BLOCKED_DIRS)
        for name in filenames
    ]
    index = units if units is not None and units.units else None
    files: dict[str, str] = {}
    for path in files_to_read(paths, index, str(root)):
        # A lockfile can run to megabytes; only its head says which Yarn wrote it.
        size = _YARN_LOCK_HEAD_CHARS if posixpath.basename(path) == _YARN_LOCK else -1
        try:
            with (root / path).open(encoding="utf-8", errors="ignore") as fh:
                files[path] = fh.read(size)
        except OSError:
            continue
    return detect_stacks(paths, files, index, str(root))


__all__ = [
    "GENERIC_REPORTS",
    "JACOCO",
    "JACOCO_INIT_SCRIPT",
    "UNKNOWN",
    "CommandEdit",
    "Stack",
    "detect_repo_stacks",
    "detect_stacks",
    "files_to_read",
]
