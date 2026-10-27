"""The command that runs a plan's tests, shared by plan validation and staged-plan batches."""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from ....ingestion.external_systems.maven_model import load_maven_reactor
from ....ingestion.package_roots import module_for, owning_package
from ....ingestion.resolvers.dotnet.msbuild import MSBuildProject, parse_csproj
from ....ingestion.resolvers.jvm_gradle import parse_settings
from ....ingestion.workspace_members import (
    declared_workspace_members,
    manifest_package_name,
    read_toml,
)
from ...test_selection import JS_SUFFIXES, PYTHON_SUFFIXES

#: Each test (an id, or the file a capped list collapses to) of a language in
#: :data:`_RUNNERS`, mapped to ``[run, arg]``: the command that runs its package
#: from the repository root, and the argument narrowing it to the test's target,
#: or None to run the whole package. Args of one run are joined with spaces,
#: except ``--flag=value`` args, whose values merge into one ``--flag "a|b"``
#: for a runner that keeps only its last such flag. A test the map does not hold
#: gets no command.
TestRuns = Mapping[str, Sequence[str | None]]

_SOLUTIONS = (".sln", ".slnx")


def _file(test: str) -> str:
    return test.split("::", 1)[0]


def _is_js(test: str) -> bool:
    return _file(test).lower().endswith(JS_SUFFIXES)


def _arg(path: str) -> str:
    # Double quotes hold a space in sh, PowerShell and cmd alike. Ceiling: a
    # path with ``"``, ``$`` or a backtick is left as is; upgrade by quoting
    # per shell once a plan names its shell.
    return f'"{path}"' if re.search(r"\s", path) and not re.search(r'["$`]', path) else path


def _relative(path: str, owner: str) -> str:
    return path[len(owner) + 1 :] if owner else path


@dataclass
class _Checkout:
    """One repository's package manifests, each extra file read at most once."""

    root: Path
    manifests: Mapping[str, frozenset[str]]
    _memo: dict[tuple[Hashable, ...], Any] = field(default_factory=dict)

    def memo(self, read: Callable[[Path], Any], path: Path) -> Any:
        """*read* of *path*, once per checkout: every test of a package asks again."""
        key = (read, path)
        if key not in self._memo:
            self._memo[key] = read(path)
        return self._memo[key]

    def has(self, directory: str, name: str) -> bool:
        return self.memo(Path.is_file, self.root / directory / name)

    def owner(
        self, path: str, name: str, accepts: Callable[[str], bool] | None = None
    ) -> str | None:
        # The scan leaves the root manifest out, so the default checks the disk.
        return owning_package(
            path, self.manifests, name, accepts or (lambda directory: self.has(directory, name))
        )

    @cached_property
    def dotnet(self) -> _DotnetLayout:
        return _dotnet_layout(self.root, self.manifests)

    @cached_property
    def cargo_members(self) -> set[str]:
        return declared_workspace_members(self.root).get("Cargo.toml", set())

    @cached_property
    def maven_reactor(self) -> frozenset[str]:
        """Module directories the root POM reaches through ``<modules>``."""
        poms = [self.root / "pom.xml"] + [
            self.root / directory / "pom.xml"
            for directory, names in self.manifests.items()
            if "pom.xml" in names
        ]
        reactor = load_maven_reactor(self.root, poms).declared_manifests
        return frozenset(posixpath.dirname(manifest) for manifest in reactor)

    @cached_property
    def gradle_projects(self) -> dict[str, str]:
        """Each project directory the root settings file includes, to its project path."""
        for name in ("settings.gradle.kts", "settings.gradle"):
            if self.has("", name):
                modules, overrides = parse_settings(self.root / name)
                projects: dict[str, str] = {}
                for module in modules:
                    directory = overrides.get(module) or module.replace(":", "/")
                    projects[posixpath.normpath(directory)] = f":{module}"
                return projects
        return {}


