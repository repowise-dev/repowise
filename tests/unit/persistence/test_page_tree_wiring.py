"""Every path that writes pages must rebuild the tree afterwards.

Placement cannot be worked out by a run that holds part of the wiki, so it is
recomputed from the store after persisting. That only holds if every persist
path actually does it, and a missing call site is invisible: the pages are
written, nothing raises, and the tree quietly loses the rows that run touched.

Two call sites were missing when this was first written, and both were found
by reading rather than by a failing test, because nothing asserted the wiring.
This does.

A source-level check is a blunt instrument. It is used here because the
alternative is constructing a full pipeline result per path, and because the
failure being prevented is precisely "somebody added a persist path and did
not know about this rule" rather than anything about runtime behaviour.
"""

from __future__ import annotations

import inspect

import pytest

_PERSIST_PATHS = [
    ("repowise.core.pipeline.persist", "the full index"),
    ("repowise.core.pipeline.incremental", "the incremental index"),
    ("repowise.core.pipeline.scoped_generation", "a scoped regeneration"),
    ("repowise.cli.commands.update_cmd.persistence", "the docs update"),
    ("repowise.cli.commands.upgrade_flow", "the fast-to-full upgrade"),
]

# The template update (``update_cmd.deterministic``) writes pages and does NOT
# rebuild, which is the one exemption to the rule above. It is allowed because
# its caller runs ``persist_incremental_index`` unconditionally straight after
# it, in the same command, and that rebuild sees strictly more than this one
# could (it runs after the sweeps and tombstones). The exemption is not taken on
# trust: ``test_the_template_update_defers_to_the_persist_that_follows_it`` below
# asserts the call ordering that makes it safe, so deleting or reordering that
# caller fails here rather than silently unplacing every re-rendered page.
_DEFERRED_PATHS = [
    (
        "repowise.cli.commands.update_cmd.deterministic",
        "the template update",
        "repowise.cli.commands.update_cmd.command",
    ),
]


@pytest.mark.parametrize("module_name,description", _PERSIST_PATHS)
def test_persist_path_rebuilds_the_tree(module_name: str, description: str):
    module = __import__(module_name, fromlist=["*"])
    source = inspect.getsource(module)
    # The call, not the name: an import line alone would satisfy a bare
    # substring check and did, the first time this was written.
    assert "rebuild_page_tree(" in source, (
        f"{description} ({module_name}) writes pages without rebuilding the tree, "
        "so the pages it touches lose their place"
    )


def test_the_writer_list_is_not_silently_empty():
    """Guards the parametrisation itself."""
    assert len(_PERSIST_PATHS) + len(_DEFERRED_PATHS) >= 6


@pytest.mark.parametrize("module_name,description,caller_name", _DEFERRED_PATHS)
def test_the_template_update_defers_to_the_persist_that_follows_it(
    module_name: str, description: str, caller_name: str
):
    """A deferred writer is only safe while its caller still rebuilds after it.

    Two things have to hold, and both are checked: the writer really does not
    rebuild (otherwise the exemption is stale and the work is being done twice
    again), and the caller invokes it *before* the persist that does rebuild.
    Reordering those two calls, or dropping the second, silently leaves every
    re-rendered page unplaced — exactly the failure this module exists for.
    """
    writer = inspect.getsource(__import__(module_name, fromlist=["*"]))
    assert "rebuild_page_tree(" not in writer, (
        f"{description} ({module_name}) rebuilds the tree after all, so it no "
        "longer belongs in the deferred list — move it back to _PERSIST_PATHS"
    )

    caller = inspect.getsource(__import__(caller_name, fromlist=["*"]))
    writes = caller.index("persist_deterministic_pages(\n")
    rebuilds = caller.index("_persist_index_only_update(\n")
    assert writes < rebuilds, (
        f"{caller_name} no longer runs the rebuilding persist after "
        f"{description}, so its pages never get placed"
    )


def test_the_scoped_path_sweeps_superseded_rows():
    """Backlog B19, wired the same way and asserted the same way.

    A page whose members change is rewritten under a new id, and until this
    call existed the old row stayed behind as a duplicate on every
    ``repowise update``. That the sweep is *correct* is tested elsewhere; this
    asserts it is reached, because a function that is right and never called
    looks exactly like a function that was not needed.
    """
    module = __import__("repowise.core.pipeline.scoped_generation", fromlist=["*"])
    source = inspect.getsource(module)
    assert "sweep_superseded_generated_pages(\n" in source, (
        "a scoped regeneration writes pages without retiring the rows they "
        "supersede, so a membership change strands the old page as a duplicate"
    )


def test_the_scoped_sweep_runs_before_the_tree_is_rebuilt():
    """Order matters: a retired row must not be handed a place in the tree."""
    module = __import__("repowise.core.pipeline.scoped_generation", fromlist=["*"])
    source = inspect.getsource(module)
    sweep = source.index("sweep_superseded_generated_pages(\n")
    rebuild = source.index("await rebuild_page_tree(")
    assert sweep < rebuild
