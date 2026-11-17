"""Static test-to-code map, derived from the dependency graph.

``test_coverage`` answers "which test executed this line" and is filled only by
a coverage report. Most repositories have none, so ``tests_to_run`` came back
empty, ``impacted_tests`` said "run the full suite", and ``untested_hotspot``
fell back to matching filenames.

Matching filenames fails both ways, and this repository is the proof. Of its six
worst bug-magnet files, five - ``call_resolver.py``, ``dead_code/analyzer.py``,
``pipeline/persist.py``, ``tool_answer/answer.py``, ``pr_blast.py`` - have no
file named for them anywhere under ``tests/`` and so read as untested, while the
graph names the test files that reach them. The sixth,
``analysis/health/engine.py``, is worse: the convention matches on basename
alone, so it paired with ``tests/unit/distill/test_engine.py`` - a different
engine, in a different subsystem - and called the file tested on the strength of
a name collision.

The graph already knows the answer. This module reads that relation. It is a
second signal beside the measured one, never a replacement and never averaged
with it.

Which graph, and why
--------------------
Two graphs can answer "does a test reach this file", and they are not equally
good at it.

The **call graph** is the primary signal: seed the symbols a test file declares,
walk ``EXECUTION_EDGE_TYPES`` forward, and every symbol reached is one the test
can actually run. This is the closer model of the question - the claim is about
execution, and these edges are execution.

The **import graph** is the broader, weaker tier: a test file that imports a
source file references it, which is real evidence and a much cruder one. A test
importing a module pulls in every symbol the module defines while the test body
may touch one function.

Measured against real per-test coverage on this repository,
over the slice where per-test attribution is complete and both sides see the
same 37 test files (159 production files provably executed):

============================  =======  =======  =========  =======
forward signal                 claims  correct  precision   recall
============================  =======  =======  =========  =======
import graph, 1 hop                43       31      72.1%    19.5%
call graph, 3 hops                 48       44      91.7%    27.7%
call graph, 3 hops, filtered       46       44      95.7%    27.7%
both unioned                       57       45      78.9%    28.3%
============================  =======  =======  =========  =======

The call graph wins on both axes, so it leads. Unioning the import graph into it
buys 0.6 points of recall for 16.8 points of precision, and a false "something
reaches this" suppresses a real untested-hotspot finding, so the forward walk
does not union - it is call edges only.

The reverse walk combines them differently, because there the import tier can be
spent only on the targets the call graph said nothing about:

=============================  =======  ======  =========
reverse signal                 targets     hit  precision
=============================  =======  ======  =========
import graph, 1 hop                 32   96.9%      94.8%
call graph, 3 hops, filtered        46  100.0%      97.5%
both unioned                        47  100.0%      95.8%
call graph, else import graph       47  100.0%      97.5%
=============================  =======  ======  =========

Falling back is free where unioning is not: it answers one more target at
identical precision, because the second tier never speaks over the first.

Both tables compare the signals uncapped and over the same test set, which is
what makes them a choice between signals rather than two different experiments.
End to end through this module, with the walk seeing the repository's whole test
set and ``MAX_TESTS_PER_TARGET`` applied, the reverse walk scores 100.0% hit and
95.5% precision on the same slice; the gap is the cap truncating a repo-wide
list, not a difference in the walk.

Depth, and why 3
----------------
The call walk saturates at 3 hops - 3, 4 and 5 return the same 48 claims and 44
confirmations - so the recall ceiling is the call graph's own capture rate, not
the depth, and there is nothing to buy by walking further. The import walk keeps
its measured default of 1: a blanket second hop was tested and recovers 20 of
128 misses while adding 50 wrong claims, and both a facade-only second hop
through ``__init__.py`` (3 recovered, 5 added) and ``conftest.py`` transitivity
(zero recovered) were tested and disproved. What the import graph misses is
transitive *execution*, which import edges structurally cannot see, which is
what the call graph is for.

Those numbers rank tests for a file. Saying a file has *no* test is a different
claim, and the default depth over-made it: on a 63-file change to this
repository, 224 dependents read as untested, and in a hand-labelled sample
only 41% of the flagged files were. A caller about to make that claim passes
``helpers`` (``pr_blast.file_tests``; the health pass's pairing does the same),
which adds the helper or barrel hop and :func:`~.walk.package_walk`: a test
loading the file through its own package (an ``__init__`` re-export chain, a
registry beside the modules it imports), never through a hub, never from
another package. Flagged-and-truly-untested rose from 41% to 71% with every
real gap in the sample kept; 32 of 35 sampled files it newly calls tested
were. The import tier's default stays 1, so test ranking and selection are
unchanged.

Last, :func:`~.walk.use_walk` lets reach cross packages on symbol evidence:
call edges back from the file's symbols (``DEFAULT_CALL_DEPTH``) to code in
other files that calls into it, then the tests that call that code or load its
file. Importing something that imports the file is not enough, a symbol with
more than ``HUB_MIN_FAN_IN`` callers in code neither carries nor joins, and a
``conftest.py`` loading every router is no test. Files with no code of their
own (re-export barrels, ``package-info.java``) are ``type_only``. On the
labelled set in ``tests/fixtures/untested_eval`` (149 files, Python,
TypeScript, Go, Java), precision of "untested" rose from 55% to 84% with
recall of real gaps unchanged at 88%.

Why recall reads low, and why that is not the graph's fault
-----------------------------------------------------------
27.7% looks poor for a graph this dense, and the reason is what the ground truth
counts. ``coverage`` records a line as run whether a test called into it or
Python merely evaluated the module body on import, so a file that was only
imported is indistinguishable in the truth set from one a test drove. Splitting
the 159 truth files by how much of what ran was *inside a function body*:

=================================  =======  =======  ========
what actually ran in the file        files    found    recall
=================================  =======  =======  ========
nothing inside any function body        39        0        0%
under a quarter inside bodies           19        0        0%
a quarter to three quarters             45        3        7%
over three quarters inside bodies       56       43       77%
=================================  =======  =======  ========

A quarter of the "covered" files never ran a single line inside a function. The
walk is right to miss those: nothing called them, and claiming otherwise is the
import graph's error, which is what it costs 16.8 points of precision to make.

Of the 13 missed in the bottom row, 11 are ``alembic/versions/*`` migrations,
whose ``upgrade``/``downgrade`` the framework invokes by naming convention with
no static caller anywhere in the repository. Excluding those, the walk finds 43
of 45 files that a test genuinely exercised and that anything statically calls.
Chasing the headline number by widening the walk trades that away.

Confidence, and why one origin is dropped
-----------------------------------------
``graph_edges.resolution_origin`` records which strategy resolved a call edge,
and they are not equally trustworthy. ``global_unique`` binds a name to the only
symbol carrying it anywhere in the repository, which is a guess, and it is the
one origin the vocabulary itself scores at 0.50. Dropping it costs no recall at
all and buys 4.0 points of forward precision (91.7% -> 95.7%) and 1.1 of reverse
(96.4% -> 97.5%). Nothing else earns its keep: filtering ``receiver_global`` too
changes no number, and a confidence floor of 0.90 cuts recall from 27.7% to
23.3%.

What it claims, and what it does not
------------------------------------
Reaching is not executing. A call edge says control *can* flow, not that a given
test run did, so the inferred map **over-claims**, and the direction of the
error is known and one-sided:

* Sound as a floor. "Some test reaches this file, so do not call it untested"
  is safe: the edge is recorded, not guessed.
* Unsound as a quantity. No percentage may be derived from it. There is no line
  attribution here at all - reaching is a file-level fact - so a caller that
  needs "are these changed *lines* covered" must use the measured map or say it
  does not know.

Every surface that consumes this labels the result ``inferred``. Nothing here
writes to ``test_coverage``, and no row it produces is stored: the relation is a
bounded walk over ``graph_edges``/``graph_nodes``, which are already indexed and
already fresh. Materialising it would cost a transitive closure that goes stale
the moment the graph moves, to answer a query that is a breadth-first search
over data already in the database.

Layout
------
The walks are written once, over the needs they yield (:mod:`.walk`), and run
either over a graph held in memory (:func:`build_walk_maps`, no database and no
checkout) or over the stored graph (:mod:`.db`). :func:`reach_states` is the
typed answer, the one that may say "no test reaches this".
"""

