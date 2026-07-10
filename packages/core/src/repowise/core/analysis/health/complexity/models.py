"""Walker output schema — the dataclasses ``walk_file`` produces.

Pure data, no logic. Each type is consumed by a specific biomarker or by
the health engine; see the per-class docstrings for the downstream reader.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConditionComplexity:
    """One control-flow condition with its boolean-operator count.

    Emitted by the walker as a side-channel to ``FunctionComplexity``;
    does not affect CCN or cognitive complexity. Consumed by the
    ``complex_conditional`` biomarker.
    """

    line: int  # 1-indexed start line of the enclosing construct
    operator_count: int
    enclosing_construct: str  # "if" | "while" | "for" | "ternary" | "case"


@dataclass
class FunctionComplexity:
    """Per-function metrics produced by the walker."""

    name: str
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    ccn: int
    max_nesting: int
    cognitive: int
    nloc: int  # non-blank lines inside the body
    # Number of top-level body sub-blocks whose internal nesting reached
    # ≥ 2 — used by ``bumpy_road``. A flat function has 0 bumps.
    bumps: int = 0
    # Number of declared parameters on the function signature — used by
    # ``primitive_obsession``. Counted via the tree-sitter ``parameters``
    # field; 0 when the language lacks an explicit list or extraction fails.
    param_count: int = 0
    # Per-condition boolean-operator counts collected during the walk.
    # Empty when no branch/loop carries compound boolean expressions.
    complex_conditions: list[ConditionComplexity] = None  # type: ignore[assignment]
    # Runs of ≥2 consecutive assertion statements within the body, each
    # ``(start_line, end_line, count)``. Populated only for languages whose
    # ``LanguageNodeMap`` opts into assertion detection (``assert_kinds`` /
    # ``assert_call_kinds``). Consumed by the test-quality biomarkers.
    assertion_blocks: list[tuple[int, int, int]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.complex_conditions is None:
            self.complex_conditions = []
        if self.assertion_blocks is None:
            self.assertion_blocks = []


@dataclass
class CohesionGroup:
    """One LCOM4 connected component within a class.

    A cluster of methods that share instance state (a field or a
    method-call edge), plus the instance fields the cluster collectively
    touches. Emitted as a side-channel by ``_compute_lcom4`` and consumed
    by the Extract Class refactoring detector — when a class has
    ``lcom4 >= 2`` each group is a candidate extracted class. ``methods``
    and ``fields`` are stable-sorted (by first appearance / name) so the
    same class yields the same split across runs.
    """

    methods: list[str]
    fields: list[str]


@dataclass
class ClassComplexity:
    """Per-class aggregate metrics produced by the walker.

    Emitted only for languages whose ``LanguageNodeMap`` opts into
    class-level analysis (``class_kinds`` non-empty). Consumed by the
    ``low_cohesion`` (LCOM4) and ``god_class`` biomarkers.

    ``lcom4`` is the LCOM4 cohesion metric — the number of connected
    components in the graph whose nodes are the class's methods and whose
    edges link methods that share an instance field or call one another.
    ``1`` means a fully cohesive class (or "no signal": see the safety
    valve in ``_compute_lcom4``). Higher values mean the class splinters
    into unrelated method clusters.

    ``components`` carries those connected components as ``CohesionGroup``
    records (the method+field membership behind the ``lcom4`` integer).
    Empty when there is no cohesion signal (``lcom4`` was the ``1`` safety
    valve); otherwise ``len(components) == lcom4``. The Extract Class
    refactoring detector reads it directly — it *is* the split.
    """

    name: str
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    method_count: int
    total_nloc: int
    methods: list[FunctionComplexity]
    lcom4: int = 1
    max_method_ccn: int = 0
    field_count: int = 0
    components: list[CohesionGroup] = field(default_factory=list)
    # Tight Class Cohesion (Bieman-Kang): the fraction of method pairs that
    # share at least one instance field, in ``[0, 1]`` — higher is more
    # cohesive. ``1.0`` is the "no signal" default (fewer than two methods, or
    # a language whose member access we do not map), mirroring the ``lcom4``
    # safety valve. A cohesive Extract Class split raises the worst split
    # class's TCC toward ``1``; the enrich self-check reads it before/after.
    tcc: float = 1.0


@dataclass(frozen=True)
class ErrorHandlingHit:
    """One error-handling anti-pattern occurrence in a file.

    Collected by the walker's whole-tree pass (see
    ``_collect_error_handling``) and consumed by the ``error_handling``
    biomarker. ``kind`` is one of:

    - ``swallowed_catch`` — a catch/except whose body has no real handling
      (empty, or only ``pass`` / ``...`` / a docstring / comments).
    - ``bare_except`` — Python truly-catch-all ``except:`` / ``except
      BaseException:`` (also swallows KeyboardInterrupt / SystemExit),
      regardless of body.
    - ``broad_except`` — Python ``except Exception:`` (broad, but cannot catch
      the BaseException-only interrupts), regardless of body.
    - ``unsafe_unwrap`` — Rust ``.unwrap()`` / ``.expect()`` /
      ``.unwrap_unchecked()`` calls (latent panic-on-error). Suppressed inside
      ``#[test]`` / ``#[cfg(test)]`` items.
    - ``panic_macro`` — Rust ``panic!`` / ``unreachable!`` / ``todo!`` /
      ``unimplemented!`` macros (unconditional abort). Suppressed inside tests.
    - ``go_swallow`` — Go empty ``if err != nil {}`` block, or a trailing
      blank-identifier discard of a multi-return call's value.
    """

    kind: str
    line: int  # 1-indexed


@dataclass(frozen=True)
class PerfHit:
    """One performance-risk occurrence in a file (the ``performance`` dimension).

    Collected by the walker's whole-tree perf pass (see
    ``_collect_perf_hits``) and lifted into findings by the perf biomarkers.
    Precision-first: every hit is loop-body-scoped and execution-sink-gated so
    an unsupported language / parse failure / builder-only call yields nothing.

    ``kind`` is one of:

    - ``io_in_loop`` — an execution sink at an I/O boundary (db / network /
      filesystem / subprocess) inside a real, data-dependent loop body. The
      boundary kind is carried in ``detail``.
    - ``string_concat_in_loop`` — string accumulation (``+=`` onto a string)
      inside a loop, instead of a buffer / ``join``.
    - ``blocking_sync_in_async`` — a known blocking sync call (``time.sleep`` /
      sync ``requests`` / ``subprocess`` / ``os.system`` / bare ``open``) inside
      an ``async def``, not awaited. ``detail`` carries the offending API.
    - ``resource_construction_in_loop`` — a heavy I/O client / connection
      (``sqlite3.connect`` / ``httpx.Client`` / ``boto3.client`` / ``new
      PrismaClient``) constructed each iteration instead of hoisted.
    - ``lock_in_loop`` — a lock acquired each iteration (``lock.acquire`` /
      ``mu.Lock`` / ``synchronized`` / ``lock(x){}``); a contention signal that
      activates the ``lock`` I/O boundary kind.
    - ``serial_await_in_loop`` — an awaited I/O sink in a loop body (the
      missed-``gather`` / ``Promise.all`` shape). ``detail`` carries the
      boundary kind. Rides alongside ``io_in_loop`` as an advisory co-signal.
    - ``membership_test_against_list_in_loop`` — ``x in big_list`` (or
      ``big_list.includes(x)``) inside a loop where the right operand is a known
      list -> O(n·m); a set would make each probe O(1).

    Phase-7b markers (``func_start``-carrying so the centrality gate can resolve
    the enclosing function to a graph symbol node):

    - ``nested_loop_with_io`` — an I/O sink in the inner body of a nested loop
      (``loop_depth >= 2``) -> O(n·m) round-trips. Rides alongside ``io_in_loop``;
      the nesting itself raises confidence, so it is NOT centrality-gated.
    - ``nested_loop_quadratic`` — a data-dependent loop nested inside another
      (O(n^2) shape). Emitted as a *candidate*; the centrality gate keeps it
      ONLY in a hot/central function (the gate is the precision fix).
    - ``hot_path_sync_io`` — a blocking (non-awaited) I/O sink at ``loop_depth 0``.
      Emitted as a *candidate*; survives only in a top-centrality function.
    - ``blocking_io_under_lock`` — an I/O sink reached while a block-scoped lock
      (C# ``lock``/Java ``synchronized``) is held. Same-function (sink lexically
      under the lock) is emitted here; the cross-function case is added by
      ``perf.gated.collect_blocking_io_under_lock``.

    The Phase-7a markers above are emitted via the dialect ``loop_call_marker`` /
    ``loop_stmt_marker`` hooks (and the inline awaited-sink path for
    ``serial_await_in_loop``), so each is per-language opt-in.
    """

    kind: str
    line: int  # 1-indexed
    function: str | None = None
    detail: str = ""
    # 1-indexed ``def`` line of the enclosing function (0 = module scope). Lets
    # the centrality gate (Phase 7b) resolve a candidate hit to its graph symbol
    # node without a separate lookup. 0 for hits where it is irrelevant.
    func_start: int = 0
    # Reachability path for a cross-function hit: the resolved symbol-node
    # chain ``A -> B -> ... -> sink`` (PR4). Empty for same-function hits —
    # its emptiness is what distinguishes the two cases downstream.
    path: tuple[str, ...] = ()
    # Set by the dataflow promotion pass (``perf.promotion``) when intra-
    # procedural reaching definitions PROVE the enclosing loop carries no data
    # dependence between iterations, turning an advisory "if the iterations are
    # independent" hedge into an asserted finding. Only ever flipped for the
    # advisory markers the promotion pass targets; left ``False`` everywhere the
    # proof is unavailable (no dialect, guard trip, non-convergence) or the loop
    # genuinely carries a dependence. The biomarker sharpens its message when set.
    promoted: bool = False


@dataclass(frozen=True)
class PerfFnFacts:
    """Per-function facts the cross-function perf bridge needs (PR4).

    Collected on the same whole-tree perf pass as ``PerfHit``. Two signals:

    - ``loop_call_targets`` — the rightmost names of calls made inside a loop
      body that are NOT themselves a direct I/O sink (those are already a
      same-function ``io_in_loop`` hit). Each is a candidate helper whose body
      might reach a sink transitively — the entry points of the reachability
      walk.
    - ``bare_sink_kind`` — the boundary kind of an I/O sink this function
      executes at ``loop_depth 0`` (its own top level, not nested in a loop),
      else ``None``. A function with a bare sink is a *reachability target*:
      when a loop in another function calls into it, that sink runs once per
      iteration. The ``loop_depth 0`` requirement is deliberate — a sink the
      function runs inside its own loop is already counted there.

    ``func_start`` is the 1-indexed line of the enclosing function's ``def``
    (0 for module scope, which maps to the synthetic ``::__module__`` node);
    the bridge resolves it to a graph symbol node by line containment.

    ``lock_call_targets`` is the Phase-7b analogue of ``loop_call_targets``: the
    rightmost names of non-sink calls made while a block-scoped lock is held.
    Each is a candidate helper whose body might reach a sink transitively —
    the cross-function ``blocking_io_under_lock`` entry points.
    """

    function: str | None
    func_start: int
    # ``(callee_name, call_line)`` pairs for the loop-nested non-sink calls.
    loop_call_targets: tuple[tuple[str, int], ...]
    bare_sink_kind: str | None
    # ``(callee_name, call_line)`` pairs for non-sink calls under a held lock.
    lock_call_targets: tuple[tuple[str, int], ...] = ()
    # Phase-7b centrality-gated facts the engine turns into hits only for a hot
    # function. ``nested_loop_line`` is a representative data-dependent
    # nested-loop site (0 = none -> ``nested_loop_quadratic``);
    # ``blocking_sink_{kind,line}`` is the first non-awaited loop_depth-0 I/O
    # sink (None/0 = none -> ``hot_path_sync_io``).
    nested_loop_line: int = 0
    blocking_sink_kind: str | None = None
    blocking_sink_line: int = 0


@dataclass
class FileComplexity:
    """Walker output for one file: per-function and per-class metrics.

    ``walk_file`` returns this; ``walk_file_complexity`` is the
    backward-compatible thin wrapper that returns only ``functions``.
    """

    functions: list[FunctionComplexity]
    classes: list[ClassComplexity]
    file_nloc: int = 0
    # Error-handling anti-pattern occurrences (whole-tree pass). Empty when
    # the language is unsupported or parsing failed — "no signal", never a
    # false positive.
    error_handling_hits: list[ErrorHandlingHit] = field(default_factory=list)
    # Performance-risk occurrences (whole-tree perf pass). Empty when the
    # language opts out of the perf pass (no ``call_kinds``) or parsing failed.
    perf_hits: list[PerfHit] = field(default_factory=list)
    # Names imported from an I/O-typed library in this file, mapped to their
    # boundary kind (db / network / filesystem / subprocess / lock). The
    # per-file import bridge; PR4's cross-function reachability consumes it.
    io_boundary_names: dict[str, str] = field(default_factory=dict)
    # Per-function facts feeding PR4's cross-function N+1 detection: which
    # callees each function invokes inside a loop body, and whether the
    # function holds a bare (non-loop) I/O sink. Empty when the language opts
    # out of the perf pass. Consumed by ``perf.crossfn``, not by a biomarker.
    perf_fn_facts: list[PerfFnFacts] = field(default_factory=list)
    # True when the file carries co-located tests that the filename/dir
    # heuristic cannot see — e.g. Rust ``#[cfg(test)] mod tests`` blocks,
    # which live inside the source file itself. OR'd into ``has_test_file``
    # so well-tested inline-test files aren't flagged as untested. Only ever
    # flips a file from "untested" to "tested", so it can silence a finding
    # but never invent one.
    has_inline_tests: bool = False
