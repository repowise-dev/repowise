"""Runtime targets for ``evidence setup``: what to profile in each unit, and how.

A target is a real entry point, a benchmark or the unit-test suite, in that
order: a claim may not exceed its workload class, and only the first two may
lead. Entry points come from their owners: console scripts from the unit's
``pyproject.toml`` (:func:`~repowise.core.generation.flow_entries.console_scripts`),
the stored entry-point flag ranked by
:func:`~repowise.core.generation.entry_points.rank_entry_point_paths`, Go
``package main`` packages (:func:`~repowise.core.analysis.published_packages.go_main_packages`)
and ``package.json`` ``bin`` files (:func:`~repowise.core.ingestion.always_run.own_code`).
A Python web app's entry file that builds an ASGI app at module level is
served under uvicorn, for the agent to drive with requests.
Benchmarks are the stored functions :func:`~repowise.core.code_origin.is_benchmark_function`
accepts.

Every recipe needs no elevated privileges: pyinstrument in process for
Python, ``go test -cpuprofile`` for Go, ``node --cpu-prof`` for JavaScript.
Profiles land in ``.repowise/``. TypeScript is refused: its positions are in
the compiled JavaScript.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..code_origin import is_benchmark_function
from ..test_runners.stacks import Stack

#: Where recipes write profiles, repo-relative; the index lives here too.
PROFILE_DIR = ".repowise"
EVIDENCE_STATUS_COMMAND = "repowise evidence status"
#: Targets of one class shown per unit; the default answer stays small.
PER_CLASS = 3
CLASS_ORDER = ("entry_point", "benchmark", "unit_test")

_TS = (".ts", ".tsx", ".mts", ".cts")
_JS = (".js", ".mjs", ".cjs")
_PYTEST_BENCHMARK = re.compile(r"pytest[-_]benchmark")
_PYTEST_WORD = re.compile(r"(?<![\w-])pytest(?![\w-])")
#: Origins whose tests and benchmarks are not profiled.
_NOT_OURS = frozenset({"vendored", "generated"})
# Paths and module names reach printed commands; anything a shell could read
# as more than a path (``$``, quotes, ``;``, ``,`` in PowerShell) is left out.
_SAFE_PATH = re.compile(r"(?!-)[\w.+=/-]*")
_MODULE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
# The launchers a Python unit's test command may start with; anything else
# (an env assignment, ``cd x &&``, ``coverage run``) runs plain ``python``.
_COMMAND_WORDS = frozenset({"pytest", "python", "py.test"})
_LAUNCHERS = (("uv", "run"), ("poetry", "run"), ("pdm", "run"), ("hatch", "run"), ("pipenv", "run"))

# pyinstrument through its API, sampling every 10 ms (100 Hz, as Go's pprof
# and py-spy do) and written by the recipe as a sampled speedscope profile:
# one sample per pyinstrument sample, weighted by the wall time it covered.
# That keeps the sample count, so every share has an interval, and keeps the
# file small: the renderer's evented output (no sample count) grew to about
# 100 MB for a 10-minute run. A frame is pyinstrument's own identifier
# (``co_name``, file, first line). The path is fixed before the program runs,
# so a ``chdir`` does not move it, and an older profile is removed first. The
# profile is written at exit when the run succeeded: no uncaught exception and
# an outermost exit code in ``{ok}``. Only the program's own exit is judged
# (``try``/``except SystemExit`` around the run, through ``exec`` since ``-c``
# is one line), so a ``sys.exit`` a test catches does not fail the run. A run
# stopped by Ctrl-C still writes what it collected: the interrupt unwinds the
# program, which exits as usual. SIGTERM (what a timeout sends) writes the
# profile in the handler, then ends the process with ``os._exit(143)``, so a
# non-daemon thread or a program that swallows the interrupt cannot keep a
# timed-out run alive. ``W`` writes once and is named so the run's own
# ``except ... as e`` cannot unbind it. ``n`` holds the failed-test count a
# pytest run reports; it lands in the profile as ``testsFailed``. Ceiling: a program that installs its own
# ``sys.excepthook`` hides its crashes, one that installs its own SIGINT or
# SIGTERM handler loses the partial profile, and Windows delivers no SIGTERM
# on a kill. Double quotes outside, single inside: one spelling for sh and
# PowerShell.
_PYINSTRUMENT = (
    "import sys,os,atexit,signal,json;from pyinstrument import Profiler,__version__ as V;"
    "p=Profiler(interval=0.01);"
    "o=os.path.abspath('{out}');os.makedirs(os.path.dirname(o),exist_ok=True);"
    "os.path.exists(o) and os.remove(o);f=[];c=[0];i=[];n=[];F={{}};"
    "sys.excepthook=(lambda h:lambda *a:(f.append(1),h(*a)))(sys.excepthook);"
    # chr(36) is ``$``, which sh and PowerShell expand inside double quotes.
    "w=lambda r:json.dump({{chr(36)+'schema':'https://www.speedscope.app/file-format-schema.json',"
    "'exporter':'pyinstrument@'+V,'profiles':[{{'type':'sampled','name':'python','unit':'seconds',"
    "'startValue':0,'endValue':sum(t for _,t in r),"
    "'samples':[[F.setdefault(x.partition(chr(1))[0],len(F)) for x in k] for k,_ in r],"
    "'weights':[round(t,6) for _,t in r]}}],"
    "'shared':{{'frames':[(lambda a:{{'name':a[0],'file':a[1],'line':int(a[2])}})((x.split(chr(0))+['','0'])[:3]) for x in F]}},"
    "**({{'testsFailed':n[0]}} if n else {{}})}},open(o,'w',encoding='utf-8'),separators=(',',':'));"
    "W=lambda:(lambda r:(i or not f and c[0] in {ok}) and w(r))(p.stop().frame_records);"
    "signal.signal(signal.SIGINT,lambda *a:(i.append(1),signal.default_int_handler(*a)));"
    "signal.signal(signal.SIGTERM,lambda *a:(i.append(1),W(),os._exit(143)));"
    "atexit.register(W);"
    "p.start();exec('try:'+chr(10)+' {run}'+chr(10)+'except SystemExit as e:'+chr(10)+' c[0]=e.code;raise')"
)
#: Exit codes a profiled run may end with and still be stored. pytest's 1 means
#: the suite ran and some tests failed: the profile still measured the whole
#: workload, and the count is recorded. 2 to 5 (interrupted, internal error,
#: usage error, no tests) and any crash are refused, unless the recipe's own
#: Ctrl-C or SIGTERM handler saw the run stopped.
_CLEAN_EXITS = "(None,0)"
_TEST_EXITS = "(None,0,1)"
#: How the Python suite recipe may be cut short. A Windows kill delivers no
#: SIGTERM, so only Ctrl-C is promised everywhere.
SUBSET_NOTE = (
    "A subset of the tests is enough: failing tests are fine, and a run stopped with Ctrl-C "
    "(or SIGTERM, which a timeout sends outside Windows) still writes its profile."
)
#: Stands for an entry point's arguments; a shell refuses to run it as printed.
ARGS_PLACEHOLDER = "<args>"
_ARGS_REASON = (
    f"Replace {ARGS_PLACEHOLDER} with the arguments of a representative run, then run it; "
    "with no arguments the program only prints its usage. Replace it in the ingest line's "
    "workload too, with a short name for those arguments (the subcommand), so runs with "
    "different arguments keep separate histories."
)


@dataclass(frozen=True)
class RuntimeInputs:
    """What the index knows that runtime targets need."""

    #: Production files the index flags as entry points, best first.
    entry_files: tuple[str, ...] = ()
    #: ``(path, function)`` of every stored benchmark function outside vendored and generated code.
    benchmarks: tuple[tuple[str, str], ...] = ()
    #: Stored test files outside vendored and generated code, for the unit-test suite's package.
    test_files: tuple[str, ...] = ()
    #: ``(workload, commit)`` of every stored run, so a workload measured at
    #: HEAD is not asked for again.
    measured: frozenset[tuple[str, str]] = frozenset()


async def _stored_benchmarks(session: Any, repository_id: str) -> set[tuple[str, str]]:
    """``(path, function)`` of stored benchmark functions the planners profile
    (:func:`is_benchmark_function`): Go ``Benchmark*`` and pytest-benchmark."""
    from sqlalchemy import and_, or_, select

    from ..persistence.models import WikiSymbol

    query = select(WikiSymbol.file_path, WikiSymbol.name).where(
        WikiSymbol.repository_id == repository_id,
        or_(
            and_(WikiSymbol.file_path.like("%_test.go"), WikiSymbol.name.like("Benchmark%")),
            and_(
                WikiSymbol.file_path.like("%.py"),
                # Every benchmark path rule has "bench" in it.
                WikiSymbol.file_path.ilike("%bench%"),
                or_(WikiSymbol.name.like("bench%"), WikiSymbol.name.like("test%")),
            ),
        ),
    )
    rows = await session.execute(query)
    return {(r.file_path, r.name) for r in rows if is_benchmark_function(r.file_path, r.name)}


async def load_runtime_inputs(session: Any, repository_id: str) -> RuntimeInputs:
    """The stored entry points, benchmark functions and test files of a repository."""
    from sqlalchemy import or_, select

    from ..generation.entry_points import rank_entry_point_paths
    from ..persistence.crud import list_evidence_runs
    from ..persistence.models import GraphNode
    from .ingest import load_origins

    files = await session.execute(
        select(GraphNode.node_id, GraphNode.is_entry_point, GraphNode.is_test).where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_type == "file",
            or_(GraphNode.is_entry_point.is_(True), GraphNode.is_test.is_(True)),
        )
    )
    rows = list(files)
    entries = [r.node_id for r in rows if r.is_entry_point]
    tests = [r.node_id for r in rows if r.is_test]
    benchmarks = await _stored_benchmarks(session, repository_id)
    origins = await load_origins(session, repository_id, [*entries, *tests, *(p for p, _ in benchmarks)])
    # A copied library's or a generator's tests and benchmarks measure code nobody here maintains.
    ours = {p for p, origin in origins.items() if origin not in _NOT_OURS}
    return RuntimeInputs(
        entry_files=tuple(
            rank_entry_point_paths(p for p in entries if origins.get(p) == "production")
        ),
        benchmarks=tuple(sorted(b for b in benchmarks if b[0] in ours)),
        test_files=tuple(sorted(p for p in tests if p in ours)),
        measured=frozenset(
            (run.workload_name, run.commit_sha)
            for run in await list_evidence_runs(session, repository_id)
        ),
    )


async def stored_target_classes(session: Any, repository_id: str) -> tuple[str, ...]:
    """Workload classes ``evidence setup`` would plan a profile for, from the
    index alone, in :data:`CLASS_ORDER`.

    Held to what the planners above profile from stored inputs, with the same
    origin rules as :func:`load_runtime_inputs`: a production Python
    ``__main__.py``, a Python or Go test file of our own (only those stacks
    profile a suite), and, when neither exists, a stored benchmark function
    (:func:`_stored_benchmarks`). About 40 ms on an 8,000-file index where
    the full load takes 200 ms. Ceiling: an entry point found only on disk
    (a console script, a ``package.json`` bin) and a stack whose test runner
    detection fails are not read here, so this can say less than setup
    prints; a test file in a unit setup finds no stack for can say more.
    Read only without the checkout: with one, the actions loader reads what
    setup offers (:func:`~.setup.load_runtime_offer`).
    """
    from sqlalchemy import or_, select

    from ..code_origin import path_origin
    from ..persistence.models import GraphNode, HealthFileMetric
    from .ingest import load_origins

    def files(*where: Any) -> Any:
        return (
            select(GraphNode.node_id, HealthFileMetric.code_origin)
            .outerjoin(
                HealthFileMetric,
                (HealthFileMetric.repository_id == GraphNode.repository_id)
                & (HealthFileMetric.file_path == GraphNode.node_id),
            )
            .where(GraphNode.repository_id == repository_id, GraphNode.node_type == "file", *where)
        )

    def origin(row: Any) -> str:
        return row.code_origin or path_origin(row.node_id)

    found: list[str] = []
    mains = files(GraphNode.is_entry_point.is_(True), GraphNode.node_id.like("%/__main__.py"))
    if any(origin(r) == "production" for r in await session.execute(mains)):
        found.append("entry_point")
    suite = (
        GraphNode.is_test.is_(True),
        or_(GraphNode.node_id.like("%.py"), GraphNode.node_id.like("%_test.go")),
    )
    # One test file with a stored origin of ours settles it; only an index
    # without stored origins reads every test file's path.
    stored = files(*suite, HealthFileMetric.code_origin.not_in(_NOT_OURS)).limit(1)
    has_tests = (await session.execute(stored)).first() is not None or any(
        origin(r) not in _NOT_OURS
        for r in await session.execute(files(*suite, HealthFileMetric.code_origin.is_(None)))
    )
    if not found and not has_tests:
        benches = await _stored_benchmarks(session, repository_id)
        origins = await load_origins(session, repository_id, (p for p, _ in benches))
        if any(o not in _NOT_OURS for o in origins.values()):
            found.append("benchmark")
    if has_tests:
        found.append("unit_test")
    return tuple(found)


def _within(root: str, path: str) -> bool:
    return not root or path == root or path.startswith(root + "/")


def _owned(root: str, roots: Collection[str], path: str) -> str | None:
    """*path* relative to the unit *root*; None outside it, in a deeper unit, or unsafe to print."""
    if not _within(root, path) or any(
        r != root and _within(root, r) and _within(r, path) for r in roots
    ):
        return None
    if not _SAFE_PATH.fullmatch(path) or ".." in path.split("/"):
        return None
    return path[len(root) + 1 :] if root else path


def _slug(name: str) -> str:
    return re.sub(r"[^\w-]+", "-", name).strip("-") or "run"


def _profile_path(name: str, suffix: str) -> str:
    return f"{PROFILE_DIR}/{_slug(name)}{suffix}"


def _named(root: str, name: str, suffix: str) -> tuple[str, str]:
    """A target's workload name and profile path, both carrying the unit *root*.

    Every target is named here, so two units never share a workload (one's
    run at HEAD would hide the other's) or a profile file.
    """
    workload = f"{root}: {name}" if root else name
    return workload, _profile_path(workload, suffix)


def _readable(input_format: str) -> bool:
    """Whether this build's ``evidence add`` reads *input_format* (the profile-format registry)."""
    from .profile_formats import profile_formats

    try:
        profile_formats.get(input_format)
    except (KeyError, LookupError):
        return False
    return True


def _from(root: str, path: str) -> str:
    """Repo-relative *path* as a command run in the unit *root* names it."""
    return posixpath.relpath(path, root) if root else path


def _fresh(path: str) -> str:
    """A step that removes an older profile at *path*, so a run that fails leaves nothing to ingest.

    git, since evidence needs a checkout anyway, spells it the same in sh and
    PowerShell; ``-x`` reaches the ignored ``.repowise/``.
    """
    return f"git clean -fqx -- {path}"


def evidence_ingest_line(profile: str, workload: str, workload_class: str, commit: str) -> str:
    """The ``evidence add`` line for a profile, run from the repository root."""
    return (
        f'repowise evidence add {profile} --workload "{workload}" '
        f"--class {workload_class} --commit {commit}"
    )


def _target(
    workload_class: str,
    name: str,
    *,
    cwd: str,
    command: str | None,
    profile: str | None,
    commit: str | None,
    input_format: str | None = None,
    prerequisites: Sequence[str] = (),
    reason: str | None = None,
    timed: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """One runtime target, JSON-ready.

    *timed* is the same workload without the profiler, for ``perf measure``;
    it rides under ``timed`` until :func:`runtime_targets` drops it, as
    *input_format* rides under ``format`` for :func:`offered_targets`.

    ``ingest`` is None when the run cannot be read yet: no commit, or
    *input_format* (what the recipe writes) is not a registered profile
    format in this build. ``next_call`` only takes targets with an ingest
    line and no ``reason``, so a recipe whose output would be refused, or one
    an agent must finish first (an entry point's arguments), never leads the loop.
    """
    ingest = None
    if command and profile and reason is None:
        if input_format is not None and not _readable(input_format):
            reason = f"This build of repowise does not read {input_format} files yet."
        elif commit:
            ingest = evidence_ingest_line(profile, name, workload_class, commit)
        else:
            reason = "Not a git checkout: evidence needs the commit it measured."
    return {
        "class": workload_class,
        "workload": name,
        "cwd": cwd,
        "prerequisites": list(prerequisites) if command else [],
        "command": command,
        "profile": profile,
        "ingest": ingest,
        "reason": reason,
        **({"timed": timed} if command and timed else {}),
        **({"format": input_format} if ingest and input_format else {}),
        **({"note": note} if command and note else {}),
    }


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def _env_prefix(stack: Stack) -> list[str]:
    """The launcher the unit's tests run under (``uv run``, ``poetry run``), or none.

    Read off its coverage command, so a profile runs in the environment its
    tests run in.
    """
    words = stack.test.split()
    if tuple(words[:2]) not in _LAUNCHERS:
        return []
    out, rest = words[:2], words[2:]
    while rest and rest[0].startswith("-"):
        option, rest = rest[0], rest[1:]
        # An option's value is the next word, unless that word starts the command.
        value = [] if "=" in option or not rest or rest[0].startswith("-") or rest[0] in _COMMAND_WORDS else [rest.pop(0)]
        # ``--with pytest-cov`` adds the coverage plugin, which a profile does not need.
        if [option, *value] != ["--with", "pytest-cov"]:
            out += [option, *value]
    return out


def _python_launcher(stack: Stack) -> tuple[str, list[str]]:
    """The ``python`` that runs with pyinstrument in the unit's environment, and its install steps."""
    prefix = _env_prefix(stack)
    if prefix[:2] == ["uv", "run"]:
        return " ".join([*prefix, "--with", "pyinstrument", "python"]), []
    python = " ".join([*prefix, "python"])
    # The unit's own test install (the project and its test dependencies), then the profiler.
    return python, [*stack.requires, f"{python} -m pip install pyinstrument"]


def _pyinstrument(launcher: str, out: str, run: str, ok: str = _CLEAN_EXITS) -> str:
    # *run* sits in a single-quoted literal that ``exec`` reads.
    quoted = run.replace("\\", "\\\\").replace("'", "\\'")
    return f'{launcher} -c "{_PYINSTRUMENT.format(out=out, run=quoted, ok=ok)}"'


def _python_targets(
    stack: Stack, repo_root: Path, inputs: RuntimeInputs, commit: str | None, roots: Collection[str]
) -> list[dict[str, Any]]:
    from ..generation.flow_entries import console_scripts

    root = stack.root
    cwd = str(repo_root / root) if root else str(repo_root)
    launcher, needs = _python_launcher(stack)
    out: list[dict[str, Any]] = []

    def profiled(cls: str, name: str, run: str, extra: Sequence[str] = ()) -> dict[str, Any]:
        workload, profile = _named(root, name, ".speedscope.json")
        suite = cls == "unit_test"
        command = _pyinstrument(launcher, _from(root, profile), run, _TEST_EXITS if suite else _CLEAN_EXITS)
        return _target(cls, workload, cwd=cwd, command=command, profile=profile, commit=commit,
                       input_format="speedscope", prerequisites=[*needs, *extra],
                       note=SUBSET_NOTE if suite else None)

    for name, (module, function) in sorted(console_scripts(repo_root / root).items()):
        argv = f"sys.argv=['{name}']+sys.argv[1:];"
        if function:
            run = f"{argv}from {module} import {function.split('.')[0]};sys.exit({function}())"
        else:
            run = f"{argv}import runpy;runpy.run_module('{module}',run_name='__main__',alter_sys=True)"
        out.append(profiled("entry_point", name, run))
    for path in inputs.entry_files:
        rel = _owned(root, roots, path)
        if rel is None or not rel.endswith("/__main__.py"):
            continue
        module = rel.removeprefix("src/").removesuffix("/__main__.py").replace("/", ".")
        if not _MODULE.fullmatch(module):
            continue
        run = f"sys.argv=['{module}']+sys.argv[1:];import runpy;runpy.run_module('{module}',run_name='__main__',alter_sys=True)"
        out.append(profiled("entry_point", f"python -m {module}", run))
    out += _served_apps(stack, repo_root, inputs, commit, roots)
    out = out[:PER_CLASS]

    if _declares_pytest_benchmark(repo_root, root):
        out.append(_pytest_benchmark(stack, repo_root, commit))
    if stack.test:
        # The paths the unit's coverage command runs (``tests/unit`` beside
        # ``tests/integration``), so the profile never collects slower suites,
        # unless the arguments name tests of their own (a directory, a ``.py``
        # file or a node id, not an option's value as in ``--ignore tests/slow``;
        # a file option's value such as ``--cov-config .coveragerc`` is neither):
        # then they pick what runs. The plugin keeps the failed count for the
        # profile (``n``).
        paths = [p for p in stack.test_paths if _SAFE_PATH.fullmatch(p)]
        tests = "(lambda p:os.path.isdir(p) or p.endswith('.py') and os.path.isfile(p))(x.split('::')[0])"
        run = (
            "a=sys.argv[1:];"
            f"import pytest;sys.exit(pytest.main(([] if any({tests} and not (j and a[j-1] in "
            f"{sorted(_VALUE_OPTIONS)!r}) for j,x in enumerate(a)) "
            f"else {paths!r})+a,plugins=[type('R',(),"
            "{'pytest_sessionfinish':lambda s,session:n.append(session.testsfailed)})()]))"
        )
        # A launcher outside uv whose installs do not bring pytest installs it here.
        extra = (
            [f"{launcher} -m pip install pytest"]
            if needs and not any(_PYTEST_WORD.search(n) for n in stack.requires) else []
        )
        out.append(profiled("unit_test", "python tests", run, extra))
    return out


#: pytest options whose value is the next word: a path after one is that option's, not one to run.
_VALUE_OPTIONS = frozenset({"-k", "-m", "-p", "-c", "-o", "--ignore", "--ignore-glob", "--deselect",
                            "--rootdir", "--confcutdir", "--basetemp", "--cov", "--junitxml"})
#: A module-level ASGI application: ``app = FastAPI(...)`` or ``Starlette(...)``, annotated or not.
_ASGI_APP = re.compile(
    r"(?m)^([A-Za-z_]\w*)\s*(?::[^=\n]+)?=\s*(?:fastapi\.|starlette\.applications\.)?(?:FastAPI|Starlette)\s*\("
)
_SERVE_REASON = (
    "Send the requests of a representative workload to the server while it runs (your client, a "
    "load tool or the app's own scripts), then stop it with Ctrl-C; an idle server measures only "
    "its startup."
)


def _asgi_apps(
    repo_root: Path, root: str, roots: Collection[str], entry_files: Sequence[str]
) -> list[tuple[str, str]]:
    """``(module, app)`` for each stored entry file of the unit that builds an ASGI app at module level.

    Read from disk; the entry-point flag already names a web app's ``main.py``,
    ``app.py``, ``server.py`` and ``asgi.py``. Ceiling: an app made by a
    factory function (``create_app()``) or under another framework is not
    found, and the server's own settings (host, port, reload) are uvicorn's
    defaults.
    """
    found: list[tuple[str, str]] = []
    for path in entry_files:
        rel = _owned(root, roots, path)
        if rel is None or not rel.endswith(".py") or rel.endswith("/__main__.py"):
            continue
        module = rel.removeprefix("src/").removesuffix(".py").replace("/", ".")
        if not _MODULE.fullmatch(module):
            continue
        try:
            text = (repo_root / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Of several apps in one file, the one named ``app`` is the one served.
        if names := _ASGI_APP.findall(text):
            found.append((module, "app" if "app" in names else names[0]))
    return found[:PER_CLASS]


def _served_apps(
    stack: Stack, repo_root: Path, inputs: RuntimeInputs, commit: str | None, roots: Collection[str]
) -> list[dict[str, Any]]:
    """Each module-level ASGI app of the unit, served by uvicorn under the profiler.

    uvicorn imports the app by name and serves it until Ctrl-C (or SIGTERM),
    then returns, so the profile is written as for any clean exit. The ingest
    line stands; the reason keeps the target from leading ``next_call``, as an
    entry point waiting for its arguments is kept.
    """
    root = stack.root
    launcher, needs = _python_launcher(stack)
    server = launcher.replace("--with pyinstrument", "--with pyinstrument --with uvicorn")
    install = [f"{launcher} -m pip install uvicorn"] if needs else []
    out: list[dict[str, Any]] = []
    for module, app in _asgi_apps(repo_root, root, roots, inputs.entry_files):
        workload, profile = _named(root, f"serve {module}:{app}", ".speedscope.json")
        run = f"import uvicorn;uvicorn.run('{module}:{app}')"
        target = _target("entry_point", workload, cwd=str(repo_root / root) if root else str(repo_root),
                         command=_pyinstrument(server, _from(root, profile), run), profile=profile,
                         commit=commit, input_format="speedscope", prerequisites=[*needs, *install])
        out.append({**target, "reason": target["reason"] or _SERVE_REASON, "serves": True})
    return out


def _pytest_benchmark(stack: Stack, repo_root: Path, commit: str | None) -> dict[str, Any]:
    """pytest-benchmark over the unit, in the unit's environment.

    Node ids are relative to pytest's rootdir, so this runs from the repository
    root; uv is pointed at the unit's project. Ceiling: a nested unit outside
    uv gets no install steps, since its install lines name paths from its own
    directory.
    """
    root = stack.root
    name, profile = _named(root, "pytest bench", ".json")
    prefix = _env_prefix(stack)
    if root and prefix[:2] == ["uv", "run"]:
        prefix[2:2] = ["--project", root]
    run = [*prefix, "pytest", *([root] if root else []), "--benchmark-only"]
    # Rounds fill a fixed time (``--benchmark-max-time``) whatever one call
    # costs; with none, each benchmark runs its minimum rounds, so wall time
    # follows the change. (``--benchmark-disable`` is refused with ``--benchmark-only``.)
    timed = " ".join([*run, "--benchmark-max-time=0", "--benchmark-min-rounds=10"])
    command = " ".join([*run, f"--benchmark-json={profile}"])
    return _target("benchmark", name, cwd=str(repo_root), command=command, profile=profile, commit=commit,
                   input_format="pytest-benchmark",
                   prerequisites=[*(() if root else stack.requires), _fresh(profile)], timed=timed)


def _declares_pytest_benchmark(repo_root: Path, root: str) -> bool:
    """Whether the unit's ``pyproject.toml`` or requirements name pytest-benchmark.

    Ceiling: a benchmark file alone is not enough, since asv and plain scripts
    use the same names and ``--benchmark-only`` needs the plugin.
    """
    base = repo_root / root
    names = ["pyproject.toml", *(p.name for p in base.glob("requirements*.txt"))]
    for name in names:
        try:
            if _PYTEST_BENCHMARK.search((base / name).read_text(encoding="utf-8", errors="ignore")):
                return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


def _go_package_arg(rel_dir: str) -> str:
    return f"./{rel_dir}" if rel_dir else "."


def _go_profiled(root: str, rel_dir: str, flags: str, name: str) -> tuple[str, str, str, str]:
    """The workload, the ``go test`` line profiling one package, its profile
    path, and the same line without the profile (what ``perf measure`` times)."""
    workload, profile = _named(root, name, ".pprof")
    binary = profile.removesuffix(".pprof") + ".test"
    parts = ["go test", flags, "-count=1", f"-o {_from(root, binary)}",
             f"-cpuprofile {_from(root, profile)}", _go_package_arg(rel_dir)]
    # ``-bench`` runs each benchmark for about a second whatever one call costs, so
    # wall time is timed over a fixed iteration count instead. Ceiling: one count
    # for every benchmark; a per-benchmark count needs its measured cost.
    timed = ["go test", flags.replace("-bench .", "-bench . -benchtime=10x"), "-count=1",
             _go_package_arg(rel_dir)]
    return workload, " ".join(p for p in parts if p), profile, " ".join(p for p in timed if p)


def _go_bench_results(root: str, rel_dir: str, cwd: str, commit: str | None) -> dict[str, Any]:
    """The package's benchmark results as ``go test`` prints them: time, bytes and allocations per op.

    ``go test`` has no flag that writes them to a file, so the line redirects
    its output; ``>`` reads the same in sh and PowerShell, and the converter
    reads the UTF-16 PowerShell 5 writes. A failed build prints no result
    lines, which ingest refuses.
    """
    name, results = _named(root, f"go bench results {_go_package_arg(rel_dir)}", ".txt")
    command = (
        f"go test '-run=^$' -bench . -benchmem -count=1 {_go_package_arg(rel_dir)} "
        f"> {_from(root, results)}"
    )
    return _target("benchmark", name, cwd=cwd, command=command, profile=results, commit=commit,
                   input_format="go-bench")


def _go_targets(
    stack: Stack,
    repo_root: Path,
    inputs: RuntimeInputs,
    commit: str | None,
    mains: Iterable[str],
    roots: Collection[str],
) -> list[dict[str, Any]]:
    # Ceiling: ``go test -o`` and ``-cpuprofile`` need ``.repowise/`` to exist,
    # which it does once the repository is indexed with a local store.
    root = stack.root
    cwd = str(repo_root / root) if root else str(repo_root)
    out: list[dict[str, Any]] = []
    for main in mains:
        rel = "" if main == root else _owned(root, roots, main)
        if rel is None:
            continue
        workload, _ = _named(root, f"go run {_go_package_arg(rel)}", "")
        out.append(_target(
            "entry_point", workload, cwd=cwd, command=None, profile=None,
            commit=commit,
            reason="go run takes no profiler flag; profile this program through a benchmark or test.",
        ))
    out = out[:PER_CLASS]
    bench_dirs = sorted({
        posixpath.dirname(rel) for p, _ in inputs.benchmarks
        if p.endswith("_test.go") and (rel := _owned(root, roots, p)) is not None
    })
    for rel_dir in bench_dirs[:PER_CLASS]:
        # Benchmarks only: no unit test runs inside the profile.
        name, command, profile, timed = _go_profiled(
            root, rel_dir, "'-run=^$' -bench . -benchmem", f"go bench {_go_package_arg(rel_dir)}"
        )
        out.append(_target("benchmark", name, cwd=cwd, command=command, profile=profile, commit=commit,
                           input_format="pprof", prerequisites=[_fresh(_from(root, profile))], timed=timed))
        out.append(_go_bench_results(root, rel_dir, cwd, commit))
    # The package with the most test files runs the most code; depth breaks ties.
    test_counts = Counter(
        posixpath.dirname(rel) for p in inputs.test_files
        if p.endswith("_test.go") and (rel := _owned(root, roots, p)) is not None
    )
    test_dirs = sorted(test_counts, key=lambda d: (-test_counts[d], d.count("/") + bool(d), d))
    if test_dirs:
        name, command, profile, _ = _go_profiled(
            root, test_dirs[0], "", f"go test {_go_package_arg(test_dirs[0])}"
        )
        out.append(_target("unit_test", name, cwd=cwd, command=command, profile=profile, commit=commit,
                           input_format="pprof", prerequisites=[_fresh(_from(root, profile))]))
    return out


# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------


def _node_targets(
    stack: Stack, repo_root: Path, commit: str | None, roots: Collection[str]
) -> list[dict[str, Any]]:
    from ..ingestion.always_run import own_code

    root = stack.root
    cwd = str(repo_root / root) if root else str(repo_root)
    manifest = posixpath.join(root, "package.json") if root else "package.json"

    def read(path: str) -> str | None:
        try:
            return (repo_root / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

    out: list[dict[str, Any]] = []
    for path in sorted(own_code([manifest], read).bin_files)[:PER_CLASS]:
        rel = _owned(root, roots, path)
        if rel is None:
            continue
        # A directory, not one file: ``--cpu-prof`` writes each process and
        # worker thread its own profile there (a fixed name would let a child
        # overwrite the parent's), and ``evidence add`` merges the directory
        # into one run. Cleaned first, so an older run's files never join it.
        name, profile = _named(root, f"node {rel}", "")
        if path.endswith(_TS):
            out.append(_target("entry_point", name, cwd=cwd, command=None, profile=None, commit=commit,
                               reason="A TypeScript profile's positions are in the compiled JavaScript; "
                                      "repowise does not map them back yet."))
            continue
        if not path.endswith(_JS):
            continue
        command = f"node --cpu-prof --cpu-prof-dir={_from(root, profile)} {rel}"
        out.append(_target("entry_point", name, cwd=cwd, command=command, profile=profile, commit=commit,
                           input_format="cpuprofile", prerequisites=[_fresh(_from(root, profile))]))
    return out


def runtime_targets(
    stack: Stack,
    repo_root: Path,
    inputs: RuntimeInputs,
    commit: str | None,
    go_mains: Sequence[str] = (),
    unit_roots: Collection[str] = (),
) -> list[dict[str, Any]]:
    """One unit's runtime targets: entry points, then benchmarks, then the unit-test suite.

    *unit_roots* are every unit's root: a file under a deeper unit is that
    unit's. A unit whose root a shell could misread gets none.
    """
    found = _unit_targets(stack, repo_root, inputs, commit, go_mains, unit_roots)
    return [{k: v for k, v in _awaiting_args(t).items() if k not in _INTERNAL} for t in found]


#: Target keys for the callers in this package, never printed. ``serves``: a
#: server, run with no arguments and driven by requests instead.
_INTERNAL = frozenset({"timed", "format", "serves"})


def offered_targets(
    stacks: Sequence[Stack],
    repo_root: Path,
    inputs: RuntimeInputs,
    commit: str | None,
    go_mains: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Every unit's runtime targets ``evidence setup`` prints an ingest line for.

    The one answer to whether this repository has anything runtime to run:
    the plan lists these targets, ``next`` asks for runtime evidence only
    while there is one, and ``perf check`` names a benchmark target only when
    one is here. Each keeps ``format``, what its command writes. An entry
    point waiting for its arguments counts: the plan names it under
    ``optional``.
    """
    roots = {s.root for s in stacks}
    return [
        t for stack in stacks
        for t in _unit_targets(stack, repo_root, inputs, commit, go_mains, roots)
        if is_offered(t)
    ]


def is_offered(target: Mapping[str, Any]) -> bool:
    """Whether the plan offers *target*: it prints an ingest line for its run."""
    return bool(target["ingest"])


def _unit_targets(
    stack: Stack,
    repo_root: Path,
    inputs: RuntimeInputs,
    commit: str | None,
    go_mains: Sequence[str],
    unit_roots: Collection[str],
) -> list[dict[str, Any]]:
    if not _SAFE_PATH.fullmatch(stack.root):
        return []
    if stack.language == "python":
        found = _python_targets(stack, repo_root, inputs, commit, unit_roots)
    elif stack.language == "go":
        found = _go_targets(stack, repo_root, inputs, commit, go_mains, unit_roots)
    elif stack.language == "node":
        found = _node_targets(stack, repo_root, commit, unit_roots)
    else:
        found = []
    return sorted(found, key=lambda t: CLASS_ORDER.index(t["class"]))


def _awaiting_args(target: dict[str, Any]) -> dict[str, Any]:
    """An entry point with the arguments left for the agent: run as printed, it measures its usage screen.

    Its ingest line names the workload per arguments (``app <args>``), so an
    ``init`` run and an ``update`` run of one program never share a history.
    """
    if target["class"] != "entry_point" or not target["command"] or target.get("serves"):
        return target
    ingest = target["ingest"]
    if ingest:
        workload = target["workload"]
        ingest = ingest.replace(f'--workload "{workload}"', f'--workload "{workload} {ARGS_PLACEHOLDER}"')
    return {
        **target,
        "command": f"{target['command']} {ARGS_PLACEHOLDER}",
        "ingest": ingest,
        "reason": target["reason"] or _ARGS_REASON,
    }


#: The workload classes a timing may come from. A unit-test suite runs far more
#: than the change, and an entry point waits for its arguments
#: (``ARGS_PLACEHOLDER``): run without them it times its usage screen. Neither
#: leads ``next_call``, and neither is timed.
TIMED_CLASSES = ("benchmark",)
#: The language family (:func:`~repowise.core.analysis.health.refactoring.language_family.language_family`)
#: each stack with runtime targets builds.
_STACK_FAMILY = {"python": "python", "go": "go", "node": "js"}


def timed_target(
    stacks: Sequence[Stack],
    repo_root: Path,
    inputs: RuntimeInputs,
    path: str,
    go_mains: Sequence[str] = (),
) -> dict[str, Any] | None:
    """The workload ``perf measure`` times for a change to *path*, or None.

    The first benchmark :func:`runtime_targets` lists for the deepest unit of
    *path*'s language that holds it, without the profiler:
    ``{workload, class, command, cwd}``, *cwd* repo-relative (``""`` the root).
    """
    from ..analysis.health.refactoring.language_family import language_family

    family = language_family(path)
    owners = [s for s in stacks if _STACK_FAMILY.get(s.language) == family and _within(s.root, path)]
    if not owners:
        return None
    root = max(len(s.root) for s in owners)
    roots = {s.root for s in stacks}
    for stack in (s for s in owners if len(s.root) == root):
        for target in _unit_targets(stack, repo_root, inputs, None, go_mains, roots):
            if target["class"] in TIMED_CLASSES and target.get("timed"):
                cwd = Path(target["cwd"]).relative_to(repo_root).as_posix()
                return {
                    "workload": target["workload"],
                    "class": target["class"],
                    "command": target["timed"],
                    "cwd": "" if cwd == "." else cwd,
                }
    return None


__all__ = [
    "CLASS_ORDER",
    "EVIDENCE_STATUS_COMMAND",
    "PROFILE_DIR",
    "RuntimeInputs",
    "evidence_ingest_line",
    "is_offered",
    "load_runtime_inputs",
    "offered_targets",
    "runtime_targets",
    "stored_target_classes",
    "timed_target",
]