from ..test_selection import is_runnable_test
from .db import (
    always_run_test_files,
    any_reached,
    any_tests_reaching,
    cached_file_paths,
    cached_files_reached_by_tests,
    cached_hub_files,
    cached_import_graphs,
    cached_placement,
    cached_test_files,
    cached_test_masks,
    call_graph_from_db,
    clear_test_map_cache,
    dependency_path,
    direct_dependents,
    direct_importers,
    imported_names_by_test,
    load_import_graph,
    load_test_files,
    placed_test_files,
    reach_into_symbols,
    reach_states_from_db,
    stored_origins,
    tests_reaching,
    tests_reaching_by_tier,
    tests_reaching_through_helpers,
    unscanned_test_files,
    write_test_masks_snapshot,
)
from .forward import (
    CallGraphView,
    call_graph_from_graph,
    files_reached_by_tests,
    files_with_paired_tests,
)
from .hubs import call_hub_files, hub_files
from .inline import go_inline_tests, inline_tests, rust_has_inline_tests
from .maps import ImportGraph, WalkMaps, build_walk_maps
from .mocks import replaced_files, replaced_modules
from .model import (
    DEFAULT_CALL_DEPTH,
    DEFAULT_MAX_DEPTH,
    MAX_TESTS_PER_TARGET,
    TRANSITIVE_HOPS,
    UNRELIABLE_CALL_ORIGINS,
    ReachDistance,
    ReachedBy,
    ReachedVia,
    ReachRow,
    ReachState,
    rank_reaching,
    tests_matching_by_name,
)
from .type_only import is_type_only, read_under, type_only_files
from .walk import CLOSURE_SEED_CAP, reach_states, reaching_by_tier

