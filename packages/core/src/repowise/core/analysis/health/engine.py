"""HealthAnalyzer — thin orchestrator over walker → biomarkers → scorer.

Run sequence per file:

  1. Open the source bytes from ``ParsedFile.file_info.abs_path``.
  2. Walk the AST with ``complexity.walk_file_complexity`` → list of
     ``FunctionComplexity``.
  3. Build a ``FileContext`` (function metrics, git meta, dependents
     count, NLOC, test-file flag).
  4. Run all registered biomarkers via ``biomarkers.detect_all``.
  5. Score the file, attach per-finding impacts.
  6. Side effect: write ``max(ccn)`` into each Symbol's
     ``complexity_estimate`` so downstream consumers benefit.

Repo-level KPIs are computed from the final per-file metrics.
"""

from __future__ import annotations

import asyncio
import functools
import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import structlog

from ...code_origin import CodeOrigin, code_origin
from ...git_refs import remote_name
from ...ingestion.git_indexer.enrich import count_active_contributors
from ...ingestion.git_indexer.function_blame import (
    BlameIndex,
    distinct_commits_in_range,
)

# Package attribution lives in one place, shared with the traverser and with
# the `repowise update` backfill, so all three agree on what a package is.
from ...ingestion.package_roots import module_for as _module_for  # noqa: F401 (re-export)
from ...ingestion.package_roots import package_roots_from_paths as _package_roots
from ...ingestion.package_roots import scan_package_roots as _scan_package_roots
from ...installed_components import is_installed_component
from ..dead_code.file_reachability import file_dependency_neighbors
from ..execution_roles import ExecutionRoles
from ..graph_view import ImportEdgeView
from .asserts.lexicon import AssertVocabulary
from .asserts.oracle_reach import collect_cross_file_oracles
from .complexity import FileComplexity, FunctionComplexity, walk_file
from .coverage_actions import paired_test_index
from .crossfn_perf import CrossFunctionPerfStage
from .dataflow import FileDataflowCache
from .duplication import ClonePair, DuplicationReport
from .duplication.detector import clone_ranges, clones_against_index, union_line_count
from .duplication.isolation import detect_clones_with_isolation as detect_clones
from .file_evaluator import FileEvaluator, _read_source_lines, _RunConfig
from .file_evaluator import _mark_deepest_block as _mark_deepest_block
from .file_evaluator import _mark_function_facts as _mark_function_facts
from .file_evaluator import walked_functions as walked_functions
from .models import HealthFileMetricData, HealthFindingData, HealthReport
from .perf import (
    CallGraphIndex,
    build_performance_opportunities,
    link_performance_findings,
)
from .ranking import deduction_by_path
from .refactoring import (
    PerformancePlanPolicy,
    RefactoringSuggestion,
    performance_fix_suggestions,
    rank_suggestions,
)
from .refactoring.graph_signals import build_file_scc_index, build_methods_by_file
from .repo_tests import RepoTestIndex
from .repo_tests import _has_paired_test_file as _has_paired_test_file
from .repo_tests import _path_basenames as _path_basenames
from .scope import scores_language
from .scoring import compute_kpis
from .source_reader import SourceReader, disk_source_reader
from .walk_cache import HealthWalkCache

if TYPE_CHECKING:
    from ..function_test_map import FunctionTests

log = structlog.get_logger(__name__)

