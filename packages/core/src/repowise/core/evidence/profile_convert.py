"""A profile run joined to the indexed code, written as a ``repowise-evidence-v1`` document.

Every format in :mod:`.profile_formats` comes through here, so frames are
joined one way only: :func:`repowise.core.frame_resolver.resolve_frames`
(the checkout root the caller names, a second signal, folding, a reason per
skipped frame; never a path suffix). The document then goes through the
same ingest as a hand-written one.

Per symbol the run gets both views:

- inclusive (``total``): every stack the symbol is on, once per stack even
  when it recurses. Context only: a function that only calls things has a
  large inclusive share and costs nothing itself.
- self, the cost a run is ranked by: stacks whose innermost maintained repo
  frame is the symbol. Frames below it that nobody here maintains (built-ins,
  the standard library, dependencies, vendored or generated files) are
  charged to it, so time spent in I/O, a C extension or a library stays with
  the code that called it. A wait is not: a stack blocked in an event loop's
  select, a thread or process join, a queue get, a future's result or a sleep
  (:data:`WAIT_FRAMES`) charges no self time, since the time went to whatever
  ran elsewhere, not to the caller. A stack whose innermost repo frame was
  skipped charges no self time at all.

The run's time is split four ways, each stack into exactly one:

- ``waits``: blocked in a wait frame below its innermost maintained repo frame
  (or anywhere, with no repo frame);
- ``libraries``: not waiting, and a library frame (:func:`is_library_frame`:
  a dependency or the standard library, GOROOT, CPython's ``Lib``, the JDK and
  Node's ``node:`` modules included) runs below its innermost repo frame, or
  anywhere on a stack with no repo frame;
- ``repo_self``: neither, and the innermost repo frame is a production
  function (``code_origin``): time in production repo code itself, the
  built-ins and runtime it called included;
- the rest: test or tooling code, module bodies, unresolved frames, and
  stacks with no repo frame that run only the runtime.

Import time is a row of its own. A Python module body (``<module>``: a
file's top level, run when it is imported) and the repo modules it imports in
turn, with nothing but import machinery and other module bodies between them,
are one import: its row is the outermost module's, under the reserved id
``<path>::<module>`` (:data:`MODULE_FUNCTION`, no line), ranked by self time
with the functions. Its self time is every stack whose innermost maintained
repo frame is one of those module bodies: their top-level code, the libraries
and standard-library modules they import, and the import machinery loading
them. A function on the way breaks the chain: a module a function imports
starts a row of its own, and a library a function imports stays the
function's (kind ``import``). The row's ``inside`` names what the import spent
its time on, by top-level package for a library or the standard library (kind
``library``: ``sqlalchemy``, ``json``), by name for a repo function run at
import time (kind ``repo``), ``own`` for the modules' own top-level code and
``import`` for the machinery itself. Ceiling: a Go package initializer or a
JavaScript module wrapper has no row (no probe profile shows them above the
share rule); its self time counts under ``self_unattributed`` as
``module_body``.

``explained`` (weight on stacks with a production frame anywhere) is still
written for older readers; it says nothing about cost, since a CLI's entry
function is on every stack.

Per symbol the run also gets what its time went to and what drove it, each as
a share of its inclusive weight, up to :data:`TOP_SPLIT` of each:

- ``inside``: the frame directly below the symbol's innermost occurrence on
  each stack, by ``name`` and ``kind`` (``format_v1.INSIDE_KINDS``): ``own`` when
  the symbol is the leaf, ``repo`` for a maintained repo frame, else by the
  unmaintained frames down to the next repo frame: ``wait`` when one blocks
  (:func:`is_wait_frame`, so a subprocess or an event loop the symbol waits on
  is a wait), ``import`` when one is import machinery, ``library`` when the
  frame is one (:func:`is_library_frame`, the rule the run's split uses),
  ``native`` otherwise (a built-in, the Go runtime, assembly). A callee seen
  with several kinds keeps the one with the most weight. This is
  ``go tool pprof -peek``'s callee list with the flat time as ``own``. A callee
  also names its heaviest ``leaf``, the frame its stacks end in (``name``,
  ``kind`` as above, ``share``), when that is not the callee itself: what a
  library call spends its time on.
- ``drivers``: the nearest maintained repo frame above the symbol's outermost
  occurrence (the code that drove it, through any library in between), else
  the frame directly above; a root frame has none.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from repowise.core.code_origin import is_vendored_or_generated_path, path_origin
from repowise.core.frame_resolver import frame_paths, resolve_frames

from .format_v1 import (
    _LONG_TEXT,
    EVIDENCE_FORMAT,
    MAX_SPLIT_ITEMS,
    MODULE_FUNCTION,
    EvidenceFormatError,
)

if TYPE_CHECKING:
    from repowise.core.frame_resolver import FrameResolution, ProfileFrame
    from repowise.core.workspace.repo_index import IndexedSymbol

    from .profile_formats import ProfileRun, Stack

#: The v1 reason for a frame outside the checkout, which the store counts apart.
_OUTSIDE = "outside_repo"
#: Frame outcomes that are code nobody here maintains: charged to the repo caller.
_UNMAINTAINED = frozenset({"outside_root", "vendored", "generated"})

#: Frames that block rather than work, by the standard-library file that
#: defines them (path suffix) and the function's last name segment. Only frames
#: below a stack's innermost maintained repo frame are checked, so a repo file
#: with the same name is never one. An event loop's turn counts too: below the
#: repo frame that started the loop, it is the loop's own bookkeeping and
#: library callbacks, not that caller's work (a repo coroutine the loop runs is
#: a repo frame of its own, so its stacks never reach this check).
WAIT_FRAMES: dict[str, frozenset[str]] = {
    "selectors.py": frozenset({"select"}),
    "asyncio/base_events.py": frozenset({"run_forever", "_run_once"}),
    "threading.py": frozenset({"wait", "wait_for", "join", "_wait_for_tstate_lock", "acquire"}),
    "queue.py": frozenset({"get", "put", "join"}),
    "concurrent/futures/_base.py": frozenset({"result", "exception", "wait", "as_completed"}),
    "multiprocessing/connection.py": frozenset(
        {"wait", "poll", "_poll", "recv", "recv_bytes", "_recv", "_recv_bytes"}
    ),
    "multiprocessing/pool.py": frozenset({"get", "wait", "join"}),
    "multiprocessing/queues.py": frozenset({"get"}),
    "multiprocessing/process.py": frozenset({"join"}),
    "multiprocessing/popen_fork.py": frozenset({"wait", "poll"}),
    "subprocess.py": frozenset({"wait", "_wait", "_try_wait", "communicate", "_communicate"}),
}
#: Runtime frames with no source file that block, by the name profilers give
#: them: Python built-ins (pyinstrument, py-spy), Go's scheduler, V8's idle.
WAIT_FUNCTIONS = frozenset(
    {
        "time.sleep", "select.select", "poll.poll", "epoll.poll", "devpoll.poll",
        "kqueue.control", "lock.acquire", "RLock.acquire", "os.waitpid", "os.wait",
        "os.wait3", "os.wait4", "_winapi.WaitForSingleObject",
        "_winapi.WaitForMultipleObjects", "runtime.gopark", "runtime.goparkunlock",
        "runtime.notetsleepg", "time.Sleep", "(idle)", "Atomics.wait",
    }
)  # fmt: skip


#: Frames that start or wait on another process, whose own work a profile of
#: this process does not hold: by the standard-library path (a fragment of it)
#: and by the name a runtime frame with no source file carries. Python's
#: multiprocessing, process pools and subprocess; Go's os/exec; Node's
#: child_process, cluster and worker threads (``--cpu-prof`` writes each
#: process and worker thread to a file of its own). ``multiprocessing.dummy``
#: runs threads, so it is left out.
CHILD_PROCESS_PATHS = (
    "/multiprocessing/", "/concurrent/futures/process.py", "/subprocess.py",
    "node:child_process", "node:internal/child_process", "node:cluster",
    "node:internal/cluster/", "node:worker_threads", "node:internal/worker",
)  # fmt: skip
CHILD_PROCESS_FUNCTIONS = frozenset(
    {
        "posix.fork", "os.fork", "posix.posix_spawn", "os.posix_spawn", "os.waitpid",
        "_winapi.CreateProcess", "os.StartProcess", "syscall.forkExec", "syscall.StartProcess",
    }
)  # fmt: skip
#: Go function-name prefixes: ``os/exec`` and the process handle it waits on.
_CHILD_PROCESS_PREFIXES = ("os/exec.", "os.(*Process).")

#: Entries of ``inside`` and of ``drivers`` kept per symbol, costliest first.
TOP_SPLIT = MAX_SPLIT_ITEMS
#: Where a Python package's top-level name starts in a library path: after an
#: installer's directory, else after the standard library's.
_INSTALLED_RE = re.compile(r"(?:^|/)(?:site|dist)-packages/(?P<top>[^/]+)")
_STDLIB_RE = re.compile(r"(?:^|/)(?:Lib|lib(?:64)?/python\d+(?:\.\d+)?)/(?P<top>[^/]+)")
#: Import machinery, by path fragment: CPython's frozen importlib, Node's module loader.
_IMPORT_PATHS = ("importlib._bootstrap", "node:internal/modules/")
_IMPORT_FUNCTIONS = frozenset({"__import__", "importlib.import_module", "import_module"})


def is_import_frame(frame: ProfileFrame) -> bool:
    """Whether *frame* is import machinery (:data:`_IMPORT_PATHS`)."""
    path = frame.path.replace("\\", "/")
    return frame.function in _IMPORT_FUNCTIONS or any(p in path for p in _IMPORT_PATHS)


def package_name(path: str) -> str:
    """The top-level package a library module file belongs to, as ``import`` names it.

    ``.../site-packages/sqlalchemy/orm/__init__.py`` -> ``sqlalchemy``,
    ``.../Lib/json/decoder.py`` -> ``json``, ``.../lib/python3.12/typing.py`` ->
    ``typing``, ``<frozen posixpath>`` -> ``posixpath``. A path under neither
    gives its file's module name.
    """
    norm = path.replace("\\", "/").rstrip("/")
    if norm.startswith("<frozen "):  # a frozen standard-library module
        return norm.removeprefix("<frozen ").rstrip(">").split(".")[0]
    match = _INSTALLED_RE.search(norm) or _STDLIB_RE.search(norm)
    if match:
        top = match.group("top")
    else:
        parts = norm.rsplit("/", 2)
        top = parts[-2] if parts[-1] == "__init__.py" and len(parts) > 1 else parts[-1]
    return top.removesuffix(".py") or top


def _written_name(name: str) -> str:
    """*name* as the v1 parser reads it: no control characters, at most :data:`_LONG_TEXT`."""
    return "".join(c if ord(c) >= 0x20 else " " for c in name).strip()[:_LONG_TEXT] or "(unknown)"


def is_native_frame(frame: ProfileFrame) -> bool:
    """Whether *frame* runs code with no source: a built-in or the language runtime.

    A frame with no file (V8's natives, a collapsed stack's bare name), one
    pyinstrument files under ``<built-in>`` or cProfile under ``~``, assembly
    (Go's ``memeqbody``), and Go's ``runtime`` package.
    """
    path = frame.path
    if not path:
        # A bare name: the runtime's when it is a V8 marker (``(program)``), Go's
        # ``runtime`` or an unqualified native (``writev``); a qualified name a
        # collapsed stack gives a library frame (``java/util/HashMap.get``,
        # ``std::sort``, ``gorm.(*DB).First``) is a library.
        name = frame.function
        if name.startswith(("(", "runtime.")):
            return True
        return not any(mark in name for mark in (".", "/", "::"))
    if path == "~" or path.startswith("<built-in") or path.lower().endswith((".s", ".asm")):
        return True
    return frame.language == "go" and frame.function.startswith("runtime.")


#: The JDK's packages, as a collapsed stack with no files names them (dots or slashes).
_JDK_NAMES = (
    "java.", "java/", "javax.", "javax/", "jdk.", "jdk/", "sun.", "sun/", "com.sun.", "com/sun/",
)  # fmt: skip


def is_library_frame(frame: ProfileFrame, reason: str | None) -> bool:
    """Whether *frame*, joined with skip *reason*, runs library code: code nobody
    here maintains that has a source, so a dependency or the standard library.

    A vendored or generated file is always one. Outside the checkout a frame with
    a file is one unless it is the runtime or a built-in (:func:`is_native_frame`)
    or import machinery (:func:`is_import_frame`, import time: the module it
    loads decides): a module cache or ``site-packages``, GOROOT, CPython's
    ``Lib`` and frozen modules, Node's ``node:`` modules. A frame with no file
    (a collapsed stack's bare name, or a placeholder such as ``<string>`` for
    code built at run time) says only its name, which places it only when it is
    the JDK's (:data:`_JDK_NAMES`, ``java/util/HashMap.get``); an application's
    own pathless frames are not known to be library code.
    """
    if reason in ("vendored", "generated"):
        return True
    if reason != "outside_root" or is_native_frame(frame) or is_import_frame(frame):
        return False
    path = frame.path
    has_file = bool(path) and (not path.startswith("<") or path.startswith("<frozen "))
    return has_file or frame.function.startswith(_JDK_NAMES)


def is_child_process_frame(frame: ProfileFrame) -> bool:
    """Whether *frame* starts or waits on another process (:data:`CHILD_PROCESS_PATHS`)."""
    if frame.function in CHILD_PROCESS_FUNCTIONS or frame.function.startswith(
        _CHILD_PROCESS_PREFIXES
    ):
        return True
    path = frame.path.replace("\\", "/")
    if "/multiprocessing/dummy/" in path or is_vendored_or_generated_path(path):
        return False
    return any(part in path for part in CHILD_PROCESS_PATHS)


def merge_runs(runs: Sequence[ProfileRun]) -> ProfileRun:
    """Several profiles of one run (a parent and its workers) as one run.

    Frames the files share become one frame and equal stacks add up, so the
    merged run reads like one file that sampled every process. The runs must
    count in one unit and come from one tool: samples of different units or
    samplers do not add up (:class:`EvidenceFormatError` otherwise). The
    clock is checked by the caller, which knows the one the user named.
    """
    from .profile_formats.model import ProfileRun, StackTally

    if len(runs) == 1:
        return runs[0]
    units = sorted({run.unit for run in runs})
    if len(units) > 1:
        raise EvidenceFormatError(
            f"the profiles count in different units ({', '.join(units)}), so their "
            "weights do not add up",
            "profile every process with the same profiler and options, then add them together",
        )
    tools = sorted({run.tool_name for run in runs if run.tool_name})
    if len(tools) > 1:
        raise EvidenceFormatError(
            f"the profiles come from different tools ({', '.join(tools)}), whose samples "
            "do not add up",
            "profile every process with the same profiler, then add them together",
        )
    index: dict[ProfileFrame, int] = {}
    tally = StackTally()
    skipped: Counter[str] = Counter()
    for run in runs:
        local = [index.setdefault(frame, len(index)) for frame in run.frames]
        for stack in run.stacks:
            tally.add(tuple(local[i] for i in stack.frames), stack.weight, stack.samples)
        skipped.update(run.skipped)
    failed = [run.tests_failed for run in runs if run.tests_failed is not None]
    return ProfileRun(
        frames=tuple(index),
        stacks=tally.stacks(),
        unit=units[0],
        value_kind=next((run.value_kind for run in runs if run.value_kind), None),
        tool_name=tools[0] if tools else None,
        tool_version=next((run.tool_version for run in runs if run.tool_version), None),
        skipped=dict(skipped),
        tests_failed=sum(failed) if failed else None,
    )


def is_wait_frame(frame: ProfileFrame) -> bool:
    """Whether *frame* blocks (:data:`WAIT_FRAMES`, :data:`WAIT_FUNCTIONS`)."""
    if frame.function in WAIT_FUNCTIONS:
        return True
    path = frame.path.replace("\\", "/")
    name = frame.function.rsplit(".", 1)[-1]
    return any(
        name in names and (path.endswith("/" + suffix))
        for suffix, names in WAIT_FRAMES.items()
    ) and not is_vendored_or_generated_path(path)


@dataclass(frozen=True)
class ProfileConversion:
    """The v1 document for one run and how its frames joined."""

    document: dict[str, Any]
    unit: str
    #: The run's whole weight, empty stacks included; None when ordering only.
    total: float | None
    #: Weight on stacks with a resolved production frame; None when ordering only.
    explained: float | None
    #: Frame-occurrence weight by outcome: ``resolved`` or the skip reason.
    frame_time: dict[str, float] = field(default_factory=dict)
    #: Self weight charged to no symbol: ``module_body`` or the innermost repo frame's skip reason.
    self_unattributed: dict[str, float] = field(default_factory=dict)
    #: Weight in production repo code itself, waits and libraries excluded.
    repo_self: float = 0.0
    #: Weight blocked in a wait frame.
    waits: float = 0.0
    #: Weight in library code (a dependency or the standard library) below the
    #: innermost repo frame.
    libraries: float = 0.0
    #: Weight starting or waiting on another process below the innermost repo
    #: frame (:func:`is_child_process_frame`): that process's own work is not in
    #: this profile. Overlaps ``waits``; not part of the four-way split.
    child_processes: float = 0.0

    @property
    def explained_share(self) -> float | None:
        if self.total is None or self.explained is None:
            return None
        return self.explained / self.total if self.total > 0 else None

    def share(self, value: float) -> float | None:
        """*value* over the run's weight, rounded; None for an empty run."""
        return round(value / self.total, 4) if self.total is not None and self.total > 0 else None

    @property
    def repo_frame_time(self) -> float:
        """Frame-occurrence weight under the checkout root, resolved or not."""
        return sum(v for k, v in self.frame_time.items() if k != "outside_root")

    @property
    def skip_rate(self) -> float | None:
        """Share of repo frame time that did not resolve; None with no repo frames or ordering-only."""
        if self.total is None:
            return None
        repo = self.repo_frame_time
        return (repo - self.frame_time.get("resolved", 0.0)) / repo if repo > 0 else None

    def summary(self) -> dict[str, Any]:
        repo = self.repo_frame_time
        skipped = (
            {
                k: round(v / repo, 4)
                for k, v in sorted(self.frame_time.items(), key=lambda kv: -kv[1])
                if k not in ("resolved", "outside_root") and repo > 0
            }
            if self.total is not None
            else {}
        )
        share, rate = self.explained_share, self.skip_rate
        return {
            "total": self.total,
            "unit": self.unit,
            "repo_self_share": self.share(self.repo_self),
            "wait_share": self.share(self.waits),
            "library_share": self.share(self.libraries),
            "child_process_share": self.share(self.child_processes),
            "explained_share": None if share is None else round(share, 4),
            "skip_rate": None if rate is None else round(rate, 4),
            "skipped_share_by_reason": skipped,
            "self_unattributed": (
                {
                    k: round(v / self.total, 4)
                    for k, v in sorted(self.self_unattributed.items())
                }
                if self.total is not None and self.total > 0
                else {}
            ),
            "symbols": len(self.document["symbols"]),
        }


def convert_profile(
    run: ProfileRun,
    *,
    root: str,
    symbols_by_file: Mapping[str, Sequence[IndexedSymbol]],
    dirty_paths: Collection[str] = (),
    origins: Mapping[str, str] | None = None,
    read_lines: Callable[[str], Sequence[str] | None] | None = None,
    value_kind: str | None = None,
    map_sources: bool = True,
) -> ProfileConversion:
    """*run*'s frames joined to the symbols at *root*, as a v1 profile document.

    The arguments after *run* are :func:`resolve_frames`'; *value_kind*
    (``cpu`` or ``wall``) overrides what the format says. The ingest path sets
    *map_sources* to False after mapping before loading original symbols.
    """
    kind = value_kind or run.value_kind
    if kind is None:
        raise EvidenceFormatError(
            "the profile does not say whether it timed CPU or wall-clock",
            "name the clock the profiler sampled: cpu or wall",
        )
    from .source_maps import remap_profile

    if map_sources:
        run = remap_profile(run, root=root, read_lines=read_lines, origins=origins,
                            dirty_paths=dirty_paths)
    refuse_compiled_positions(run, root, read_lines, origins)
    resolution = resolve_frames(
        run.frames,
        root=root,
        symbols_by_file=symbols_by_file,
        dirty_paths=dirty_paths,
        origins=origins,
        read_lines=read_lines,
    )
    tally = _Tally(run.frames, resolution, origins or {})
    for stack in run.stacks:
        tally.add(stack)
    counted = any(s.samples is not None for s in run.stacks)
    entries = tally.entries(counted=counted)
    skipped = Counter(run.skipped)
    for reason, count in resolution.skipped.items():
        skipped[_OUTSIDE if reason == "outside_root" else reason] += count
    document: dict[str, Any] = {
        "format": EVIDENCE_FORMAT,
        "kind": "profile",
        "value_kind": kind,
        "unit": run.unit,
        **(
            {}
            if run.ordering_only
            else {
                "total": tally.total,
                "explained": tally.explained,
                "repo_self": tally.buckets["repo_self"],
                "waits": tally.buckets["waits"],
                "libraries": tally.buckets["libraries"],
            }
        ),
        "skipped": dict(sorted(skipped.items())),
        "symbols": entries,
    }
    # The run's sample count only when every stack counted its samples: a
    # partial count would put the wrong ``n`` under every share's interval.
    if not run.ordering_only and run.stacks and all(s.samples is not None for s in run.stacks):
        document["total_samples"] = tally.samples
    if run.tool_name:
        document["tool"] = {"name": run.tool_name, "version": run.tool_version}
    return ProfileConversion(
        document,
        run.unit,
        None if run.ordering_only else tally.total,
        None if run.ordering_only else tally.explained,
        dict(tally.frame_time),
        dict(tally.self_unattributed),
        tally.buckets["repo_self"],
        tally.buckets["waits"],
        tally.buckets["libraries"],
        tally.child_processes,
    )


class _Tally:
    """Per-symbol inclusive and self weight, the run's split and frame time over the stacks."""

    def __init__(
        self,
        frames: Sequence[ProfileFrame],
        resolution: FrameResolution,
        origins: Mapping[str, str],
    ) -> None:
        self._reasons = resolution.reasons
        # Per frame, once: nobody here maintains it, it is a library, it blocks.
        self._unmaintained = [r in _UNMAINTAINED for r in self._reasons]
        self._library = [is_library_frame(f, r) for f, r in zip(frames, self._reasons, strict=True)]
        self._wait = [is_wait_frame(f) for f in frames]
        self._child = [is_child_process_frame(f) for f in frames]
        self._import = [is_import_frame(f) for f in frames]
        self._native = [is_native_frame(f) for f in frames]
        self._python = [f.language == "python" for f in frames]
        self._frames = frames
        self._resolved = {r.index: r for r in resolution.resolved}
        self._origins = origins
        #: Per row id, its file and symbol (None for a module body's row).
        self._symbols: dict[str, tuple[str, IndexedSymbol | None]] = {}
        self._inclusive: defaultdict[str, float] = defaultdict(float)
        self._own: defaultdict[str, float] = defaultdict(float)
        self._inclusive_n: Counter[str] = Counter()
        self._own_n: Counter[str] = Counter()
        self._calls: Counter[str] = Counter()
        for r in resolution.resolved:
            if not r.folded and r.symbol is not None and r.index < len(frames):
                frame = frames[r.index]
                if frame.calls is not None:
                    self._calls[r.symbol.symbol_id] += frame.calls
        #: Per symbol, weight by callee key (``inside``) and by caller key (``drivers``).
        self._inside: dict[str, Counter[tuple[Any, ...]]] = {}
        self._inside_kinds: dict[tuple[str, tuple[Any, ...]], Counter[str]] = {}
        #: Per symbol and callee, weight by the stack's leaf frame, and each leaf's kind.
        self._leaves: dict[tuple[str, tuple[Any, ...]], Counter[tuple[Any, ...]]] = {}
        self._leaf_kind: dict[tuple[Any, ...], str] = {}
        self._drivers: dict[str, Counter[tuple[Any, ...]]] = {}
        #: What a callee or caller key is written as.
        self._named: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.frame_time: defaultdict[str, float] = defaultdict(float)
        #: Self weight whose innermost repo frame has no symbol row, by why.
        self.self_unattributed: defaultdict[str, float] = defaultdict(float)
        #: The run's weight in ``repo_self``, ``waits`` and ``libraries``.
        self.buckets: Counter[str] = Counter({"repo_self": 0.0, "waits": 0.0, "libraries": 0.0})
        self.child_processes = 0.0
        self.total = 0.0
        self.explained = 0.0
        self.samples = 0

    def _production(self, path: str) -> bool:
        return (self._origins.get(path) or path_origin(path)) == "production"

    def _rows_on(self, frames: tuple[int, ...]) -> list[str | None]:
        """Per stack position, the row a maintained repo frame is charged to: its
        symbol's, or for a Python module body the import row it belongs to (the
        outermost module body of its chain, see the module docstring)."""
        rows: list[str | None] = [None] * len(frames)
        chain: str | None = None
        for k, i in enumerate(frames):
            if self._unmaintained[i]:
                continue
            hit = self._resolved.get(i)
            if hit is None or (hit.symbol is None and not self._python[i]):
                chain = None
            elif hit.symbol is not None:
                rows[k], chain = hit.symbol.symbol_id, None
            elif hit.folded:
                # A module-level lambda or comprehension: import time inside an
                # import, but never a row of its own (one a function calls at
                # run time is not an import).
                rows[k] = chain
            else:
                chain = chain or f"{hit.path}::{MODULE_FUNCTION}"
                rows[k] = chain
        return rows

    def add(self, stack: Stack) -> None:
        weight, samples = stack.weight, stack.samples or 0
        self.total += weight
        self.samples += samples
        # Per symbol on the stack: its outermost and innermost position.
        first: dict[str, int] = {}
        last: dict[str, int] = {}
        in_production = False
        rows = self._rows_on(stack.frames)
        for k, i in enumerate(stack.frames):
            hit = self._resolved.get(i)
            self.frame_time["resolved" if hit is not None else str(self._reasons[i])] += weight
            if hit is None:
                continue
            in_production = in_production or self._production(hit.path)
            sid = rows[k]
            if sid is not None:
                last[sid] = k
                if sid not in first:
                    first[sid] = k
                    self._symbols[sid] = (hit.path, hit.symbol)
                    self._inclusive[sid] += weight
                    self._inclusive_n[sid] += samples
        if in_production:
            self.explained += weight
        self._charge(stack.frames, rows, weight, samples)
        if first and weight > 0:
            self._split(stack.frames, first, last, weight)

    def _split(
        self, frames: tuple[int, ...], first: dict[str, int], last: dict[str, int], weight: float
    ) -> None:
        """*weight* to each symbol's callee below its innermost position and its caller
        above its outermost (``inside`` and ``drivers``, see the module docstring)."""
        n = len(frames)
        # The maintained repo frame next below and next above each position (n, -1: none).
        below, above = [n] * n, [-1] * n
        at = n
        for k in range(n - 1, -1, -1):
            below[k] = at
            if not self._unmaintained[frames[k]]:
                at = k
        at = -1
        for k in range(n):
            above[k] = at
            if not self._unmaintained[frames[k]]:
                at = k
        for sid, k in last.items():
            module = self._symbols[sid][1] is None
            if module:
                key, kind = self._imported(frames, first[sid])
            else:
                key, kind = self._callee(frames, k, below[k])
            self._inside.setdefault(sid, Counter())[key] += weight
            self._inside_kinds.setdefault((sid, key), Counter())[kind] += weight
            # A module's imports are named by package; the frame a library's
            # import ended in says nothing about what to do.
            if kind != "own" and not module:
                leaf = self._leaf_key(frames[-1])
                self._leaves.setdefault((sid, key), Counter())[leaf] += weight
        for sid, k in first.items():
            prev = above[k]
            if prev >= 0:
                key, _ = self._repo_key(frames[prev], callee=False)
            elif k > 0:
                key = self._frame_key(frames[k - 1])
            else:
                continue
            self._drivers.setdefault(sid, Counter())[key] += weight

    def _callee(self, frames: tuple[int, ...], k: int, nxt: int) -> tuple[tuple[Any, ...], str]:
        """The callee below position *k* and its kind; *nxt* is the next repo frame below."""
        if k == len(frames) - 1:
            key = ("own",)
            self._named.setdefault(key, {"name": "(own code)"})
            return key, "own"
        if nxt == k + 1:
            return self._repo_key(frames[nxt], callee=True)
        # The unmaintained frames down to the next repo frame, or to the leaf.
        segment = frames[k + 1 : nxt]
        return self._frame_key(segment[0]), self._unmaintained_kind(segment)

    def _imported(self, frames: tuple[int, ...], k: int) -> tuple[tuple[Any, ...], str]:
        """What the import whose row starts at position *k* spent this stack on:
        the first thing below it that is not import machinery or a repo module body
        (a library by top-level package, a repo function, a wait or a built-in
        called from top-level code); else the modules' own code when the stack ends
        in one, or the import machinery it ends in."""
        machinery: int | None = None
        for i in frames[k + 1 :]:
            if not self._unmaintained[i]:
                hit = self._resolved.get(i)
                if hit is None or hit.symbol is not None:
                    return self._repo_key(i, callee=True)
                machinery = None  # a repo module body of the same import
            elif self._wait[i]:
                return self._frame_key(i), "wait"
            elif self._import[i]:
                machinery = i if machinery is None else machinery
            elif self._native[i]:
                if machinery is None:
                    return self._frame_key(i), "native"
            else:
                name = _written_name(package_name(self._frames[i].path))
                key: tuple[Any, ...] = ("package", name)
                self._named.setdefault(key, {"name": name})
                return key, "library"
        if machinery is None:
            key = ("own",)
            self._named.setdefault(key, {"name": "(own code)"})
            return key, "own"
        return self._frame_key(machinery), "import"

    def _unmaintained_kind(self, segment: Sequence[int]) -> str:
        """A run of frames nobody here maintains: a wait or an import anywhere in it,
        else what its first frame is: ``native`` only for the runtime or a built-in, so a
        pathless name the split cannot place (:func:`is_library_frame`) still reads as
        ``library``."""
        if any(self._wait[i] for i in segment):
            return "wait"
        if any(self._import[i] for i in segment):
            return "import"
        first = segment[0]
        return "native" if self._native[first] and not self._library[first] else "library"

    def _leaf_key(self, index: int) -> tuple[Any, ...]:
        """The frame a stack ended in, keyed like a callee, with its kind kept once."""
        if not self._unmaintained[index]:
            key, kind = self._repo_key(index, callee=True)
        else:
            key, kind = self._frame_key(index), self._unmaintained_kind((index,))
        self._leaf_kind.setdefault(key, kind)
        return key

    def _frame_key(self, index: int) -> tuple[Any, ...]:
        """A frame the index holds no symbol for, by its name and file."""
        frame = self._frames[index]
        key = ("frame", frame.function, frame.path)
        if key not in self._named:
            self._named[key] = {"name": _written_name(frame.function or frame.path or "(unknown)")}
        return key

    def _repo_key(self, index: int, *, callee: bool) -> tuple[tuple[Any, ...], str]:
        """A maintained repo frame: its symbol, its file's module body (an import when
        it is a callee) or, unresolved, its name; with the kind it has as a callee."""
        hit = self._resolved.get(index)
        if hit is None:
            return self._frame_key(index), "repo"
        if hit.symbol is None:
            key: tuple[Any, ...] = ("module", hit.path)
            self._named.setdefault(key, {"name": "<module>", "path": hit.path})
            return key, "import" if callee else "repo"
        key = ("symbol", hit.symbol.symbol_id)
        self._named.setdefault(
            key,
            {"name": hit.symbol.qualified_name, "path": hit.path, "line": hit.symbol.start_line},
        )
        return key, "repo"

    def _top(self, weights: Counter[tuple[Any, ...]] | None, sid: str) -> list[tuple[Any, float]]:
        """The :data:`TOP_SPLIT` heaviest keys of *weights*, each with its share of *sid*'s
        inclusive weight."""
        total = self._inclusive[sid]
        if not weights or total <= 0:
            return []
        ranked = sorted(weights.items(), key=lambda kv: (-kv[1], repr(kv[0])))[:TOP_SPLIT]
        return [(key, round(min(w / total, 1.0), 4)) for key, w in ranked]

    def inside(self, sid: str) -> list[dict[str, Any]]:
        """*sid*'s ``inside``: ``name``, ``kind`` and ``share`` per callee, and the
        callee's heaviest ``leaf`` (``name``, ``kind``, ``share``) when the stacks
        under it end somewhere else than the callee itself."""
        out = []
        for key, share in self._top(self._inside.get(sid), sid):
            item: dict[str, Any] = {
                "name": self._named[key]["name"],
                "kind": min(self._inside_kinds[(sid, key)].items(), key=lambda kv: (-kv[1], kv[0]))[
                    0
                ],
                "share": share,
            }
            leaves = self._leaves.get((sid, key))
            if leaves:
                (leaf, weight), *_ = sorted(leaves.items(), key=lambda kv: (-kv[1], repr(kv[0])))
                if leaf != key:
                    item["leaf"] = {
                        "name": self._named[leaf]["name"],
                        "kind": self._leaf_kind[leaf],
                        "share": round(min(weight / self._inclusive[sid], 1.0), 4),
                    }
            out.append(item)
        return out

    def drivers(self, sid: str) -> list[dict[str, Any]]:
        """*sid*'s ``drivers``: ``function`` (with ``path`` and ``line`` in the repo) and ``share``."""
        out = []
        for key, share in self._top(self._drivers.get(sid), sid):
            named = self._named[key]
            driver: dict[str, Any] = {"function": named["name"], "share": share}
            for name in ("path", "line"):
                if named.get(name) is not None:
                    driver[name] = named[name]
            out.append(driver)
        return out

    def _charge(
        self, frames: tuple[int, ...], rows: list[str | None], weight: float, samples: int
    ) -> None:
        """Self time to the innermost maintained repo frame, and the stack's bucket."""
        # The innermost frame somebody here maintains, and what runs below it.
        at = next(
            (k for k in range(len(frames) - 1, -1, -1) if not self._unmaintained[frames[k]]), None
        )
        tail = frames if at is None else frames[at + 1 :]
        # Only under a repo frame: a worker's own idle stacks (parked in its
        # pool's queue, no repo frame) are not this process waiting on another.
        if at is not None and any(self._child[i] for i in tail):
            self.child_processes += weight
        if any(self._wait[i] for i in tail):
            self.buckets["waits"] += weight
            return
        in_library = any(self._library[i] for i in tail)
        if in_library:
            self.buckets["libraries"] += weight
        if at is None:
            return
        innermost = frames[at]
        hit = self._resolved.get(innermost)
        sid = rows[at]
        if sid is not None:
            self._own[sid] += weight
            self._own_n[sid] += samples
        if hit is not None and hit.symbol is not None:
            if not in_library and self._production(hit.path):
                self.buckets["repo_self"] += weight
        elif sid is None:
            # A Go package initializer or a JavaScript module wrapper has no
            # row (a Python module body does: ``<path>::<module>``), so its own
            # time is counted here, never dropped. A module body stays out of
            # ``repo_self`` either way: the split counts it with the rest.
            why = "module_body" if hit is not None else str(self._reasons[innermost])
            self.self_unattributed[why] += weight

    def entries(self, *, counted: bool) -> list[dict[str, Any]]:
        """One v1 entry per symbol, costliest first: self, then inclusive."""
        order = sorted(
            self._symbols, key=lambda sid: (-self._own[sid], -self._inclusive[sid], sid)
        )
        return [
            {
                "path": self._symbols[sid][0],
                **(
                    {"function": symbol.qualified_name, "line": symbol.start_line}
                    if (symbol := self._symbols[sid][1]) is not None
                    else {"function": MODULE_FUNCTION}
                ),
                "self": self._own[sid],
                "total": self._inclusive[sid],
                **(
                    {"self_samples": self._own_n[sid], "total_samples": self._inclusive_n[sid]}
                    if counted
                    else {}
                ),
                **({"inside": inside} if (inside := self.inside(sid)) else {}),
                **({"drivers": drivers} if (drivers := self.drivers(sid)) else {}),
                **({"calls": self._calls[sid]} if sid in self._calls else {}),
            }
            for sid in order
        ]


def refuse_compiled_positions(
    run: ProfileRun,
    root: str,
    read_lines: Callable[[str], Sequence[str] | None] | None = None,
    origins: Mapping[str, str] | None = None,
) -> None:
    """Refuse a run whose checkout frames carry positions in compiled code.

    A TypeScript frame's line and column are in the compiled JavaScript, and
    a JavaScript file that names a source map (``//# sourceMappingURL=``) is
    build output: a bundle or a compiler's, whose positions belong to other
    files. Source maps are not read, so either refuses the run. A dependency
    (``node_modules``, a vendored or generated file by its stored
    ``code_origin`` in *origins*, else :func:`path_origin`) is charged to its
    caller and never checked. *read_lines* reads a repo-relative file;
    without it only TypeScript is checked.
    """
    typescript = [f for f in run.frames if f.language == "typescript"]
    if typescript and frame_paths(typescript, root=root):
        raise EvidenceFormatError(
            "the profile names TypeScript files of this checkout, and their lines and "
            "columns are positions in the compiled JavaScript",
            "profile the compiled JavaScript under its own paths; TypeScript positions "
            "need source maps, which are not read",
        )
    if read_lines is None:
        return
    javascript = [f for f in run.frames if f.language == "javascript"]
    for rel in sorted(frame_paths(javascript, root=root)):
        if ((origins or {}).get(rel) or path_origin(rel)) in ("vendored", "generated"):
            continue
        lines = read_lines(rel)
        if lines is not None and _names_source_map(lines):
            raise EvidenceFormatError(
                f"the profile ran {rel}, which names a source map: it is bundled or "
                "compiled code, and its positions belong to other files",
                "profile the JavaScript sources themselves; positions in bundled or "
                "compiled code need source maps, which are not read",
            )


#: A source map comment, which a compiler or bundler writes last in its output.
_SOURCE_MAP_RE = re.compile(r"^\s*//[#@]\s*sourceMappingURL=")


def _names_source_map(lines: Sequence[str]) -> bool:
    """Whether one of the last five non-blank lines is a source map comment."""
    tail = [ln for ln in reversed(lines) if ln.strip()][:5]
    return any(_SOURCE_MAP_RE.match(ln) for ln in tail)


__all__ = [
    "ProfileConversion",
    "convert_profile",
    "is_child_process_frame",
    "is_import_frame",
    "is_library_frame",
    "is_native_frame",
    "merge_runs",
    "package_name",
    "refuse_compiled_positions",
]