__all__ = [
    "CLOSURE_SEED_CAP",
    "DEFAULT_CALL_DEPTH",
    "DEFAULT_MAX_DEPTH",
    "MAX_TESTS_PER_TARGET",
    "TRANSITIVE_HOPS",
    "UNRELIABLE_CALL_ORIGINS",
    "CallGraphView",
    "ImportGraph",
    "ReachDistance",
    "ReachRow",
    "ReachState",
    "ReachedBy",
    "ReachedVia",
    "WalkMaps",
    "always_run_test_files",
    "any_reached",
    "any_tests_reaching",
    "build_walk_maps",
    "cached_file_paths",
    "cached_files_reached_by_tests",
    "cached_hub_files",
    "cached_import_graphs",
    "cached_placement",
    "cached_test_files",
    "cached_test_masks",
    "call_graph_from_db",
    "call_graph_from_graph",
    "call_hub_files",
    "clear_test_map_cache",
    "dependency_path",
    "direct_dependents",
    "direct_importers",
    "files_reached_by_tests",
    "files_with_paired_tests",
    "go_inline_tests",
    "hub_files",
    "imported_names_by_test",
    "inline_tests",
    "is_runnable_test",
    "is_type_only",
    "load_import_graph",
    "load_test_files",
    "placed_test_files",
    "rank_reaching",
    "reach_into_symbols",
    "reach_states",
    "reach_states_from_db",
    "reaching_by_tier",
    "read_under",
    "replaced_files",
    "replaced_modules",
    "rust_has_inline_tests",
    "stored_origins",
    "tests_matching_by_name",
    "tests_reaching",
    "tests_reaching_by_tier",
    "tests_reaching_through_helpers",
    "type_only_files",
    "unscanned_test_files",
    "write_test_masks_snapshot",
]
