"""A profile run joined to the indexed code, written as a ``repowise-evidence-v1`` document.

Every format in :mod:`.profile_formats` comes through here, so frames are
joined one way only: :func:`repowise.core.frame_resolver.resolve_frames`
(the checkout root the caller names, a second signal, folding, a reason per
skipped frame; never a path suffix). The document then goes through the
same ingest as a hand-written one.

Per symbol the run gets both views:

- inclusive (``total``): every stack the symbol is on, once per stack even
  when it recurses;
- self: stacks whose innermost repo frame is the symbol. Frames outside the
  checkout below it (built-ins, the standard library, dependencies) are
  charged to it, so time spent in I/O or a C extension stays with the code
  that called it. A stack whose innermost repo frame was skipped charges no
  self time at all.

The run is *explained* by the share of its weight on stacks with a resolved
frame in production code (``code_origin``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from repowise.core.code_origin import path_origin
from repowise.core.frame_resolver import frame_paths, resolve_frames

from .format_v1 import EVIDENCE_FORMAT, EvidenceFormatError

if TYPE_CHECKING:
    from repowise.core.frame_resolver import FrameResolution
    from repowise.core.workspace.repo_index import IndexedSymbol

    from .profile_formats import ProfileRun, Stack

#: The v1 reason for a frame outside the checkout, which the store counts apart.
_OUTSIDE = "outside_repo"


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

    @property
    def explained_share(self) -> float | None:
        return self.explained / self.total if self.total > 0 else None

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
    refuse_typescript(run, root)
    resolution = resolve_frames(
        run.frames,
        root=root,
        symbols_by_file=symbols_by_file,
        dirty_paths=dirty_paths,
        origins=origins,
        read_lines=read_lines,
    )
    tally = _Tally(resolution, origins or {})
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
        "skipped": dict(sorted(skipped.items())),
        "symbols": entries,
    }
    if run.tool_name:
        document["tool"] = {"name": run.tool_name, "version": run.tool_version}
    return ProfileConversion(
        document,
        run.unit,
        tally.total,
        tally.explained,
        dict(tally.frame_time),
        dict(tally.self_unattributed),
    )


class _Tally:
    """Per-symbol inclusive and self weight, explained weight and frame time over the stacks."""

    def __init__(self, resolution: FrameResolution, origins: Mapping[str, str]) -> None:
        self._reasons = resolution.reasons
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
        self.total = 0.0
        self.explained = 0.0

    def _production(self, path: str) -> bool:
        return (self._origins.get(path) or path_origin(path)) == "production"

    def add(self, stack: Stack) -> None:
        weight, samples = stack.weight, stack.samples or 0
        self.total += weight
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
        innermost = next(
            (i for i in reversed(stack.frames) if self._reasons[i] != "outside_root"), None
        )
        if innermost is None:
            return
        hit = self._resolved.get(innermost)
        if hit is not None and hit.symbol is not None:
            self._own[hit.symbol.symbol_id] += weight
            self._own_n[hit.symbol.symbol_id] += samples
        else:
            # The index keeps no symbol for a file's top level, so a module
            # body's own time has no row; it is counted here, never dropped.
            why = "module_body" if hit is not None else str(self._reasons[innermost])
            self.self_unattributed[why] += weight

    def entries(self, *, counted: bool) -> list[dict[str, Any]]:
        """One v1 entry per symbol, costliest inclusive first."""
        order = sorted(self._symbols, key=lambda sid: (-self._inclusive[sid], sid))
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


def refuse_typescript(run: ProfileRun, root: str) -> None:
    """A TypeScript frame of this checkout carries compiled positions, so the run is refused."""
    typescript = [f for f in run.frames if f.language == "typescript"]
    if typescript and frame_paths(typescript, root=root):
        raise EvidenceFormatError(
            "the profile names TypeScript files of this checkout, and their lines and "
            "columns are positions in the compiled JavaScript",
            "profile the compiled JavaScript under its own paths; TypeScript positions "
            "need source maps, which are not read",
        )


__all__ = ["ProfileConversion", "convert_profile", "refuse_typescript"]
