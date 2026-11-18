"""BuildUnitIndex ownership, lookups and ordering."""

from __future__ import annotations

from repowise.core.build_units import BuildUnit, BuildUnitIndex


def _unit(uid: str, root: str, deps: tuple[str, ...] = ()) -> BuildUnit:
    return BuildUnit(
        id=uid, ecosystem="manifest", name=uid, root=root, manifest=f"{root}/m", source="t", deps=deps
    )


UNITS = [
    _unit("b", "pkg/inner", deps=("a",)),
    _unit("a", "pkg"),
    _unit("top", ""),
]


def test_deepest_root_wins_and_root_unit_catches_the_rest():
    index = BuildUnitIndex.from_units(UNITS, ["pkg/inner/x.py", "pkg/y.py", "README.md"])
    assert index.file_owners == {
        "README.md": ("top",),
        "pkg/inner/x.py": ("b",),
        "pkg/y.py": ("a",),
    }


def test_explicit_listing_beats_root_and_allows_several_owners():
    index = BuildUnitIndex.from_units(UNITS, ["shared/z.cs"], {"shared/z.cs": ["b", "a"]})
    assert [u.id for u in index.units_owning("shared/z.cs")] == ["a", "b"]


def test_unseen_path_falls_back_to_deepest_root():
    index = BuildUnitIndex.from_units(UNITS, [])
    assert [u.id for u in index.units_owning("pkg/inner/new.py")] == ["b"]


def test_lookups_and_sorted_order():
    index = BuildUnitIndex.from_units(UNITS, ["pkg/y.py", "README.md"])
    assert list(index.units) == ["a", "b", "top"]
    assert list(index.file_owners) == ["README.md", "pkg/y.py"]
    assert index.unit("a").root == "pkg"
    assert index.unit("missing") is None
    assert [u.id for u in index.dependents("a")] == ["b"]
    assert index.dependents("top") == ()


def test_rebuilt_from_its_own_maps_compares_equal():
    index = BuildUnitIndex.from_units(UNITS, ["pkg/y.py"])
    again = BuildUnitIndex(units=dict(reversed(index.units.items())), file_owners=index.file_owners)
    assert again == index
    assert list(again.units) == list(index.units)
