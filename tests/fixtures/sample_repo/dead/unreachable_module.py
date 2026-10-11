"""This module is intentionally unreachable — used for dead code detection tests."""


def orphaned_function():
    """No one calls this."""
    return 42


class OrphanedClass:
    """No one imports this."""

    pass
