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



def _declared(uid: str, ecosystem: str, manifest: str) -> BuildUnit:
    root = manifest.rpartition("/")[0]
    return BuildUnit(
        id=uid, ecosystem=ecosystem, name=uid, root=root, manifest=manifest, source="t"
    )


def test_build_file_unit_passes_other_code_languages_up():
    # A unit declared in a build file is rooted at no package: at its root it
    # does not own files in another code language, which go on to the deepest
    # root holding a unit that may own them.
    cmake = _declared("cmake:core", "cmake", "src/CMakeLists.txt")
    paths = [
        "src/CMakeLists.txt", "src/a.cpp", "src/b.c", "src/inc/A.H", "src/gen.py", "src/x.rb",
        "src/cmake/flags.cmake", "src/README.md", "src/Makefile", "src/Lib.hs",
    ]
    index = BuildUnitIndex.from_units([cmake, _unit("top", "")], paths)
    assert index.file_owners == {
        # Its own build file, its languages, and files in no code language.
        "src/CMakeLists.txt": ("cmake:core",),
        "src/README.md": ("cmake:core",),
        "src/a.cpp": ("cmake:core",),
        "src/b.c": ("cmake:core",),
        "src/cmake/flags.cmake": ("cmake:core",),
        "src/inc/A.H": ("cmake:core",),
        # Other code languages, also those with no parser (Haskell, a
        # Makefile, which the registry counts as code).
        "src/Lib.hs": ("top",),
        "src/Makefile": ("top",),
        "src/gen.py": ("top",),
        "src/x.rb": ("top",),
    }
    assert [u.id for u in index.units_owning("src/new.py")] == ["top"]
    # An explicit listing still wins, whatever the file's language.
    listed = BuildUnitIndex.from_units([cmake], ["src/gen.py"], {"src/gen.py": ["cmake:core"]})
    assert listed.file_owners == {"src/gen.py": ("cmake:core",)}



def test_build_file_unit_keeps_objective_c_files():
    # CMake and Bazel build Objective-C, so its sources stay with the target
    # instead of walking up to the package above it.
    cmake = _declared("cmake:core", "cmake", "src/CMakeLists.txt")
    index = BuildUnitIndex.from_units(
        [cmake, _unit("top", "")], ["src/view.m", "src/view.mm", "src/gen.py"]
    )
    assert index.file_owners == {
        "src/gen.py": ("top",),
        "src/view.m": ("cmake:core",),
        "src/view.mm": ("cmake:core",),
    }

def test_build_file_rule_follows_the_registry_not_the_ecosystem():
    # Any build file the registry lists gets the rule (a Bazel BUILD here);
    # a package manifest does not, so an unlisted Python file under a Delphi
    # project's root stays its own, as it always has.
    bazel = _declared("bazel://core", "bazel", "core/BUILD.bazel")
    pascal = _declared("pascal:App", "pascal", "app/App.dpr")
    index = BuildUnitIndex.from_units(
        [bazel, pascal, _unit("top", "")], ["core/x.py", "core/x.cc", "app/tool.py"]
    )
    assert index.file_owners == {
        "app/tool.py": ("pascal:App",),
        "core/x.cc": ("bazel://core",),
        "core/x.py": ("top",),
    }