# Bump when a change to this analyzer makes already-persisted health rows wrong
# — a new or removed biomarker, a changed attribution rule, a different finding
# shape. ``repowise update`` compares it against the value stored in state.json
# and re-scores on mismatch (see ``update_cmd.persistence.full_rescore_due``),
# instead of waiting out the 7-day decay timer.
#
# Reach: a single-repo ``repowise update`` checks it before the "already up to
# date" return, so a checkout with no new commits re-scores on its next update
# too, from the graph it re-parses, with no model call. Workspace members and
# the hosted indexer do not run this path at all.
#
# Deliberately *not* folded into ``config_fingerprint``: that fingerprint means
# "this repo's config content changed", and a workspace update answers drift in
# it by full-re-indexing every member repo. An analyzer change invalidates the
# scores, not the parse or the git index, so it routes to the re-score alone.
#
# Not a licence to move a calibrated scoring weight — those are frozen
# independently of this stamp.
#
# v42: a JavaScript-family file under a ``tests/`` folder inside a ``src`` tree
# is production unless its name or a test directory says otherwise, and the
# cross-file assertion pass reads the stored test flag, which moves scope and
# findings for those files.
#
# v41 (also): a loop that grows only by reading tables that do not grow with
# the repository is bounded (``perf.table_scale``), so its causes leave the queue.
# v41 (also): ``docs`` / ``docs_src`` count as a docs root only at the
# repository root or beside a docs-site generator's config, so a product's
# ``app/docs/`` route is production and stored ``code_origin`` is re-read.
# v41: ``brain_method`` is advisory, so every stored score that carried one
# moves. Parameter counts exclude every comment kind and discover C/C++
# declarators and Kotlin lists; cached walks refresh the total, typed and scalar
# counts, enabling primitive_obsession in those languages.
# History findings de-stack before the history cap (``scoring.destacked``).
# Defect code categories follow a saturating curve scaled by their cap, findings on
# one function group, and critical size findings scale past their threshold
# (``shape_curve``).
#
# v40: an Extract Method plan names its helper with a placeholder
# (``extracted_helper``, numbered when taken in scope) and asks the agent applying
# it to rename it, so every stored plan's ``suggested_name``, header and call text
# change. Plan ids do not depend on the name and hold.
#
# v39: a Python module named like a test outside every test
# directory is production code when the nearest pytest config's ``testpaths``
# or ``python_files`` leaves it out (``core/test_selection.py``). Its
# ``is_test`` flag flips, and the biomarkers and ``has_test_file`` now read that
# stored flag instead of re-classifying the path, so such a module joins the
# production scores and findings. A callback passed to a chained or curried
# call (``it.each([...])(...)``, ``z.object({...}).superRefine(...)``) is named
# by the call's dotted path without its arguments, so findings and perf hits
# inside one get a new ``function_name`` and a new public id; triage stored on
# the old id no longer attaches.
#
# v38: Split File's co-change edge reads per-function commit
# sets (the 50 newest distinct commits of each function, stored on
# ``git_function_blame``), and a class takes the union of its methods' sets
# instead of blame over its whole span. Group membership is a kernel input to
# the ``split_file`` id, so ``REFACTORING_MODEL_VERSION`` moved 3 -> 4 with it:
# every refactoring id now carries the ``refac4_`` prefix, and a held ``refac3_``
# id reports ``stale_model``.
#
# v37: schema migrations are ``tooling`` in the performance
# ``execution_context`` (any ``migrations/`` directory, Rails ``db/migrate``,
# Alembic ``alembic/versions``). The context is stored on every performance
# opportunity and is a kernel input to its id, so ``PERFORMANCE_MODEL_VERSION``
# moved 2 -> 3 with it: every performance id now carries the ``perf3_`` prefix,
# and a held ``perf2_`` id reports ``stale_model`` instead of a silent miss.
#
# v37 (also): refactoring plans credit what they remove. Extract Method, Extract
# Class (``god_class``) and Extract Helper claim the share of the finding that
# moves, a span must clear a minimum-worth floor, ``slice_nloc`` counts code
# lines only, and JSX prop plumbing earns no Extract Method. Stored plans and
# opportunities change, and ``REFACTORING_MODEL_VERSION`` moved with this.
# v37 (also): the walker records ``dispatch_share`` and ``deprecated`` per
# function, which a cached v36 walk does not carry, and each file metric gains
# ``code_origin``. Complexity findings copy ``dispatch_share`` into their
# details and findings on a deprecated function gain ``deprecated: true``. No
# CCN, threshold, weight or score moves.
# v37 (also): a finding inside a function or class records that symbol's first
# line (``details["symbol_line"]``) and its public id is anchored on the symbol
# plus the offset into it, hashing no metric values, so every stored finding id
# moves once.
# v37 (also): the walker records each function's deepest nested block and each
# performance hit's loop header line; size findings carry ``deepest_block`` and
# performance findings ``loop_line`` in their details, both outside the id
# kernel. A cached v37 walk without them reads the defaults (absent).
# v37 (also): a perf hit inside a lambda with no named function around it is
# named for that lambda (``build``, ``it callback``), so its stored
# ``function_name`` and public id change; top-level script code still carries
# none. The v3 performance model also keys one opportunity per intervention, so
# every stored opportunity id and the id stamped on every perf finding change.
# ``lazy_load_in_loop`` findings carry ``details["orm"]``, which moves their
# public ids and, for Django, their performance weight (0.4 -> 0.7).
#
# v36 (also): which files are tests changed (``repowise.core.test_paths``).
# Compound directories headed by a test word (``e2e-tests/``, ``pkg_tests/``,
# ``integration_test/``) became test trees, and ``test``/``.test.`` filenames
# stopped counting on configuration (``tsconfig.test.json``, a workflow's
# ``*.test.yml``) and on a bare non-Python ``test.ts``. Every biomarker that
# exempts or targets tests reads that flag, so stored findings move.
#
# v35 (also): a multi-item ``with`` header is classified on its oracle rather
# than on whichever context manager the author happened to type first.
# ``assert_call_kinds`` is the language's plain call node, not an assertion
# shape, so the header scan returned the first call it met and classified only
# that one: ``with pytest.raises(E), atomic():`` counted nothing while
# ``with atomic(), pytest.raises(E):`` counted one. Each item is classified now,
# and a declining call's arguments are not scanned, so an assertion passed as an
# argument still does not stand in for the header's oracle.
#
# v36: files a package manifest declares (package.json ``bin``, a built
# ``main`` mapped to its source, a distribution's package ``__init__``) are
# entry points, so perf findings reachable from them are marked so.
#
# v35: the update's full re-score reads every stored git column, so the
# percentile gates of ``prior_defect`` and ``co_change_scatter`` see their
# inputs, and it keeps the stored blame-marker findings it cannot recompute.
# A store an older re-score wrote is missing those findings.
#
# v34: ``hidden_coupling`` is advisory: still detected and stored, it no longer
# deducts from the defect score, so every stored score that carried one moves.
#
# v33: git history windows are measured from the indexed commit's committer
# date instead of wall-clock time, by default. ``code_age_volatility`` and the
# per-function blame rollup read line ages from the same anchor, so a store
# scored against wall-clock time holds different windowed findings for any repo
# whose last commit is not today.
#
# v32: the history (organizational) cap follows the file's structure half,
# ``history_cap(structure)`` in scoring.py, so every stored score that carries
# a history deduction, and the refactoring impact figures, move.
#
# v31: Pascal opts into assertion detection (``assert_call_kinds``): DUnit's
# ``Check``/``CheckEquals``/``Fail`` family (a new ``asserts/lexicon.py`` row,
# broad tier) and DUnitX's ``Assert.*`` plus the RTL's own ``Assert(cond, msg)``
# (narrow tier, no row needed -- ``Assert`` itself is an assert-prefixed
# identifier). A new ``expr_stmt_kinds`` field tells the walker that a call in
# flat statement position sits under a node named ``statement``, not the
# hardcoded ``expression_statement`` every other mapped grammar uses.
# ``block_kinds`` also gains ``statements`` (plural) -- a ``try``'s guarded
# body and its ``except``/``finally`` clauses use that distinct container, not
# ``block``, so every assertion inside a ``try ... finally Free; end`` (close
# to universal in Delphi tests) was invisible before this: measured on a real
# ~150-file Delphi codebase's Test*.dpr suite, assertion coverage moved from
# 66/120 files (1844 assertions) to 86/120 (2873). ``large_assertion_block`` /
# ``duplicated_assertion_block`` now fire for Pascal; ``assertion_free_test``
# deliberately does not -- it gates on a ``SHIPPING_LANGUAGES`` allowlist that
# needs its own measured-precision pass before Pascal joins it.
#
# v30: a Pascal call to a zero-argument procedure may omit its parentheses
# entirely (``Q.Open;``), which produces no ``exprCall`` node at all -- just a
# bare ``identifier`` / ``exprDot`` under a ``statement`` wrapper, invisible to
# the whole performance pass regardless of sink kind. The new
# ``bare_call_wrapper_kinds`` / ``PerfDialect.bare_statement_call`` hook (a
# no-op for every language that does not map it) tells that shape apart from
# the wrapper's other tenants (``Exit;`` / ``inherited;``), so ``Q.Open;`` now
# finds the same ``io_in_loop`` as ``Q.Open();`` already did.
#
# v29: Pascal's ``uses`` clause now feeds ``io_boundaries.collect_io_names``
# (``FireDAC`` / ``ADODB`` -> db, ``IdHTTP`` / ``System.Net.HttpClient`` ->
# network), and the Pascal ``PerfDialect`` gates ``TDataSet.Open`` /
# ``.ExecSQL`` / ``.Post`` and an HTTP client's ``.Get`` / ``.Post`` on that
# evidence -- a loop calling one of these now produces a ``db`` / ``network``
# ``io_in_loop`` where before it stayed silent (filesystem/subprocess only).
#
# v28: Pascal's ``foreach`` (``for x in collection do``) was absent from its
# ``loop_kinds``, so a for-in loop contributed no CCN and opened no nesting
# level -- stored complexity / nesting for any Pascal function using one
# understates both. Pascal also gained a ``PerfDialect`` (filesystem /
# subprocess sinks by RTL/VCL/FPC name), so ``io_in_loop`` / ``hot_path_sync_io``
# now fire for it instead of the pass silently skipping every Pascal file.
#
# v27: a new marker, ``lazy_load_in_loop`` (a lazy relationship read on each
# iteration of a loop over its rows); and ``unbounded_read_reduced_in_memory`` now
# runs on ``init`` too, where it never ran before. A v26 store built by ``init`` has
# neither marker; one built by ``update`` has the second.
#
# v26: ``.all()`` on an imported or module-global name (a plugin registry) is not a
# db sink, a SQL string saying ``LIMIT n`` caps a read, and a batched read is never
# proven for ``session.get`` or beside a call made for its effect; a v25 store differs.
#
# v25: repetition with nothing to change (a filesystem or subprocess boundary, or
# a loop already walking chunks) is stored as ``expected``; a v24 store says investigate.
#
# v24: ``session.get(Model, key)`` is a db sink, an awaited TS limiter closure runs
# in its loop, and Go/Java loops report chunking; a v23 store has none of them.
#
# v23: loop-shaped perf findings carry the loop's magnitude, the bulk form of a
# per-key sink and any concurrency bound around it; a v22 store has none.
#
# v22: perf findings in a chunked loop carry ``chunked_iteration``, and
# ``unbounded_read_reduced_in_memory`` is a new marker; a v21 store has neither.
#
# v21 moves two stored counts, both of which were order-dependent in the same
# way. ``assertion_count`` rises on such a header. ``mock_setup_count`` falls on
# it, because ``mock_walk._count_body_setup`` skips a statement
# ``_is_assertion_statement`` admits and that predicate runs this same branch --
# ``with patch(...), pytest.raises(E):`` already counted no mock setup in v20,
# and the reversed spelling now agrees with it. Both feed
# ``mock_saturated_test``, one per side of its ratio, and it is advisory.
#
# ``assertion_blocks`` does not move at all: a ``with`` header is capped at the
# broad tier, so it can never join a narrow run. Verified set-for-set against
# v20 on four corpora, with the average defect score unmoved on all four and no
# ``mock_saturated_test`` finding gained or lost. The two calibrated block
# markers read that field alone and are untouched, and every reader of the two
# counts above is advisory, so no score moves. Python is the only language that
# maps ``with_kinds`` and opts into assertion detection, so no other language's
# rows change. A cached v20 walk carries the old counts, and this stamp is what
# re-walks them.
#
# v20: every marker now reads ``FileContext.all_functions``, the full
# walked list, where eleven of them used to read a ``function_metrics`` map keyed
# by function name. That key kept one row per distinct name, so a file's anonymous
# ``it`` callbacks collapsed into one and a method shadowed its same-named
# neighbour on another class. What moves is how much of that walk each marker
# sees, on every scoring marker at once, which makes persisted rows wrong. The
# walk itself is unchanged, but this stamp keys the walk and duplication-token
# caches, so bumping it re-walks and re-tokenizes anyway.
#
# v20: ``assertion_free_test`` stops reading a containing symbol as the test.
# ``ExecutionGraphIndex.resolve_function`` falls back to the innermost symbol
# whose range holds the start line, which tolerates a decorator offset but also
# answers a wrapper declaration -- a TS ``const suite = describe(...)`` spans
# every callback inside it -- for every test in it at once. One delegating test
# then suppressed its silent siblings through that wrapper's edge. The oracle
# pass now asks for the function rather than its enclosure, so those findings
# come back, and nothing re-mints the rows a v19 store suppressed except this
# stamp.
#
# v20 also reads a hand-rolled ``raise`` / ``throw`` as an oracle, for which the
# walker records one new ``FunctionComplexity`` field, ``raise_count``, that a
# cached v19 walk does not carry at all. It is its own field rather than a term
# in ``assertion_count`` precisely so that it moves nothing calibrated:
# ``mock_saturated_test`` and ``large_assertion_block`` read ``assertion_count``
# and ``assertion_blocks`` directly and are untouched. Only
# ``assertion_free_test`` and the oracle pass behind it read the new field, both
# through ``asserts/predicate.checks_something``, and both as a boolean.
#
# v20 finally reads an assertion call from a position ``_assertion_tier`` never
# classifies, because that pass classifies statements: a ``const e =
# expect(x)`` is a declaration and a ``return expect(x).toBe(1)`` is a return,
# and neither reaches it. ``checks_something`` asks ``called_names`` for a
# callee under ``NARROW_PREFIXES`` as a floor under the count, and the walker
# now collects those names through nested function bodies rather than stopping
# at them -- a function nested in an already-collected one is never collected
# as an entry of its own, so its calls were recorded nowhere at all. The
# *counts* still stop at a nested function, so ``assertion_count`` is
# bit-identical to v19; only the name sets widen, and a cached v19 walk carries
# the narrower ones. No score moves; the marker is advisory.
#
# v19: ``assertion_free_test`` resolves a test's oracle across file
# boundaries, on a call edge rather than by name, so a test that delegates its
# checks to a helper in another module is no longer called assertion-free. The
# resolution itself is a graph pre-pass, but the walker also records one new
# ``FunctionComplexity`` field for it, ``bare_called_names``, which a cached
# v18 walk does not carry at all: it is the subset of ``called_names`` whose
# call site had no receiver, and the pass pairs it with a file-scoped call edge.
# The marker also reports fewer findings than a v18 store holds, and nothing
# re-mints those rows except this stamp. It counts nothing, so no score moves.
#
# v17: the walker records one new ``FunctionComplexity`` field,
# ``called_names``, which a cached v16 walk does not carry at all. It feeds the
# advisory ``assertion_free_test``, which no longer calls a test assertion-free
# when it handed its checks to a function in the same file that asserts. It
# counts nothing, so no calibrated marker reads it and no score moves.
#
# v16: the health pass reads a ``.tsx`` file with the JSX grammar.
# It arrives tagged ``typescript``, and the grammar that tag selects errors on
# the first ``<Component />``, so a cached v15 walk stored an ERROR-recovered
# tree. Three things stored under it change for ``.tsx`` files and nothing
# else: ``assertion_count`` and ``assertion_blocks`` (assertions after the
# first element were not seen at all), the clone token stream the duplication
# detector hashes, and the dataflow CFG. Function names change too -- recovery
# swallowed whole source spans into the callee name, and those accidentally
# unique names were defeating ``function_metrics``' name key, so the two
# calibrated block markers see a ``.tsx`` file the way they have always seen a
# ``.ts`` one. Non-``.tsx`` files are byte-identical, verified file for file.
#
# v15: ``assertion_free_test`` landed, and the walker records two new
# ``FunctionComplexity`` fields for it (``verification_count``, ``is_test_case``)
# that a cached v14 walk does not carry. It also widens ``assertion_count`` in
# every language that counts assertions at all -- a ``with`` header, a
# property-terminated chain, a lambda body, a private ``_assert*`` helper and
# Java's split call shape all reach the broad tier now -- so the advisory
# ``mock_saturated_test`` divides by a different, larger denominator than a v14
# walk stored. The narrow tier the calibrated block markers read is unchanged,
# verified run-for-run against v14 on four corpora, so no score moves.
#
# v14: the assertion count gained a per-language broad tier, so a Go
# or JS/TS file's ``assertion_count`` changes from what a cached v13 walk stored
# for it. The narrow tier the calibrated block markers read is unchanged, so no
# score moves; this stamp is what re-walks the files whose advisory denominator
# does. Go's row changes no finding until Go also has a mock dialect — it is
# there for the markers that read this count next.
#
# v13: ``mock_saturated_test`` gained a TypeScript / JavaScript
# vocabulary, so a TS or JS file's ``mock_setup_count`` changes from the zero a
# cached v12 walk stored for it.
#
# v12: ``mock_saturated_test`` landed and the walker records two new
# ``FunctionComplexity`` fields, which a cached v11 walk does not carry.
#
# v11: F# and Objective-C gained working complexity node maps and Elixir lost
# its broken one, so stored complexity changes for files in those languages.
#
# v10: non-code files stopped being scored and the deduction was split into its
# structure and history halves, stored per file.
#
# v9: C gained a complexity node map. ``LANGUAGE_NODE_MAPS`` had no
# ``c`` entry, so every C file scored as if it had no branches: ``max_ccn`` and
# the maintainability index were computed off an empty node set and persisted
# that way. The map is present now, so a C file's stored complexity metrics and
# the findings keyed off them change on the next update. C-free repos are
# unaffected; the stamp is what makes a C repo re-score instead of waiting out
# the decay timer.
#
# v8: paired-test detection changed. ``_has_paired_test_file`` had
# ``test_<stem>.py`` hardcoded, so the prefix layout only ever matched Python;
# it now follows the file's own suffix, and ``<stem>_spec`` joins the suffix
# forms. Files that were counted untested and are not become tested, which
# moves untested-hotspot findings and the scores that carry them, on every
# language with a prefix or spec convention rather than Ruby alone.
HEALTH_ANALYZER_VERSION = 42


