"""One module per ecosystem, each a ``units(ctx) -> Found`` function over that ecosystem's resolver index.

``Found.claims`` pairs each unit with its listed files: the repo-relative
paths the index attributes to it; an empty tuple leaves ownership to the
deepest unit root. ``Found.ruled_out`` names the manifests the adapter read
and judged to be no unit (a virtual workspace, a ``package.json`` outside the
declared workspace). A unit's own manifest and the ruled-out ones are the
only manifests an adapter decides; the manifest fallback may claim the root
of any other. ``deps`` may name ids that do not exist:
:func:`~repowise.core.build_units.collect.collect_build_units` keeps only the
ones it indexed.
"""

from typing import NamedTuple

from repowise.core.build_units.model import BuildUnit


class Found(NamedTuple):
    claims: list[tuple[BuildUnit, tuple[str, ...]]]
    ruled_out: frozenset[str] = frozenset()