def _declares_test(manifest: Path) -> bool:
    try:
        scripts = json.loads(manifest.read_text(encoding="utf-8")).get("scripts")
    except (OSError, ValueError, AttributeError):
        return False
    script = scripts.get("test") if isinstance(scripts, dict) else None
    # npm init's placeholder exits 1 without running anything.
    return isinstance(script, str) and bool(script) and "no test specified" not in script


def _npm(owner: str) -> str:
    return f"npm --prefix {_arg(owner)} test --" if owner else "npm test --"


def _js_run(repo: _Checkout, test: str) -> Run | None:
    """``npm test`` from the nearest package whose package.json declares a
    test script, on the test file's path relative to that package."""
    path = _file(test)
    owner = repo.owner(
        path, "package.json", lambda d: repo.memo(_declares_test, repo.root / d / "package.json")
    )
    return None if owner is None else (_npm(owner), _arg(_relative(path, owner)))


def js_root_runs(tests: Iterable[str]) -> dict[str, list[str | None]]:
    """Each JS/TS test in *tests* run from the repository root: the guess when
    no checkout is there to read its package from."""
    return {path: [_npm(""), _arg(path)] for path in {_file(t) for t in tests if _is_js(t)}}


def keeps_runs(tests: Iterable[str], runs: TestRuns) -> bool:
    """Whether a plan stores its *runs* map: when it holds any, or is empty
    beside a JS/TS test, so a later read does not take the plan for one stored
    before runs existed and guess a root ``npm test`` (:func:`stored_runs`)."""
    return bool(runs) or any(_is_js(test) for test in tests)


def stored_runs(validation: Mapping[str, Any]) -> dict[str, list[str | None]]:
    """The ``test_runs`` a stored *validation* holds, plus the run of each JS/TS
    test a plan stored before JS joined them keeps in ``js_packages``. A plan
    stored with neither ran its JS/TS tests from the root, and still does."""
    runs = validation.get("test_runs")
    if runs is None and validation.get("js_packages") is None:
        tests = validation.get("tests")
        return js_root_runs(t for t in tests if isinstance(t, str)) if isinstance(tests, list) else {}
    out = {k: list(v) for k, v in runs.items()} if isinstance(runs, Mapping) else {}
    packages = validation.get("js_packages")
    for path, owner in packages.items() if isinstance(packages, Mapping) else ():
        if isinstance(owner, str):
            out.setdefault(path, [_npm(owner), _arg(_relative(path, owner))])
    return out


#: NuGet ids that make a project a test project: the test SDK and each
#: framework's runner.
_TEST_PACKAGES = frozenset(
    {
        "microsoft.net.test.sdk",
        "mstest",
        "mstest.testadapter",
        "nunit3testadapter",
        "tunit",
        "xunit",
        "xunit.runner.visualstudio",
        "xunit.v3",
        "xunit.v3.mtp-v1",
        "xunit.v3.mtp-v2",
    }
)
#: MSBuild files every project below their directory imports.
_SHARED_BUILD_FILES = ("Directory.Build.props", "Directory.Build.targets")
#: The .NET test project naming convention (``Shop.Tests``, ``Shop.UnitTests``,
#: ``Shop.Test``, ``Shop.Specs``).
_TEST_PROJECT_NAME = re.compile(r"(Tests|\.Test|\.Specs)$", re.IGNORECASE)


def _declares_tests(is_test_project: bool, packages: Iterable[str]) -> bool:
    return is_test_project or not _TEST_PACKAGES.isdisjoint(p.lower() for p in packages)


def _shared_declares_tests(root: Path, directory: str, seen: dict[str, bool]) -> bool:
    """Whether a ``Directory.Build`` file in *directory* declares tests outside
    any ``Condition``, each directory read once per call (*seen*)."""
    if directory not in seen:
        parsed = [
            parse_csproj(root / directory / name)
            for name in _SHARED_BUILD_FILES
            if (root / directory / name).is_file()
        ]
        seen[directory] = any(
            _declares_tests(p.is_test_project, p.unconditional_package_references)
            for p in parsed
            if p is not None
        )
    return seen[directory]


