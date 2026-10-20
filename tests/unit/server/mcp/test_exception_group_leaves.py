"""A task-group crash names every fault in it, not just the first one.

anyio reports a child task's failure as an ``ExceptionGroup``, so the class the
layers above record was ``ExceptionGroup`` and nothing more: a missing
dependency, a permission problem and a closed pipe read identically. The unwrap
that fixed that kept ``exceptions[0]`` and dropped the siblings, which trades
one blind spot for a smaller one — 73 installs a month still report a bare group
class, and on a crash-looping server one fault masking another costs weeks.

These pin the flatten itself. The raise still carries exactly one exception,
because an exception can only be one thing; the leaf set travels beside it.
"""

from __future__ import annotations

from repowise.server.mcp_server._server import group_leaves


def test_a_plain_exception_is_its_own_leaf():
    exc = ValueError("boom")
    assert group_leaves(exc) == [exc]


def test_every_sibling_survives():
    """The whole point: `exceptions[0]` alone said one fault where there were two."""
    a, b = ModuleNotFoundError("no mcp"), PermissionError("locked")
    assert group_leaves(ExceptionGroup("g", [a, b])) == [a, b]


def test_a_nested_group_flattens_to_its_leaves():
    """anyio nests groups when a task group fails inside another one."""
    inner = ExceptionGroup("inner", [OSError("a"), OSError("b")])
    outer = ExceptionGroup("outer", [ValueError("v"), inner])
    assert [type(leaf).__name__ for leaf in group_leaves(outer)] == [
        "ValueError",
        "OSError",
        "OSError",
    ]


def test_an_empty_group_yields_itself():
    """A group is a worse answer than a leaf, and a better one than nothing.

    ``ExceptionGroup`` refuses an empty sequence, so this is reachable only
    through a subclass that overrides it — but the flatten must not return an
    empty list either way, since the caller raises ``leaves[0]``.
    """

    class _Empty(ExceptionGroup):
        @property
        def exceptions(self):
            return ()

    empty = _Empty("nothing", [ValueError("placeholder")])
    assert group_leaves(empty) == [empty]


def test_pathological_nesting_stops_at_the_depth_cap():
    """The cap exists to stop a cycle hanging, not because deep groups are real."""
    exc: BaseException = ValueError("core")
    for i in range(40):
        exc = ExceptionGroup(f"g{i}", [exc])
    leaves = group_leaves(exc)
    assert len(leaves) == 1
    assert isinstance(leaves[0], BaseExceptionGroup)
