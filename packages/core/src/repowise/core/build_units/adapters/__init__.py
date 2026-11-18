"""One module per ecosystem, each a ``units(ctx)`` function over that ecosystem's resolver index.

``units`` returns ``(unit, listed files)`` pairs. Listed files are the
repo-relative paths the index attributes to the unit; an empty tuple leaves
ownership to the deepest unit root. ``deps`` may name ids that do not exist:
:func:`~repowise.core.build_units.collect.collect_build_units` keeps only the
ones it indexed.
"""