def _declared_test_project(
    root: Path, project: str, parsed: MSBuildProject, seen: dict[str, bool]
) -> bool:
    """Whether *project* (*parsed*) says it runs tests, itself or through a
    shared build file on its path from the repository root."""
    if _declares_tests(parsed.is_test_project, parsed.package_references):
        return True
    parts = project.split("/")[:-1]
    return any(
        _shared_declares_tests(root, "/".join(parts[:i]), seen) for i in range(len(parts) + 1)
    )


def _project_graph(root: Path, projects: list[str]) -> tuple[dict[str, set[str]], set[str]]:
    """Over *projects* (repo-relative ``.csproj`` paths): each referenced one
    mapped to the projects whose ``ProjectReference`` names it, and the
    projects that declare tests (:func:`_declared_test_project`).
    Ceiling: one parse per project and shared build file, per call."""
    by_path = {(root / project).resolve(): project for project in projects}
    referrers: dict[str, set[str]] = {}
    declared: set[str] = set()
    seen: dict[str, bool] = {}
    for project in projects:
        parsed = parse_csproj(root / project)
        if parsed is None:
            continue
        if _declared_test_project(root, project, parsed, seen):
            declared.add(project)
        for target in parsed.project_references:
            if target in by_path:
                referrers.setdefault(by_path[target], set()).add(project)
    return referrers, declared


def _root_files(root: Path) -> tuple[list[str], str | None]:
    """The ``.csproj`` files at the repository root, which the package scan
    leaves out, and its solution: the only one, or the one named after the
    checkout; None when there is none or the choice is a guess."""
    try:
        names = sorted(p.name for p in root.iterdir())
    except OSError:
        return [], None
    projects = [name for name in names if name.lower().endswith(".csproj")]
    solutions = [name for name in names if Path(name).suffix.lower() in _SOLUTIONS]
    if len(solutions) > 1:
        solutions = [name for name in solutions if Path(name).stem.lower() == root.name.lower()]
    return projects, solutions[0] if len(solutions) == 1 else None


def _test_project(path: str, layout: _DotnetLayout) -> str | None:
    """The test project that runs C# file *path*: the one project in its
    directory (the root's when no deeper one holds it) when it declares tests
    or is named as a test project, else the only project declaring tests that
    references it (*path* is in a test helper library); None otherwise. An app
    or library with neither signal runs no tests."""
    by_dir = layout.by_dir
    owner = module_for(path, by_dir.keys())
    projects = by_dir.get(owner, []) if owner in by_dir else by_dir.get("", [])
    if len(projects) != 1:
        return None
    if projects[0] in layout.test_projects or _TEST_PROJECT_NAME.search(Path(projects[0]).stem):
        return projects[0]
    users = [u for u in layout.referrers.get(projects[0], ()) if u in layout.test_projects]
    return users[0] if len(users) == 1 else None


@dataclass(frozen=True)
class _DotnetLayout:
    """A checkout's C# projects (``by_dir``: directory to ``.csproj`` paths,
    ``""`` for the root), who references whom, the test projects, and the
    root solution."""

    by_dir: dict[str, list[str]]
    referrers: dict[str, set[str]]
    test_projects: set[str]
    solution: str | None


def _dotnet_layout(root: Path, manifests: Mapping[str, frozenset[str]]) -> _DotnetLayout:
    """The layout from the ``.csproj`` names *manifests* (the package scan)
    records, plus the root's own files, which the scan leaves out."""
    root_projects, solution = _root_files(root)
    by_dir: dict[str, list[str]] = {"": root_projects} if root_projects else {}
    for directory, names in manifests.items():
        for name in sorted(names):
            if name.lower().endswith(".csproj"):
                by_dir.setdefault(directory, []).append(f"{directory}/{name}")
    referrers, test_projects = _project_graph(root, [p for ps in by_dir.values() for p in ps])
    return _DotnetLayout(by_dir, referrers, test_projects, solution)