def _log_duplication_diagnostics(report: DuplicationReport) -> None:
    """Emit a debug line when a duplication guard fired.

    Skipped bundles / capped buckets are otherwise invisible — surfacing
    them explains why a repo produced fewer clone findings than expected
    (and confirms the issue-#341 hang guards are doing their job).
    """
    diag = report.diagnostics
    if not diag:
        return
    if any(
        diag.get(k)
        for k in (
            "skipped_minified",
            "skipped_token_cap",
            "window_budget_hit",
            "degenerate_buckets",
            "timed_out",
        )
    ):
        log.debug("health_duplication_limits", **diag)


def _partner(file_path: str, pair: ClonePair) -> str:
    """The other file of *pair*: *file_path* itself for a clone inside it."""
    return pair.file_b if pair.file_a == file_path else pair.file_a


def _pct_of_kept(
    file_path: str, clones: list[ClonePair], kept: list[ClonePair], dup_pct: float | None
) -> float | None:
    """*dup_pct* scaled to the lines the *kept* clones still cover."""
    if len(kept) == len(clones) or not dup_pct:
        return dup_pct
    covered = union_line_count(clone_ranges(file_path, clones))
    share = union_line_count(clone_ranges(file_path, kept)) / covered if covered else 0.0
    return round(dup_pct * share, 2)


