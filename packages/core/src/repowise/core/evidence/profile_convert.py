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
- ``libraries``: not waiting, and a dependency frame (vendored, generated, or
  outside the checkout under a vendored path such as ``site-packages``) runs
  below its innermost repo frame, or anywhere on a stack with no repo frame;
- ``repo_self``: neither, and the innermost repo frame is a production
  function (``code_origin``): time in production repo code itself, built-ins
  and the standard library it called included;
- the rest: test or tooling code, module bodies, unresolved frames, and
  stacks with no repo frame that run only the runtime and standard library.

``explained`` (weight on stacks with a production frame anywhere) is still
written for older readers; it says nothing about cost, since a CLI's entry
function is on every stack.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from repowise.core.code_origin import is_vendored_or_generated_path, path_origin
from repowise.core.frame_resolver import frame_paths, resolve_frames

from .format_v1 import EVIDENCE_FORMAT, EvidenceFormatError

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
    #: The run's whole weight, empty stacks included.
    total: float
    #: Weight on stacks with a resolved production frame.
    explained: float
    #: Frame-occurrence weight by outcome: ``resolved`` or the skip reason.
    frame_time: dict[str, float] = field(default_factory=dict)
    #: Self weight charged to no symbol: ``module_body`` or the innermost repo frame's skip reason.
    self_unattributed: dict[str, float] = field(default_factory=dict)
    #: Weight in production repo code itself, waits and libraries excluded.
    repo_self: float = 0.0
    #: Weight blocked in a wait frame.
    waits: float = 0.0
    #: Weight in dependency code below the innermost repo frame.
    libraries: float = 0.0
    #: Weight starting or waiting on another process below the innermost repo
    #: frame (:func:`is_child_process_frame`): that process's own work is not in
    #: this profile. Overlaps ``waits``; not part of the four-way split.
    child_processes: float = 0.0

    @property
    def explained_share(self) -> float | None:
        return self.explained / self.total if self.total > 0 else None

    def share(self, value: float) -> float | None:
        """*value* over the run's weight, rounded; None for an empty run."""
        return round(value / self.total, 4) if self.total > 0 else None

    @property
    def repo_frame_time(self) -> float:
        """Frame-occurrence weight under the checkout root, resolved or not."""
        return sum(v for k, v in self.frame_time.items() if k != "outside_root")

    @property
    def skip_rate(self) -> float | None:
        """Share of repo frame time that did not resolve; None with no repo frames."""
        repo = self.repo_frame_time
        return (repo - self.frame_time.get("resolved", 0.0)) / repo if repo > 0 else None

    def summary(self) -> dict[str, Any]:
        repo = self.repo_frame_time
        skipped = {
            k: round(v / repo, 4)
            for k, v in sorted(self.frame_time.items(), key=lambda kv: -kv[1])
            if k not in ("resolved", "outside_root") and repo > 0
        }
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
            "self_unattributed": {
                k: round(v / self.total, 4) for k, v in sorted(self.self_unattributed.items())
            }
            if self.total > 0
            else {},
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
) -> ProfileConversion:
    """*run*'s frames joined to the symbols at *root*, as a v1 profile document.

    The arguments after *run* are :func:`resolve_frames`'; *value_kind*
    (``cpu`` or ``wall``) overrides what the format says.
    """
    kind = value_kind or run.value_kind
    if kind is None:
        raise EvidenceFormatError(
            "the profile does not say whether it timed CPU or wall-clock",
            "name the clock the profiler sampled: cpu or wall",
        )
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
        "total": tally.total,
        "explained": tally.explained,
        "repo_self": tally.buckets["repo_self"],
        "waits": tally.buckets["waits"],
        "libraries": tally.buckets["libraries"],
        "skipped": dict(sorted(skipped.items())),
        "symbols": entries,
    }
    # The run's sample count only when every stack counted its samples: a
    # partial count would put the wrong ``n`` under every share's interval.
    if run.stacks and all(s.samples is not None for s in run.stacks):
        document["total_samples"] = tally.samples
    if run.tool_name:
        document["tool"] = {"name": run.tool_name, "version": run.tool_version}
    return ProfileConversion(
        document,
        run.unit,
        tally.total,
        tally.explained,
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
        # Per frame, once: nobody here maintains it, it is a dependency, it blocks.
        self._unmaintained = [r in _UNMAINTAINED for r in self._reasons]
        self._dependency = [
            r in ("vendored", "generated")
            or (r == "outside_root" and is_vendored_or_generated_path(f.path.replace("\\", "/")))
            for f, r in zip(frames, self._reasons, strict=True)
        ]
        self._wait = [is_wait_frame(f) for f in frames]
        self._child = [is_child_process_frame(f) for f in frames]
        self._resolved = {r.index: r for r in resolution.resolved}
        self._origins = origins
        self._symbols: dict[str, tuple[str, IndexedSymbol]] = {}
        self._inclusive: Counter[str] = Counter()
        self._own: Counter[str] = Counter()
        self._inclusive_n: Counter[str] = Counter()
        self._own_n: Counter[str] = Counter()
        self.frame_time: Counter[str] = Counter()
        #: Self weight whose innermost repo frame has no symbol row, by why.
        self.self_unattributed: Counter[str] = Counter()
        #: The run's weight in ``repo_self``, ``waits`` and ``libraries``.
        self.buckets: Counter[str] = Counter({"repo_self": 0.0, "waits": 0.0, "libraries": 0.0})
        self.child_processes = 0.0
        self.total = 0.0
        self.explained = 0.0
        self.samples = 0

    def _production(self, path: str) -> bool:
        return (self._origins.get(path) or path_origin(path)) == "production"

    def add(self, stack: Stack) -> None:
        weight, samples = stack.weight, stack.samples or 0
        self.total += weight
        self.samples += samples
        on_stack: set[str] = set()
        in_production = False
        for i in stack.frames:
            hit = self._resolved.get(i)
            self.frame_time["resolved" if hit is not None else str(self._reasons[i])] += weight
            if hit is None:
                continue
            in_production = in_production or self._production(hit.path)
            if hit.symbol is not None and hit.symbol.symbol_id not in on_stack:
                sid = hit.symbol.symbol_id
                on_stack.add(sid)
                self._symbols[sid] = (hit.path, hit.symbol)
                self._inclusive[sid] += weight
                self._inclusive_n[sid] += samples
        if in_production:
            self.explained += weight
        self._charge(stack.frames, weight, samples)

    def _charge(self, frames: tuple[int, ...], weight: float, samples: int) -> None:
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
        in_library = any(self._dependency[i] for i in tail)
        if in_library:
            self.buckets["libraries"] += weight
        if at is None:
            return
        innermost = frames[at]
        hit = self._resolved.get(innermost)
        if hit is not None and hit.symbol is not None:
            self._own[hit.symbol.symbol_id] += weight
            self._own_n[hit.symbol.symbol_id] += samples
            if not in_library and self._production(hit.path):
                self.buckets["repo_self"] += weight
        else:
            # The index keeps no symbol for a file's top level, so a module
            # body's own time has no row; it is counted here, never dropped.
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
                "function": self._symbols[sid][1].qualified_name,
                "line": self._symbols[sid][1].start_line,
                "self": self._own[sid],
                "total": self._inclusive[sid],
                **(
                    {"self_samples": self._own_n[sid], "total_samples": self._inclusive_n[sid]}
                    if counted
                    else {}
                ),
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
    "merge_runs",
    "refuse_compiled_positions",
]