Run = tuple[str, str | None]


def _go_run(repo: _Checkout, test: str) -> Run | None:
    """``go test`` on the test's package directory, inside the module owning it."""
    path = _file(test)
    module = repo.owner(path, "go.mod")
    if module is None:
        return None
    package = posixpath.dirname(_relative(path, module))
    run = f"go -C {_arg(module)} test" if module else "go test"
    return run, _arg(f"./{package}" if package else ".")


def _declared_target(data: Mapping[str, Any], rel: str) -> str | None:
    """The ``[[test]]`` target of the crate's manifest *data* whose root is *rel*."""
    declared = data.get("test")
    for target in declared if isinstance(declared, list) else ():
        name = target.get("name") if isinstance(target, dict) else None
        if not isinstance(name, str):
            continue
        if posixpath.normpath(target.get("path") or f"tests/{name}.rs") == rel:
            return name
    return None


def _discovered_target(rel: str) -> str | None:
    """The target Cargo makes of ``tests/<name>.rs`` or ``tests/<name>/main.rs``."""
    parts = rel.split("/")
    if len(parts) == 2 and parts[0] == "tests" and parts[1].endswith(".rs"):
        return parts[1][:-3]
    return parts[1] if len(parts) == 3 and parts[0] == "tests" and parts[2] == "main.rs" else None


def _rust_test_target(data: Mapping[str, Any], rel: str) -> str | None:
    """The integration-test target whose root is *rel* (relative to its crate)."""
    package = data.get("package")
    autotests = not (isinstance(package, dict) and package.get("autotests") is False)
    return _declared_target(data, rel) or (_discovered_target(rel) if autotests else None)


def _rust_run(repo: _Checkout, test: str) -> Run | None:
    """``cargo test`` on the crate owning the test; a virtual workspace root is no crate."""
    path = _file(test)

    def name(directory: str) -> str | None:
        return repo.memo(manifest_package_name, repo.root / directory / "Cargo.toml")

    crate = repo.owner(path, "Cargo.toml", lambda directory: name(directory) is not None)
    if crate is None:
        return None
    data = repo.memo(read_toml, repo.root / crate / "Cargo.toml")
    if not crate or crate in repo.cargo_members:
        run = f"cargo test -p {name(crate)}"
    elif "workspace" in data or "workspace" not in repo.memo(read_toml, repo.root / "Cargo.toml"):
        # ``-p`` finds only crates of the workspace at the root; any other needs its manifest.
        run = f"cargo test --manifest-path {_arg(crate + '/Cargo.toml')}"
    else:
        # Cargo refuses a crate inside a workspace that does not list it (or excludes it).
        return None
    target = _rust_test_target(data, _relative(path, crate))
    return run, f"--test {target}" if target else None


def _maven_run(repo: _Checkout, module: str) -> Run:
    # Ceiling: ``./mvnw`` is the sh spelling; cmd runs ``mvnw.cmd``, like ``&&`` in _ruby_run.
    mvn = "./mvnw" if repo.has("", "mvnw") else "mvn"
    if not module:
        return f"{mvn} test", None
    if module in repo.maven_reactor:
        # ``-am`` builds the modules it depends on; bare ``-pl`` would look for
        # them in the local repository and fail on a fresh checkout.
        return f"{mvn} -pl {_arg(module)} -am test", None
    return f"{mvn} -f {_arg(module + '/pom.xml')} test", None


def _gradle_run(repo: _Checkout, module: str) -> Run | None:
    gradle = "./gradlew" if repo.has("", "gradlew") else "gradle"
    if not module:
        return f"{gradle} test", None
    project = repo.gradle_projects.get(module)
    return (f"{gradle} {project}:test", None) if project else None


_GRADLE_SCRIPTS = ("build.gradle.kts", "build.gradle")


