"""Helper utilities with an unused export for dead code detection tests."""


def unused_helper():
    """This function is exported but never imported anywhere."""
    return "unused"


def used_by_nobody_else():
    """Another unused helper."""
    return unused_helper()
