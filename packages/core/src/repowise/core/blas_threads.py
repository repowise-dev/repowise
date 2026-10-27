"""Cap the BLAS thread pool before numpy is ever imported.

Importing numpy brings up a BLAS runtime, and OpenBLAS commits a private
per-thread workspace for as many threads as the host has cores. That memory is
committed at import, is never returned, and is invisible to ``tracemalloc``
because no Python object owns it — which is why it went unattributed in
``#1394`` for so long: three separate reads of the generation code could not
find it, since it is not in the generation code and it is not Python.

Measured on a 32-core Windows host, process private bytes charged to
``import numpy`` alone, with no repowise work running at all:

======================  ==========
``OPENBLAS_NUM_THREADS``  cost
======================  ==========
1                        6 MB
2                       38 MB
4                      103 MB
8                      231 MB
16                     488 MB
32 (unset, = cores)    746 MB
======================  ==========

Roughly 32 MB per thread, linear, and entirely independent of repository size:
the same 746 MB is paid indexing a ten-file repo as a ten-thousand-file one.

**We do not need those threads.** The only numpy/scipy work in an index is
PageRank over a sparse graph and small vector arithmetic. Measured on the same
host, ``nx.pagerank`` over a 10,000-node graph allocates 1.5 MB and returns in
well under a second, single-threaded. Threaded BLAS pays off on large dense
matrix multiplies, which this pipeline never performs.

End to end on a 876-file C# repo, peak process memory fell from 1,547 MB to
777 MB — half — with wall clock unchanged (3m37s against 3m45s and 3m56s for
two unpinned baselines).

**``OMP_NUM_THREADS`` is deliberately not set here.** It looks like the same
knob and is not: igraph's community detection is OpenMP-parallel and really
does use those threads. Pinning it alongside the BLAS variables cost 18% wall
clock (4m30s against 3m45s) on the same repo for no extra memory saving. The
saving is entirely a BLAS-allocator effect, so only BLAS is pinned.

An explicit setting from the environment always wins: someone who has tuned
these deliberately gets what they asked for.
"""

from __future__ import annotations

import os

#: The per-backend thread-count variables, one per BLAS implementation numpy
#: may be linked against. Setting one that does not apply is harmless — the
#: absent runtime never reads it.
_BLAS_THREAD_VARS = (
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


def limit_blas_threads(threads: int = 1) -> None:
    """Pin every BLAS backend to *threads*, unless already set explicitly.

    Must be called before the first ``import numpy`` in the process to have any
    effect: the workspace is committed during BLAS initialisation, and a later
    change to the environment cannot give it back.

    Environment variables rather than a runtime API because the runtime APIs
    (``threadpoolctl``, ``mkl.set_num_threads``) only exist after numpy is
    imported, by which point the memory is already gone. Setting the
    environment also carries to spawned worker processes for free, which
    matters here: the parse pool and the betweenness pool each start fresh
    interpreters that would otherwise pay the full cost again, per worker.
    """
    for name in _BLAS_THREAD_VARS:
        # Respect an explicit choice. `setdefault` rather than assignment is
        # the whole of the opt-out: `OPENBLAS_NUM_THREADS=8 repowise init`
        # keeps eight.
        os.environ.setdefault(name, str(threads))