def _jvm_run(repo: _Checkout, test: str) -> Run | None:
    """Maven or Gradle on the deepest module owning the test."""
    path = _file(test)
    maven = repo.owner(path, "pom.xml")
    gradle = max(
        (owner for name in _GRADLE_SCRIPTS if (owner := repo.owner(path, name)) is not None),
        key=len,
        default=None,
    )
    # Both own the same directory when a repo ships both builds; Maven then runs.
    if maven is not None and (gradle is None or len(maven) >= len(gradle)):
        return _maven_run(repo, maven)
    return _gradle_run(repo, gradle) if gradle is not None else None


_RAKE_TEST = re.compile(r"""TestTask|\btask\s*\(?\s*:?['"]?test\b""")
_RSPEC = re.compile(r"""\brspec\b""")


def _declares_rake_test(rakefile: Path) -> bool:
    try:
        return bool(_RAKE_TEST.search(rakefile.read_text(encoding="utf-8", errors="ignore")))
    except OSError:
        return False


def _uses_rspec(directory: Path) -> bool:
    """Whether the gem in *directory* runs RSpec: a ``.rspec`` file, or rspec
    named in its Gemfile or gemspec."""
    if (directory / ".rspec").is_file():
        return True
    for manifest in [directory / "Gemfile", *directory.glob("*.gemspec")]:
        try:
            if _RSPEC.search(manifest.read_text(encoding="utf-8", errors="ignore")):
                return True
        except OSError:
            continue
    return False


def _ruby_run(repo: _Checkout, test: str) -> Run | None:
    """RSpec for a spec file of a gem that uses RSpec, else the Rakefile's test task."""
    path = _file(test)
    owner = repo.owner(path, "Gemfile")
    if owner is None:
        return None
    rel = _relative(path, owner)
    # Ceiling: ``&&`` fails in Windows PowerShell 5.1, as quoting does in _arg.
    cd = f"cd {_arg(owner)} && " if owner else ""
    if rel.startswith("spec/") and repo.memo(_uses_rspec, repo.root / owner):
        # A spec helper is no example file, so the whole suite runs.
        return f"{cd}bundle exec rspec", _arg(rel) if rel.endswith("_spec.rb") else None
    if repo.memo(_declares_rake_test, repo.root / owner / "Rakefile"):
        return f"{cd}bundle exec rake test", None
    return None


def _dotnet_filter(test: str) -> str | None:
    """``Class.Method`` for a ``file::...::Class::Method`` id: a substring of
    the test's fully qualified name, so a namespace or an outer class in front
    still matches. None for a file, or a name the filter syntax would misread."""
    name = ".".join(test.split("::")[1:][-2:])
    return name if re.fullmatch(r"\w+(\.\w+)?", name) else None


def _dotnet_run(repo: _Checkout, test: str) -> Run | None:
    """``dotnet test`` on the test project holding the test (else the root
    solution), filtered to the named test. Coverage collection is off: a
    coverage gate (coverlet's threshold) fails any filtered run."""
    project = _test_project(_file(test), repo.dotnet) or repo.dotnet.solution
    if project is None:
        return None
    name = _dotnet_filter(test)
    return f"dotnet test {_arg(project)} -p:CollectCoverage=false", (
        f"--filter={name}" if name else None
    )


#: Test-file suffixes and the function naming the run, per language whose
#: runner works per package. A runner gets the whole test id, ``::`` name
#: included, so one that can select a single test can narrow to it.
_RUNNERS: tuple[tuple[tuple[str, ...], Callable[[_Checkout, str], Run | None]], ...] = (
    ((".go",), _go_run),
    ((".rs",), _rust_run),
    ((".java", ".kt"), _jvm_run),
    ((".rb",), _ruby_run),
    ((".cs",), _dotnet_run),
    (JS_SUFFIXES, _js_run),
)


