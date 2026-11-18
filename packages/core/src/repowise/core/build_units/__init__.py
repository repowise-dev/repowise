"""Build units: the projects, crates, packages and modules a repo builds.

:func:`repowise.core.build_units.collect.collect_build_units` reads them from
the resolver indexes into a :class:`BuildUnitIndex`. The model is imported
here on its own so a reader of stored units does not load the resolvers.
"""

from repowise.core.build_units.model import BuildUnit, BuildUnitIndex

__all__ = ["BuildUnit", "BuildUnitIndex"]