#: Partner files whose lines one analysis pass keeps (``_repo_lines``).
_PARTNER_LINES_CACHE = 256


def _percentile_p80(counts: list[int]) -> int | None:
    """80th percentile of *counts* using the inclusive-lower convention
    already used by ``churn_percentile`` in ``enrich.compute_percentiles``.
    Returns ``None`` for an empty list.
    """
    if not counts:
        return None
    counts = sorted(counts)
    idx_p80 = min(len(counts) - 1, max(0, int(0.8 * len(counts))))
    return counts[idx_p80]


def _full_repo_p80(
    value: int | None, changed_files: object, override: int | None
) -> int | None:
    """*value*, but only when this run is entitled to publish it repo-wide.

    An incremental run walks the changed files alone, so the percentile it
    derives describes a churn-heavy subset and must never be stored as the
    repo's. A run that was handed an override did not measure anything either.
    """
    if changed_files is not None or override is not None:
        return None
    return value


def _compute_repo_function_mod_p80(
    walked: list[tuple[Any, FileComplexity]],
    git_meta_map: dict[str, dict],
) -> int | None:
    """Compute the repo-wide 80th percentile of per-function modification counts.

    Uses the per-file ``BlameIndex`` produced by the FULL git tier. Returns
    ``None`` when blame is unavailable on every file (ESSENTIAL tier, or
    git indexing skipped entirely) — biomarkers treat ``None`` as the
    "no signal" outcome.
    """
    counts: list[int] = []
    for pf, fcx in walked:
        meta = git_meta_map.get(pf.file_info.path) or {}
        idx = meta.get("blame_index")
        if not isinstance(idx, BlameIndex) or not idx.lines:
            continue
        for fc in fcx.functions:
            mod_count = len(distinct_commits_in_range(idx, fc.start_line, fc.end_line))
            if mod_count > 0:
                counts.append(mod_count)
    return _percentile_p80(counts)


def _dependents_count(graph: Any, path: str) -> int:
    """Distinct files that use *path* in code: co-change is history, and a Go
    package sibling or a synthetic header pair edge uses nothing."""
    return len(set(file_dependency_neighbors(graph, path, incoming=True, uses_only=True)))


def _compute_repo_dependents_p80(dependents: dict[str, int]) -> int | None:
    """Repo-wide 80th percentile of file-level in-degree (dependents).

    Restricted to files that actually have ≥1 dependent — this is the
    "top quintile of *connected* files", mirroring the mod-count p80
    convention (which only counts functions that were actually modified).
    Returns ``None`` when no graph is available or no file has dependents,
    in which case centrality-percentile gates fall back to their fixed
    floor. Used by ``brain_method`` so its centrality gate adapts to
    sparse-graph languages (TS/Rust) instead of assuming Python's denser
    import graph.
    """
    return _percentile_p80([deg for deg in dependents.values() if deg > 0])


def _compute_repo_active_contributors(git_meta_map: dict[str, dict]) -> int | None:
    """Distinct non-bot contributors active in the repo's trailing 90 days.

    Derived from the per-author ``last_commit_ts`` timestamps already in
    ``top_authors_json`` — no extra git work. ``None`` = unknown (git
    skipped, or a pre-timestamp index); biomarkers then keep their
    historical behaviour rather than mis-gating on a phantom team size.
    """
    metas = [m for m in git_meta_map.values() if isinstance(m, dict)]
    if not metas:
        return None
    try:
        return count_active_contributors(metas)
    except Exception as exc:
        log.debug("health_active_contributors_failed", error=str(exc))
        return None