def runs_for_tests(
    root: Path, manifests: Mapping[str, frozenset[str]], tests: Iterable[str]
) -> dict[str, list[str | None]]:
    """The run (:data:`TestRuns`) of each test in *tests* whose language is in
    :data:`_RUNNERS`, from the package roots in *manifests*. A test whose runner
    or manifest the repository lacks is left out. Blocking file reads.
    """
    repo = _Checkout(root, manifests)
    runs: dict[str, list[str | None]] = {}
    for test in sorted(set(tests)):
        lowered = _file(test).lower()
        runner = next((run for suffixes, run in _RUNNERS if lowered.endswith(suffixes)), None)
        if runner is not None and (found := runner(repo, test)) is not None:
            runs[test] = list(found)
    return runs


def _run_key(runs: TestRuns, test: str) -> str | None:
    """The key *runs* holds for *test*: the test itself, else its file."""
    return test if test in runs else _file(test) if _file(test) in runs else None


def plan_runs(tests: Iterable[str], runs: TestRuns | None) -> dict[str, list[str | None]]:
    """The part of *runs* a plan's *tests* use, stored on the plan so a later
    read (batches, a served plan) needs no checkout."""
    if runs is None:
        return {}
    keys = {key for test in tests if (key := _run_key(runs, test)) is not None}
    return {key: list(runs[key]) for key in keys}


def _merged(args: Iterable[str]) -> list[str]:
    """*args* with every ``--flag=value`` sharing a flag merged into one
    ``--flag "a|b"``; any other arg is kept as is."""
    plain: set[str] = set()
    valued: dict[str, set[str]] = {}
    for arg in args:
        flag, equals, value = arg.partition("=")
        if equals and flag.startswith("-") and " " not in flag:
            valued.setdefault(flag, set()).add(value)
        else:
            plain.add(arg)
    merged = [f'{flag} "{"|".join(sorted(values))}"' for flag, values in sorted(valued.items())]
    return sorted(plain) + merged


def _package_commands(tests: list[str], runs: TestRuns) -> list[str]:
    """One command per run naming every target its tests need; a test that
    needs its whole package widens the run to the whole package."""
    groups: dict[str, set[str] | None] = {}
    for key in {key for test in tests if (key := _run_key(runs, test)) is not None}:
        run, arg = runs[key]
        if arg is None:
            groups[run] = None
        elif (args := groups.setdefault(run, set())) is not None:
            args.add(arg)
    return [" ".join([run, *_merged(groups[run] or ())]) for run in sorted(groups, key=_order)]


def _order(run: str) -> tuple[bool, bool, str]:
    # JS runs first and the root package's first among them, as before JS joined
    # the runs: with no Python test, Fix first quotes this first command.
    return not run.startswith("npm "), run != _npm(""), run


def commands_for_tests(
    tests: list[str],
    *,
    total: int | None = None,
    runs: TestRuns | None = None,
) -> list[str]:
    """A command that runs at least everything the plan says validates it.

    When the displayed list is capped, enumerating only the shown tests would
    read as a complete validation run while silently skipping the rest, so the
    selection widens to the files those tests live in: bounded by file count
    rather than test count, and never narrower than the evidence.

    An empty list means the plan has no command to suggest. Every command names
    its tests: with none found, a bare ``pytest`` or ``npm test`` would run a
    whole suite as if it guarded the change. A JS/TS, Go, Rust, JVM, Ruby or C#
    test runs its package, or the narrower target *runs* names for it. Any other
    test, or one *runs* does not hold, gets none in place of a guess that would
    fail when run.
    """
    if total is not None and total > len(tests):
        tests = sorted({_file(test) for test in tests})
    python_tests = sorted(test for test in tests if _file(test).endswith(PYTHON_SUFFIXES))
    commands: list[str] = []
    if python_tests:
        commands.append("pytest " + " ".join(map(_arg, python_tests)))
    if runs:
        commands.extend(_package_commands(tests, runs))
    return commands


__all__ = [
    "TestRuns",
    "commands_for_tests",
    "js_root_runs",
    "keeps_runs",
    "plan_runs",
    "runs_for_tests",
    "stored_runs",
]
