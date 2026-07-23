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
    ("repowise.cli.commands.update_cmd.deterministic", "the template update"),
    ("repowise.cli.commands.upgrade_flow", "the fast-to-full upgrade"),
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
    assert len(_PERSIST_PATHS) >= 6