class HealthAnalyzer:
    """Pure-Python health analyzer. No LLM, no network."""

    def __init__(
        self,
        graph: Any,  # networkx.DiGraph
        git_meta_map: dict[str, dict] | None = None,
        parsed_files: list[Any] | None = None,
        coverage_map: dict[str, dict[str, Any]] | None = None,
        community_label_map: dict[str, str] | None = None,
        duplication_cache_dir: Any | None = None,
        repo_root: Any | None = None,
        source_reader: SourceReader | None = None,
        stored_blame_findings: dict[str, list[Any]] | None = None,
        file_dependents: dict[str, int] | None = None,
        tested_files: set[str] | frozenset[str] | None = None,
        walk_cache: HealthWalkCache | None = None,
        package_roots: set[str] | None = None,
        clone_index_dir: Any | None = None,
    ) -> None:
        self.graph = graph
        self.git_meta_map = git_meta_map or {}
        self.parsed_files = list(parsed_files or [])
        self._abs_paths = {pf.file_info.path: pf.file_info.abs_path for pf in self.parsed_files}
        # Clone partners' lines, shared by the test-clone gate and Extract
        # Helper's reuse check. Bounded: partners cluster, a pass reads few.
        self._repo_lines = functools.lru_cache(maxsize=_PARTNER_LINES_CACHE)(self._read_repo_lines)
        # Per-file coverage keyed by repo-relative POSIX path. Each value
        # is ``{line_coverage_pct, branch_coverage_pct, covered_lines,
        # total_coverable_lines}``. ``None``-equivalent files are simply
        # absent from the map.
        self.coverage_map = coverage_map or {}
        # Per-file community label keyed by repo-relative POSIX path,
        # populated from graph community detection by the orchestrator and
        # consumed only by the refactoring detectors. Distinct from the
        # ``module`` column, which is a path derived from package boundaries.
        self.community_label_map = community_label_map or {}
        # Directory for the duplication token/window cache (typically the
        # repo's ``.repowise``). None disables caching — the duplication
        # pass then re-tokenizes everything, exactly as before.
        self.duplication_cache_dir = duplication_cache_dir
        # The walk result for a file is a function of its bytes, its language
        # and the walker's version, so it is kept beside the index like the
        # parse trees and the clone windows are. Absent without a cache dir.
        # A *walk_cache* passed in is the caller's: it loads and saves it, so
        # several passes (both sides of a comparison) share one file.
        self._owns_walk_cache = walk_cache is None
        self._walk_cache: HealthWalkCache | None = walk_cache or (
            HealthWalkCache(duplication_cache_dir, HEALTH_ANALYZER_VERSION)
            if duplication_cache_dir is not None
            else None
        )
        # Checkout root, used only to read package boundaries off disk. None
        # falls back to inferring them from the analyzed file list, which sees
        # only the manifests the traverser emitted.
        self.repo_root = repo_root
        # Origins are decided in ``_walk``, which holds the bytes, and kept by
        # path: the walk cache keys on content alone, so a path-dependent
        # answer cannot live on the cached walk.
        self._project_name: str | None = None
        self._origins: dict[str, CodeOrigin] = {}
        self._installed_ui_dirs: dict[str, tuple[PurePosixPath, ...]] = {}
        # Every source read in the pass. Defaults to the working tree; a
        # revision comparison supplies bytes instead.
        self.read_source: SourceReader = source_reader or disk_source_reader
        # Stored ``BLAME_MARKERS`` findings by path, replayed for a file scored
        # without a blame index (a re-score from stored git metadata) so the
        # pass does not delete what it cannot recompute.
        self.stored_blame_findings = stored_blame_findings or {}
        # An index's ``.repowise`` for a pass over a few files: their clones
        # are spliced against the index's pair state (read-only) so partners
        # this pass never reads still count. ``clone_source`` says where the
        # last pass's clones came from: "index", "analyzed_files" (only among
        # the files it read), or None when duplication did not run.
        self.clone_index_dir = clone_index_dir
        self.clone_source: str | None = None
        # Dependents counts to gate centrality on instead of this graph's. A
        # revision comparison passes the other side's, so a file crossing a
        # centrality gate inside a partial graph is not read as a code change.
        self._file_dependents_override = file_dependents
        # Files the index records as tested (paired or reached), for a pass holding no tests.
        self._tested_files = frozenset(tested_files or ())
        # Supplied by a caller that already knows the roots its files sit under.
        self._package_roots_cache: set[str] | None = package_roots
        self._execution_graph_cache: CallGraphIndex | None = None
        self.test_index = RepoTestIndex(self.graph, self.parsed_files, self._execution_graph)
        self.perf_stage = CrossFunctionPerfStage(
            self.graph, self._execution_graph, self._origins, self.parsed_files, self.read_source
        )

    def file_dependents(self) -> dict[str, int]:
        """Each analysed file's dependents count, as the centrality gates read it."""
        if self._file_dependents_override is not None:
            return dict(self._file_dependents_override)
        if self.graph is None:
            return {}
        return {
            pf.file_info.path: _dependents_count(self.graph, pf.file_info.path)
            for pf in self.parsed_files
            if pf.file_info.path in self.graph
        }

    def _dependents(self, path: str) -> int:
        override = self._file_dependents_override
        if override is not None and path in override:
            return override[path]
        if self.graph is None or path not in self.graph:
            return 0
        return _dependents_count(self.graph, path)

    def _execution_graph(self) -> CallGraphIndex | None:
        """Build the reliable execution index at most once per analysis."""
        if self.graph is not None and self._execution_graph_cache is None:
            self._execution_graph_cache = CallGraphIndex(self.graph)
        return self._execution_graph_cache

    def _package_boundaries(self, analyzed_paths: set[str]) -> set[str]:
        """Package roots for this repo, decided once per analyzer.

        One answer for every file, so ``module`` is a property of the repo
        layout rather than of whichever code path happens to be running — the
        defect this replaced was four call sites disagreeing.

        Read off disk when a ``repo_root`` is known, because the analyzed file
        list only carries manifests the traverser could language-detect, and it
        drops ``go.mod``, ``pom.xml``, ``build.gradle``, ``Gemfile``,
        ``build.sbt`` and a dozen more. Falls back to the file list otherwise
        (in-memory callers and tests), which is the previous behaviour.
        """
        if self._package_roots_cache is not None:
            return self._package_roots_cache
        roots: set[str] | None = None
        if self.repo_root is not None:
            try:
                roots = _scan_package_roots(self.repo_root)
            except OSError as exc:
                # An unreadable tree must not fail the health pass; the file
                # list still gives the pre-existing answer.
                log.debug("health_package_root_scan_failed", error=str(exc))
        if roots is None:
            roots = _package_roots(analyzed_paths)
        self._package_roots_cache = roots
        return roots

    def analyze(
        self,
        config: dict | None = None,
        *,
        on_step: Any | None = None,
        changed_files: set[str] | list[str] | None = None,
        repo_function_mod_p80: int | None = None,
        timings: Any | None = None,
        duplication_files: set[str] | None = None,
    ) -> HealthReport:
        """Analyze the configured parsed files.

        Pass *changed_files* (repo-relative POSIX paths) for incremental
        runs from ``repowise update`` — the engine still needs the full
        parsed-file set to build duplication context (clones cross
        files), but only files in *changed_files* contribute findings /
        metrics. The caller is responsible for upserting (not replacing)
        the result against the existing rows.

        *repo_function_mod_p80* overrides the repo-wide 80th percentile of
        per-function modification counts that gates the Function Hotspot
        biomarker. On an incremental run ``walked`` holds only the changed
        files, so deriving the percentile from it would bias the gate
        toward churn-heavy subsets (issue #1484) — the caller should pass
        the percentile computed over the full repo (from the persisted
        ``git_function_blame`` rollup) instead. ``None`` (the default)
        computes it from the walked set as before.

        *timings* is a phase table; each stage of this pass records an
        ``analysis.health.*`` row so a slow pass names the stage.

        *duplication_files* narrows the clone detector's incremental splice to
        the files whose bytes changed. It defaults to *changed_files*, which on
        an update also carries the performance closure: files re-walked for
        their call paths, whose tokens did not move and whose clone pairs are
        already in the persisted index.
        """
        from repowise.core.pipeline.phase_timing import timed

        run = _RunConfig.read(config)
        changed_set = set(changed_files) if changed_files is not None else None
        dup_report = self._duplication(
            run,
            set(duplication_files) if duplication_files is not None else changed_set,
            timings,
        )
        with timed(timings, "analysis.health.walk"):
            walked = self._walk_files(self._target_files(changed_set), run.vocab, on_step)
        return self._score(
            run,
            walked,
            dup_report,
            changed_files=changed_files,
            repo_function_mod_p80=repo_function_mod_p80,
            on_step=on_step,
            timings=timings,
        )

    async def analyze_async(
        self,
        config: dict | None = None,
        *,
        on_step: Any | None = None,
        changed_files: set[str] | list[str] | None = None,
        max_workers: int | None = None,
        repo_function_mod_p80: int | None = None,
    ) -> HealthReport:
        """Parallel variant of :meth:`analyze` for large repos.

        Splits the per-file work across an ``asyncio.gather`` of
        ``asyncio.to_thread`` calls. Tree-sitter parsing releases the
        GIL, so this gives a real wall-clock win even on single-process
        Python — the 30s budget on a 3,000-file synthetic repo (plan §4
        P4.6) is met by this path.

        Duplication still runs once up-front (cross-file by nature), and
        the symbol-complexity write-back still runs on the main thread
        so ORM objects don't cross thread boundaries unexpectedly.

        *repo_function_mod_p80* overrides the repo-wide percentile that
        gates the Function Hotspot biomarker (see :meth:`analyze`); on an
        incremental run the caller passes the value computed over the full
        repo so the gate is not biased by the changed-files subset.
        """
        run = _RunConfig.read(config)
        changed_set = set(changed_files) if changed_files is not None else None
        # Duplication is only consumed by the biomarker stage, so it can
        # overlap with the pre-walk instead of blocking it — on large
        # repos the scan takes seconds during which the progress bar
        # would otherwise sit at zero.
        dup_task = (
            None
            if "dry_violation" in run.disabled
            else asyncio.ensure_future(asyncio.to_thread(self._duplication, run, changed_set, None))
        )
        target_files = self._target_files(changed_set)
        if not target_files:
            if dup_task is not None:
                dup_task.cancel()
            return HealthReport(
                repo_id="",
                analyzed_at=datetime.now(UTC),
                findings=[],
                metrics=[],
                kpis={},
            )
        walked = await self._walk_files_async(target_files, run.vocab, on_step, max_workers)
        return self._score(
            run,
            walked,
            await dup_task if dup_task is not None else DuplicationReport(),
            changed_files=changed_files,
            repo_function_mod_p80=repo_function_mod_p80,
            on_step=on_step,
            timings=None,
        )

    def _duplication(
        self, run: _RunConfig, changed: set[str] | None, timings: Any | None
    ) -> DuplicationReport:
        """Clone pairs for the pass, found once up front so each file's
        biomarkers see their clone list; empty when disabled or on failure.

        Repo-wide even for an incremental run, since a changed file's clone
        partners may be unchanged files: *changed* lets the detector splice
        its persisted pair index instead of recomputing it all.
        """
        from repowise.core.pipeline.phase_timing import timed

        self.clone_source = None
        # An empty splice set: no file whose clone list a biomarker reads changed.
        if "dry_violation" in run.disabled or changed == set():
            return DuplicationReport()
        if self.clone_index_dir is not None:
            report = self._clones_against_index()
            if report is not None:
                self.clone_source = "index"
                return report
        self.clone_source = "analyzed_files"
        try:
            with timed(timings, "analysis.health.duplication"):
                report = detect_clones(
                    self.parsed_files,
                    self.git_meta_map,
                    cache_dir=self.duplication_cache_dir,
                    source_reader=self.read_source,
                    changed_files=changed,
                )
            _log_duplication_diagnostics(report)
            return report
        except Exception as exc:
            log.debug("health_duplication_failed", error=str(exc))
            return DuplicationReport()

    def _clones_against_index(self) -> DuplicationReport | None:
        # A missing, stale or truncated clone state is ``None`` without raising,
        # so anything raised here is a defect: say so at warning rather than let
        # the files-only fallback pass for the expected no-index case.
        try:
            report = clones_against_index(
                self.parsed_files,
                self.git_meta_map,
                Path(self.clone_index_dir),
                source_reader=self.read_source,
            )
        except Exception as exc:
            log.warning("health_index_clones_failed", error=repr(exc))
            return None
        return report

    def _target_files(self, changed_set: set[str] | None) -> list[Any]:
        """The parsed files this pass scores: only the changed ones on an incremental run."""
        return [
            pf
            for pf in self.parsed_files
            if (changed_set is None or pf.file_info.path in changed_set)
            and scores_language(pf.file_info.language)
        ]

    def _walk_or_empty(self, pf: Any, vocab: AssertVocabulary) -> FileComplexity:
        try:
            return self._walk(pf, vocab)
        except Exception as exc:
            log.debug("health_walk_failed", path=pf.file_info.path, error=str(exc))
            return FileComplexity(functions=[], classes=[])

    def _walk_files(
        self, targets: list[Any], vocab: AssertVocabulary, on_step: Any | None
    ) -> list[tuple[Any, FileComplexity]]:
        """Walk every target before any biomarker runs, so the repo-wide
        percentiles are computed once over the whole walked set."""
        self._load_walk_cache()
        walked: list[tuple[Any, FileComplexity]] = []
        for pf in targets:
            walked.append((pf, self._walk_or_empty(pf, vocab)))
            # Walk tick — the phase total counts each file twice (walk +
            # evaluate).
            if on_step:
                on_step(pf.file_info.path)
        self._save_walk_cache()
        return walked

    async def _walk_files_async(
        self,
        targets: list[Any],
        vocab: AssertVocabulary,
        on_step: Any | None,
        max_workers: int | None,
    ) -> list[tuple[Any, FileComplexity]]:
        """:meth:`_walk_files` in worker threads: tree-sitter parsing releases
        the GIL, so this is real parallelism."""
        self._load_walk_cache()
        semaphore = asyncio.Semaphore(max(1, int(max_workers or os.cpu_count() or 4)))

        async def _one(pf: Any) -> tuple[Any, FileComplexity]:
            async with semaphore:
                fcx = await asyncio.to_thread(self._walk_or_empty, pf, vocab)
            # Walk tick — the phase total counts each file twice (walk +
            # evaluate) so the bar moves from the very first completed walk.
            if on_step:
                on_step(pf.file_info.path)
            return pf, fcx

        walked = list(await asyncio.gather(*[_one(pf) for pf in targets]))
        self._save_walk_cache()
        return walked

    def _score(
        self,
        run: _RunConfig,
        walked: list[tuple[Any, FileComplexity]],
        dup_report: DuplicationReport,
        *,
        changed_files: set[str] | list[str] | None,
        repo_function_mod_p80: int | None,
        on_step: Any | None,
        timings: Any | None,
    ) -> HealthReport:
        """Everything after the walk, shared by :meth:`analyze` and
        :meth:`analyze_async` so the two paths cannot drift apart."""
        from repowise.core.pipeline.phase_timing import timed

        with timed(timings, "analysis.health.repo_stats"):
            fn_mod_p80 = (
                repo_function_mod_p80
                if repo_function_mod_p80 is not None
                else _compute_repo_function_mod_p80(walked, self.git_meta_map)
            )
            full_repo_fn_mod_p80 = _full_repo_p80(
                fn_mod_p80, changed_files, repo_function_mod_p80
            )
        # Cross-file test oracles, same rule: resolve before the marker runs.
        with timed(timings, "analysis.health.oracle_reach"):
            self._apply_cross_file_oracles(walked)
        # One shared dataflow service for the whole pass: the promotion pass
        # and the Extract Method detector read the same lazily parsed
        # per-file object, so no file is parsed twice for dataflow.
        dataflow_cache = FileDataflowCache(self.read_source)
        self.perf_stage.run(walked, dataflow_cache, timings)
        # Before the evaluation: untested_complexity reads each function's nearest test.
        with timed(timings, "analysis.health.function_tests"):
            function_tests = self.test_index.function_tests()
        with timed(timings, "analysis.health.repo_stats"):
            evaluator = self._file_evaluator(
                run, dup_report, fn_mod_p80, dataflow_cache, function_tests
            )
        with timed(timings, "analysis.health.evaluate"):
            metrics, findings, suggestions = self._evaluate_all(evaluator, walked, on_step)
        # KPIs are repo-wide; on an incremental run they would be biased
        # by the changed-files subset. Skip them in that case — the
        # ``persist`` step recomputes KPIs from the merged DB rows.
        kpis = self._kpis(metrics, findings) if changed_files is None else {}
        with timed(timings, "analysis.health.finalize"):
            execution_roles = self._mark_perf_entry_reachability(findings)
            suggestions = self._with_performance_plans(run, findings, suggestions)
        return HealthReport(
            repo_id="",
            analyzed_at=datetime.now(UTC),
            findings=findings,
            metrics=metrics,
            kpis=kpis,
            function_blame_rows=self._function_blame_rows(walked),
            execution_roles=execution_roles,
            function_facts=self._function_fact_rows(walked),
            function_tests=function_tests,
            repo_function_mod_p80=full_repo_fn_mod_p80,
            refactoring_suggestions=suggestions,
            performance_plan_policy=PerformancePlanPolicy(
                enabled=run.performance_fix,
                min_confidence=run.refactoring_min_confidence,
            ),
        )

    def _file_evaluator(
        self,
        run: _RunConfig,
        dup_report: DuplicationReport,
        fn_mod_p80: int | None,
        dataflow_cache: FileDataflowCache,
        function_tests: FunctionTests | None = None,
    ) -> FileEvaluator:
        """The repo-wide inputs every file's evaluation reads, built once per pass."""
        analyzed_paths = {pf.file_info.path for pf in self.parsed_files}
        return FileEvaluator(
            self,
            paired_tests=self.test_index.paired(analyzed_paths)
            | (self._tested_files & analyzed_paths),
            package_roots=self._package_boundaries(analyzed_paths),
            dup_report=dup_report,
            run=run,
            graph_view=ImportEdgeView(self.graph) if self.graph is not None else None,
            function_mod_p80=fn_mod_p80,
            dependents_p80=_compute_repo_dependents_p80(self.file_dependents()),
            active_contributors=_compute_repo_active_contributors(self.git_meta_map),
            file_scc_index=build_file_scc_index(self.graph),
            methods_by_file=build_methods_by_file(self.graph),
            dataflow_cache=dataflow_cache,
            function_tests=function_tests,
            paired_test_files=paired_test_index(self.test_index.all_test_files()),
        )

    def _evaluate_all(
        self,
        evaluator: FileEvaluator,
        walked: list[tuple[Any, FileComplexity]],
        on_step: Any | None,
    ) -> tuple[list[HealthFileMetricData], list[HealthFindingData], list[RefactoringSuggestion]]:
        metrics: list[HealthFileMetricData] = []
        findings: list[HealthFindingData] = []
        suggestions: list[RefactoringSuggestion] = []
        for pf, fcx in walked:
            # Side effect: stamp each matched symbol's complexity_estimate.
            self._populate_symbol_complexity(pf, fcx.functions)
            file_metric, file_findings, file_suggestions = evaluator.evaluate(pf, fcx)
            # Every dataflow consumer has read this file by now.
            evaluator.dataflow_cache.release(pf.file_info.abs_path)
            metrics.append(file_metric)
            findings.extend(file_findings)
            suggestions.extend(file_suggestions)
            if on_step:
                on_step(pf.file_info.path)
        return metrics, findings, suggestions

    def _kpis(
        self, metrics: list[HealthFileMetricData], findings: list[HealthFindingData]
    ) -> dict[str, Any]:
        hotspot_paths = {p for p, meta in self.git_meta_map.items() if self._is_hotspot(meta)}
        return compute_kpis(metrics, hotspot_paths, deductions=deduction_by_path(findings))

    def _with_performance_plans(
        self,
        run: _RunConfig,
        findings: list[HealthFindingData],
        suggestions: list[RefactoringSuggestion],
    ) -> list[RefactoringSuggestion]:
        """Link the performance findings into opportunities, add their fix
        plans to *suggestions*, and rank the whole list."""
        link_performance_findings(findings)
        opportunities = build_performance_opportunities(findings)
        if run.performance_fix:
            suggestions.extend(
                performance_fix_suggestions(
                    opportunities,
                    min_confidence=run.refactoring_min_confidence,
                )
            )
        return rank_suggestions(suggestions, centrality=self._refactoring_centrality(suggestions))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refactoring_centrality(self, suggestions: list[RefactoringSuggestion]) -> dict[str, float]:
        """File-dependency centrality (importer in-degree) for each file a
        suggestion targets — the cheap, deterministic proxy ``rank`` blends
        with impact + blast radius. Empty when the health pass ran without a
        graph (the ranking then degrades to impact + blast only)."""
        if self.graph is None:
            return {}
        out: dict[str, float] = {}
        for s in suggestions:
            path = s.file_path
            if path in out or path not in self.graph:
                continue
            out[path] = float(self._dependents(path))
        return out

    def _apply_cross_file_oracles(self, walked: list[tuple[Any, FileComplexity]]) -> None:
        """Resolve each test's oracle across file boundaries, in place.

        A test that hands its checks to an asserting helper in another file is
        not assertion-free, and the per-function count cannot see that. This
        attaches the resolved lines so ``assertion_free_test`` stays a pure
        function over its context. Failure-isolated and a no-op without a
        graph, in which case the marker behaves exactly as it did before.
        """
        try:
            index = self._execution_graph()
            if self.graph is None or index is None:
                return
            by_file = collect_cross_file_oracles(walked, self.graph, index=index)
            for _pf, fcx in walked:
                resolved = by_file.get(_pf.file_info.path)
                if resolved:
                    fcx.cross_file_oracles = resolved
        except Exception as exc:
            log.debug("health_cross_file_oracles_failed", error=str(exc))
            return

    def _mark_perf_entry_reachability(
        self, findings: list[HealthFindingData]
    ) -> ExecutionRoles | None:
        """Stamp entry reachability and execution role without walking once per row.

        The role is keyed on the loop-owning function: the path's first node
        when the cost crosses functions, else the symbol holding the finding.
        The owner is stored too, so the writer can restamp a stored finding
        this run did not rescan. Returns the roles for that writer.
        """
        index = self._execution_graph()
        perf = [finding for finding in findings if finding.dimension == "performance"]
        if index is None or self.graph is None:
            return None
        entry_files = {
            node_id
            for node_id, data in self.graph.nodes(data=True)
            if data.get("node_type") == "file" and data.get("is_entry_point")
        }
        seeds = {symbol for path in entry_files for symbol in index.declares.get(path, ())}
        reachable = index.forward_reachable(seeds) if seeds and perf else None
        try:
            roles = ExecutionRoles.build(self.graph, index)
        except Exception as exc:
            log.debug("health_execution_roles_failed", error=str(exc))
            roles = None
        for finding in perf:
            path = finding.details.get("path")
            if reachable is not None and isinstance(path, list) and path:
                finding.details["reliable_entry_reachability"] = path[0] in reachable
            if roles is not None:
                owner = roles.owner_of(finding.file_path, finding.line_start, finding.details)
                finding.details["role_owner"] = owner
                finding.details["execution_role"] = roles.role_of(owner, finding.file_path)
        return roles

    def _function_fact_rows(
        self, walked: list[tuple[Any, FileComplexity]]
    ) -> list[dict] | None:
        """One row per walked function the graph names, for the ``function_facts`` store.

        A function the graph holds no symbol for has no id to key on, and is left out.
        Test files are left out too: no reader needs their functions yet (perf and
        Extract Method serve production code). Drop the filter when one does.
        """
        index = self._execution_graph()
        if index is None:
            return None
        rows: list[dict] = []
        for pf, fcx in walked:
            path = pf.file_info.path
            if pf.file_info.is_test:
                continue
            for fc in fcx.functions:
                symbol = index.resolve_function(path, fc.start_line, func_end=fc.end_line)
                if symbol is None:
                    continue
                facts = fc.facts
                rows.append(
                    {
                        "symbol_id": symbol,
                        "awaits": facts.awaits if facts else None,
                        "is_generator": facts.is_generator if facts else None,
                        "uses_receiver": facts.uses_receiver if facts else None,
                        "receiver_assigns": facts.receiver_assigns if facts else None,
                        "receiver_assigns_known": (
                            facts.receiver_assigns is not None if facts else None
                        ),
                        "early_exits": facts.early_exits if facts else None,
                        **fc.stored_metrics(),
                    }
                )
        return rows

    def _function_blame_rows(self, walked: list[tuple[Any, FileComplexity]]) -> list[dict]:
        """Build the per-function blame rollup from the walked files + the
        FULL-tier blame indexes attached to ``git_meta_map``.

        Cheap (reads the already-materialised blame index; no extra git) and
        failure-isolated so a rollup hiccup never breaks the health report.
        Returns an empty list on the ESSENTIAL tier (no blame indexes).
        """
        try:
            from .function_blame_rollup import build_function_blame_rows

            return build_function_blame_rows(list(walked), self.git_meta_map)
        except Exception as exc:
            log.debug("function_blame_rollup_failed", error=str(exc))
            return []

    def _walk(self, pf: Any, vocab: AssertVocabulary) -> FileComplexity:
        path = pf.file_info.abs_path
        language = pf.file_info.language
        source = self.read_source(path)
        if source is None:
            return FileComplexity(functions=[], classes=[])
        self._origins[pf.file_info.path] = self._origin(pf, source)
        key = None
        if self._walk_cache is not None:
            from repowise.core.ingestion import compute_content_hash
            from repowise.core.ingestion.parser import grammar_tag_for

            # The grammar, not the language: a .tsx file and a byte-identical
            # .ts file are walked by different grammars and must key apart.
            key = HealthWalkCache.key(
                grammar_tag_for(language, path), compute_content_hash(source), vocab.key
            )
            cached = self._walk_cache.get(key)
            if cached is not None:
                return cached
        if language == "sql":
            # SQL has no tree-sitter grammar here; the sqlglot-backed walker
            # produces routine CCN + the sql_* smell hits instead.
            from .sql_complexity import walk_sql_file

            fcx = walk_sql_file(pf.file_info, source)
        else:
            fcx = walk_file(path, language, source, vocab.names)
        if key is not None and self._walk_cache is not None:
            self._walk_cache.put(key, fcx)
        return fcx

    def _project(self) -> str | None:
        """The repository's name, so its own release banner is never read as a
        vendored library's. The ``origin`` remote names it even in a worktree;
        the checkout folder is the fallback. One git call per pass, on first use.
        """
        if self._project_name is None and self.repo_root is not None:
            root = str(self.repo_root)
            self._project_name = remote_name(root) or Path(root).name
        return self._project_name

    def _origin(self, pf: Any, source: bytes | None = None) -> CodeOrigin:
        return code_origin(
            pf.file_info.path,
            source,
            is_test=bool(pf.file_info.is_test),
            project=self._project(),
            repo_root=self.repo_root,
        )

    def _installed(self, path: str) -> bool:
        return self.repo_root is not None and is_installed_component(
            self.repo_root, path, self._installed_ui_dirs
        )

    def _without_kit_clones(
        self, file_path: str, clones: list[ClonePair], dup_pct: float | None
    ) -> tuple[list[ClonePair], float | None]:
        """*clones* less those both of whose sides are components a component
        CLI installed.

        Copies of one upstream kit (shadcn/ui's ``dialog.tsx`` and
        ``alert-dialog.tsx``, or the repeated item wrappers inside one of them)
        are similar by design, so such a clone is not duplication anyone here
        wrote. Every other finding on those files stands, and a clone with a
        file outside the kit still counts.
        The file's duplication share shrinks with the lines the dropped clones
        alone covered.
        """
        if not clones or not self._installed(file_path):
            return clones, dup_pct
        kept = [p for p in clones if not self._installed(_partner(file_path, p))]
        return kept, _pct_of_kept(file_path, clones, kept, dup_pct)

    def _load_walk_cache(self) -> None:
        if self._walk_cache is not None and self._owns_walk_cache:
            self._walk_cache.load()

    def _save_walk_cache(self) -> None:
        """Persist the walk entries this pass used or produced, if any."""
        if self._walk_cache is not None and self._owns_walk_cache:
            self._walk_cache.save()
            log.debug(
                "health_walk_cache",
                hits=self._walk_cache.hits,
                misses=self._walk_cache.misses,
            )

    def _populate_symbol_complexity(self, pf: Any, fc_list: list[FunctionComplexity]) -> None:
        if not fc_list:
            return
        # Index function metrics by (start_line, end_line) for fast lookup.
        by_range = {(fc.start_line, fc.end_line): fc for fc in fc_list}
        by_name = {fc.name: fc for fc in fc_list}
        for sym in pf.symbols:
            fc = by_range.get((sym.start_line, sym.end_line)) or by_name.get(sym.name)
            if fc is None:
                continue
            # Cap at the ORM Integer; CCN beyond ~10k is implausible.
            sym.complexity_estimate = int(min(fc.ccn, 9999))

    def _read_repo_lines(self, path: str) -> list[str] | None:
        """*path*'s lines: the parsed file's own path, else under the repo root
        (an update's parsed set is the whole tree, but a caller may pass less)."""
        abs_path = self._abs_paths.get(path)
        if abs_path is None and self.repo_root is not None:
            abs_path = str(Path(self.repo_root) / path)
        return _read_source_lines(abs_path, self.read_source) if abs_path else None

    def _is_hotspot(self, meta: dict | object) -> bool:
        if isinstance(meta, dict):
            return bool(meta.get("is_hotspot", False))
        return bool(getattr(meta, "is_hotspot", False))
