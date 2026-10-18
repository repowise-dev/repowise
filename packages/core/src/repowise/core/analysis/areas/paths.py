"""What an area is: a directory at any depth, named by its repo-relative path.

The repository root is the area ``""``. A file belongs to every directory
above it, so ``a/b/c.py`` is in ``""``, ``a`` and ``a/b``. Pure string work,
shared by the rollup, the trend and the map's ``?area=`` filter so the three
can never disagree about which files an area holds.
"""

from __future__ import annotations

__all__ = [
    "ROOT_AREA",
    "ancestors",
    "area_at",
    "area_depth",
    "area_name",
    "area_parent",
    "in_area",
    "normalize_area",
]

ROOT_AREA = ""


def normalize_area(raw: str | None) -> str:
    """A caller's area path in canonical form: forward slashes, no edge slashes, no ``.``."""
    parts = [p for p in (raw or "").replace("\\", "/").split("/") if p and p != "."]
    return "/".join(parts)


def ancestors(file_path: str) -> list[str]:
    """Every area *file_path* belongs to, root first."""
    parts = file_path.split("/")[:-1]
    return [ROOT_AREA, *("/".join(parts[: i + 1]) for i in range(len(parts)))]


def area_at(file_path: str, depth: int) -> str:
    """The one area holding *file_path* at *depth*, or its own directory when
    that is shallower. Partitions files by level, as a grid of areas needs."""
    chain = ancestors(file_path)
    return chain[min(max(depth, 0), len(chain) - 1)]


def in_area(file_path: str, area: str) -> bool:
    return area == ROOT_AREA or file_path.startswith(area + "/")


def area_depth(area: str) -> int:
    """Segments in the path; the root is depth 0."""
    return 0 if area == ROOT_AREA else area.count("/") + 1


def area_name(area: str) -> str:
    return area.rsplit("/", 1)[-1]


def area_parent(area: str) -> str | None:
    """The enclosing area; the root has none."""
    if area == ROOT_AREA:
        return None
    return area.rsplit("/", 1)[0] if "/" in area else ROOT_AREA
