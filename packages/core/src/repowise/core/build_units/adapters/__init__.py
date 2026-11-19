"""One module per ecosystem, each a ``units(ctx) -> Found`` function over that ecosystem's resolver index.

``Found.claims`` pairs each unit with its listed files: the repo-relative
paths the index attributes to it; an empty tuple leaves ownership to the
deepest unit root. ``Found.ruled_out`` names the manifests the adapter read
and judged to be no unit (a virtual workspace, a ``package.json`` outside the
declared workspace). A unit's own manifest and the ruled-out ones are the
only manifests an adapter decides; the manifest fallback may claim the root
of any other. ``deps`` may name ids that do not exist:
:func:`~repowise.core.build_units.collect.collect_build_units` keeps only the
ones it indexed. :func:`linked_unit_id` is how a path dependency names the
unit at a directory: the id that won that root, ``manifest:<root>`` when an
earlier root took the name, or nothing when the directory is no package.
"""

from collections.abc import Collection, Mapping
from typing import NamedTuple

from repowise.core.build_units.model import BuildUnit


class Found(NamedTuple):
    claims: list[tuple[BuildUnit, tuple[str, ...]]]
    ruled_out: frozenset[str] = frozenset()


def linked_unit_id(
    root: str, by_root: Mapping[str, str], dropped: Collection[str]
) -> str | None:
    """The unit a path dependency on *root* links.

    *by_root* maps each root that kept its name to that unit's id. A root in
    *dropped* lost the name to an earlier root and is the fallback unit
    ``manifest:<root>`` (``manifest:.`` at the repo root). A directory in
    neither holds no package, so the dependency links nothing.
    """
    if root in by_root:
        return by_root[root]
    if root in dropped:
        return f"manifest:{root or '.'}"
    return None
