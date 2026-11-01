"""The reach walks over a graph held in memory: tiers, the typed states and their evidence."""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.analysis.test_reach import (
    ReachDistance,
    build_walk_maps,
    go_inline_tests,
    is_runnable_test,
    is_type_only,
    reach_states,
    reaching_by_tier,
    rust_has_inline_tests,
)
from repowise.core.analysis.test_reach.hubs import HUB_MIN_FAN_IN


def _graph(files: dict[str, bool], edges: list[tuple]) -> nx.MultiDiGraph:
    """File nodes (``{path: is_test}``) and edges ``(src, dst, type[, attrs])``."""
    g = nx.MultiDiGraph()
    for path, is_test in files.items():
        g.add_node(path, node_type="file", is_test=is_test)
    for src, dst, edge_type, *attrs in edges:
        if edge_type == "defines":
            g.add_node(dst, node_type="symbol")
        g.add_edge(src, dst, edge_type=edge_type, **(attrs[0] if attrs else {}))
    return g


def _call(test: str, source: str, *, origin: str | None = None) -> list[tuple]:
    attrs = {"resolution_origin": origin} if origin else {}
    return [
        (test, f"{test}::test_it", "defines"),
        (source, f"{source}::run", "defines"),
        (f"{test}::test_it", f"{source}::run", "calls", attrs),
    ]


# ---------------------------------------------------------------------------
# build_walk_maps
# ---------------------------------------------------------------------------


def test_maps_read_a_networkx_graph_and_its_node_link_dict_alike() -> None:
    g = _graph(
        {"tests/test_a.py": True, "src/a.py": False},
        [*_call("tests/test_a.py", "src/a.py"), ("tests/test_a.py", "src/a.py", "imports")],
    )
    for source in (g, nx.node_link_data(g), nx.node_link_data(g, edges="links")):
        maps = build_walk_maps(source)
        assert maps.test_files == {"tests/test_a.py"}
        assert maps.files == {"tests/test_a.py", "src/a.py"}
        assert maps.callers["src/a.py::run"] == ("tests/test_a.py::test_it",)
        assert maps.imports.importers["src/a.py"] == ("tests/test_a.py",)


def test_maps_drop_name_guessed_calls_and_the_legacy_conftest_hint() -> None:
    g = _graph(
        {"tests/test_a.py": True, "tests/conftest.py": True, "src/a.py": False},
        [
            *_call("tests/test_a.py", "src/a.py", origin="global_unique"),
            (
                "tests/conftest.py",
                "tests/test_a.py",
                "dynamic_uses",
                {"hint_source": "pytest_conftest"},
            ),
        ],
    )
    maps = build_walk_maps(g)
    assert "src/a.py::run" not in maps.callers
    assert "tests/test_a.py" not in maps.imports.importers
    assert build_walk_maps(None).files == frozenset()


# ---------------------------------------------------------------------------
# reaching_by_tier: call, then import, then the helper or barrel hop
# ---------------------------------------------------------------------------


def test_the_call_tier_answers_first_with_hops() -> None:
    g = _graph(
        {"tests/test_a.py": True, "tests/test_imp.py": True, "src/a.py": False},
        [*_call("tests/test_a.py", "src/a.py"), ("tests/test_imp.py", "src/a.py", "imports")],
    )
    found = reaching_by_tier(build_walk_maps(g), ["src/a.py"])
    assert found["src/a.py"].via == "call-graph"
    assert found["src/a.py"].tests == ["tests/test_a.py"]
    assert found["src/a.py"].reach == {"tests/test_a.py": ReachDistance(1, 1)}


def test_a_seed_moves_one_hop_per_level_however_many_seeds_share_the_walk() -> None:
    """a <- b <- c <- d <- test: b is 3 calls from the test, a is 4, past the depth.

    Walked together, b's symbol sits in the first level with a's, so a seed
    must not ride through it within that level and arrive a hop early.
    """
    chain = ["src/a.py", "src/b.py", "src/c.py", "src/d.py"]
    edges = [(p, f"{p}::f", "defines") for p in chain]
    edges += [(f"{chain[i + 1]}::f", f"{chain[i]}::f", "calls") for i in range(3)]
    edges += [("tests/test_d.py", "tests/test_d.py::t", "defines")]
    edges += [("tests/test_d.py::t", "src/d.py::f", "calls")]
    g = _graph({"tests/test_d.py": True, **dict.fromkeys(chain, False)}, edges)
    maps = build_walk_maps(g)
    together = reaching_by_tier(maps, ["src/a.py", "src/b.py"], import_depth=0, helpers=False)
    assert "src/a.py" not in together
    assert together["src/b.py"].reach == {"tests/test_d.py": ReachDistance(3, 1)}
    alone = reaching_by_tier(maps, ["src/b.py"], import_depth=0, helpers=False)
    assert alone == {"src/b.py": together["src/b.py"]}


def test_the_import_tier_answers_where_calls_are_silent() -> None:
    g = _graph(
        {"tests/test_imp.py": True, "src/a.py": False},
        [("tests/test_imp.py", "src/a.py", "imports")],
    )
    found = reaching_by_tier(build_walk_maps(g), ["src/a.py"])
    assert found["src/a.py"].via == "import-graph"
    assert found["src/a.py"].entries == {"tests/test_imp.py": frozenset({"src/a.py"})}


def test_the_helper_hop_passes_through_a_module_only_tests_import() -> None:
    g = _graph(
        {"tests/test_a.py": True, "tests/support.py": False, "src/a.py": False},
        [
            ("tests/test_a.py", "tests/support.py", "imports"),
            ("tests/support.py", "src/a.py", "imports"),
        ],
    )
    maps = build_walk_maps(g)
    assert "src/a.py" not in reaching_by_tier(maps, ["src/a.py"], helpers=False)
    assert reaching_by_tier(maps, ["src/a.py"])["src/a.py"].tests == ["tests/test_a.py"]


def _barrel_graph(n_tests: int) -> nx.MultiDiGraph:
    """``pkg/mod.py`` is imported only by its package barrel, which every test imports."""
    tests = [f"tests/test_{i:02}.py" for i in range(n_tests)]
    return _graph(
        {
            **dict.fromkeys(tests, True),
            "src/pkg/__init__.py": False,
            "src/pkg/mod.py": False,
            "src/app.py": False,
        },
        [
            ("src/pkg/__init__.py", "src/pkg/mod.py", "imports"),
            ("src/app.py", "src/pkg/__init__.py", "imports"),
            *[(t, "src/pkg/__init__.py", "imports") for t in tests],
        ],
    )


def test_a_module_behind_its_package_barrel_is_reached_not_unreached() -> None:
    """One import hop sees only the barrel; the barrel hop reaches every test."""
    maps = build_walk_maps(_barrel_graph(77))
    assert "src/pkg/mod.py" not in reaching_by_tier(maps, ["src/pkg/mod.py"], helpers=False)
    row = reach_states(maps, ["src/pkg/mod.py"])["src/pkg/mod.py"]
    assert row.state == "reached"
    assert row.reached.total == 77


def test_a_manifest_only_tests_read_is_not_test_support() -> None:
    """``package.json`` points at every entry of a package; reading it runs none of them."""
    g = _graph(
        {"pkg/__tests__/a.test.ts": True, "pkg/package.json": False, "pkg/src/git/index.ts": False},
        [
            ("pkg/__tests__/a.test.ts", "pkg/package.json", "imports"),
            ("pkg/package.json", "pkg/src/git/index.ts", "imports"),
        ],
    )
    target = "pkg/src/git/index.ts"
    assert target not in reaching_by_tier(build_walk_maps(g), [target])


# ---------------------------------------------------------------------------
# The package walk: a test loading the target through its own package
# ---------------------------------------------------------------------------


def test_a_test_importing_the_package_reaches_a_module_two_hops_behind_its_init() -> None:
    """``tool_why/__init__`` imports ``tool.py``, which imports ``workspace.py``."""
    g = _graph(
        {
            "tests/test_why.py": True,
            "src/tool_why/__init__.py": False,
            "src/tool_why/tool.py": False,
            "src/tool_why/workspace.py": False,
            "src/server.py": False,
        },
        [
            ("tests/test_why.py", "src/tool_why/__init__.py", "imports"),
            ("src/server.py", "src/tool_why/__init__.py", "imports"),
            ("src/tool_why/__init__.py", "src/tool_why/tool.py", "imports"),
            ("src/tool_why/tool.py", "src/tool_why/workspace.py", "imports"),
        ],
    )
    maps = build_walk_maps(g)
    target = "src/tool_why/workspace.py"
    assert target not in reaching_by_tier(maps, [target], helpers=False)
    found = reaching_by_tier(maps, [target])[target]
    assert found.tests == ["tests/test_why.py"]
    assert found.via == "import-graph"


def test_a_registry_beside_its_detectors_carries_the_tests_that_import_it() -> None:
    g = _graph(
        {
            "tests/test_markers.py": True,
            "src/biomarkers/registry.py": False,
            "src/biomarkers/lazy_load.py": False,
            "src/health/engine.py": False,
        },
        [
            ("tests/test_markers.py", "src/biomarkers/registry.py", "imports"),
            ("src/health/engine.py", "src/biomarkers/registry.py", "imports"),
            ("src/biomarkers/registry.py", "src/biomarkers/lazy_load.py", "imports"),
        ],
    )
    maps = build_walk_maps(g)
    target = "src/biomarkers/lazy_load.py"
    assert target not in reaching_by_tier(maps, [target], helpers=False)
    assert reaching_by_tier(maps, [target])[target].tests == ["tests/test_markers.py"]


def test_a_hub_inside_the_package_does_not_carry_its_importers_tests() -> None:
    """``models.py`` is imported by most of the repo; a test importing it says nothing of ``leaf``."""
    importers = [f"src/feature_{i:02}.py" for i in range(HUB_MIN_FAN_IN)]
    g = _graph(
        {
            "tests/test_feature.py": True,
            "src/pkg/models.py": False,
            "src/pkg/leaf.py": False,
            **dict.fromkeys(importers, False),
        },
        [
            ("tests/test_feature.py", "src/pkg/models.py", "imports"),
            *[(f, "src/pkg/models.py", "imports") for f in importers],
            ("src/pkg/models.py", "src/pkg/leaf.py", "imports"),
        ],
    )
    maps = build_walk_maps(g)
    assert "src/pkg/leaf.py" not in reaching_by_tier(maps, ["src/pkg/leaf.py"])
    # The hub's own test still reaches the hub.
    hub = reaching_by_tier(maps, ["src/pkg/models.py"])["src/pkg/models.py"]
    assert hub.tests == ["tests/test_feature.py"]


def test_a_component_no_test_renders_stays_unreached_beside_tested_siblings() -> None:
    """A tested sibling does not lend its tests to a file that imports it."""
    queue = "web/src/components/commits/commit-queue.tsx"
    view = "web/src/components/commits/commits-view.tsx"
    g = _graph(
        {
            "web/__tests__/commit-queue.test.tsx": True,
            queue: False,
            view: False,
            "web/src/app/commits/page.tsx": False,
        },
        [
            ("web/__tests__/commit-queue.test.tsx", queue, "imports"),
            ("web/src/app/commits/page.tsx", view, "imports"),
            (view, queue, "imports"),
        ],
    )
    maps = build_walk_maps(g)
    assert view not in reaching_by_tier(maps, [view])
    assert reach_states(maps, [view])[view].state == "unreached"


def test_the_package_walk_does_not_leave_the_package() -> None:
    g = _graph(
        {
            "tests/test_other.py": True,
            "src/other/x.py": False,
            "src/pkg/a.py": False,
            "src/server.py": False,
        },
        [
            ("tests/test_other.py", "src/other/x.py", "imports"),
            ("src/server.py", "src/other/x.py", "imports"),
            ("src/other/x.py", "src/pkg/a.py", "imports"),
        ],
    )
    assert "src/pkg/a.py" not in reaching_by_tier(build_walk_maps(g), ["src/pkg/a.py"])


# ---------------------------------------------------------------------------
# reach_states: the negative claim, and the states beside it
# ---------------------------------------------------------------------------


def test_unreached_needs_the_whole_import_closure_to_miss() -> None:
    # Each module in its own package, so the package walk does not answer either.
    g = _graph(
        {
            "tests/test_top.py": True,
            "src/a/top.py": False,
            "src/b/mid.py": False,
            "src/c/leaf.py": False,
            "src/d/alone.py": False,
        },
        [
            ("tests/test_top.py", "src/a/top.py", "imports"),
            ("src/a/top.py", "src/b/mid.py", "imports"),
            ("src/b/mid.py", "src/c/leaf.py", "imports"),
            # Source imports top.py too, so the helper hop does not pass through it.
            ("src/d/alone.py", "src/a/top.py", "imports"),
        ],
    )
    maps = build_walk_maps(g)
    assert "src/c/leaf.py" not in reaching_by_tier(maps, ["src/c/leaf.py"])
    rows = reach_states(maps, ["src/c/leaf.py", "src/d/alone.py"])
    assert rows["src/c/leaf.py"].state == "reached"
    assert rows["src/c/leaf.py"].reached.entries == {
        "tests/test_top.py": frozenset({"src/a/top.py"})
    }
    assert rows["src/d/alone.py"].state == "unreached"


def test_a_closure_route_through_another_target_still_reaches() -> None:
    g = _graph(
        {
            "tests/test_top.py": True,
            "src/top.py": False,
            "src/mid.py": False,
            "src/leaf.py": False,
            "src/alone.py": False,
        },
        [
            ("tests/test_top.py", "src/top.py", "imports"),
            ("src/top.py", "src/mid.py", "imports"),
            ("src/mid.py", "src/leaf.py", "imports"),
            ("src/alone.py", "src/top.py", "imports"),
        ],
    )
    maps = build_walk_maps(g)
    alone = reach_states(maps, ["src/leaf.py"])["src/leaf.py"].state
    together = reach_states(maps, ["src/mid.py", "src/leaf.py"])
    assert alone == together["src/leaf.py"].state == "reached"
    assert together["src/mid.py"].state == "reached"


def test_more_targets_than_one_closure_carries_are_walked_in_batches(monkeypatch) -> None:
    from repowise.core.analysis.test_reach import walk

    monkeypatch.setattr(walk, "CLOSURE_SEED_CAP", 1)
    g = _graph(
        {"tests/test_top.py": True, "src/top.py": False, "src/a.py": False, "src/b.py": False},
        [
            ("tests/test_top.py", "src/top.py", "imports"),
            ("src/top.py", "src/mid.py", "imports"),
            ("src/mid.py", "src/a.py", "imports"),
            ("src/mid.py", "src/b.py", "imports"),
            ("src/a.py", "src/top.py", "imports"),
        ],
    )
    rows = reach_states(build_walk_maps(g), ["src/a.py", "src/b.py"])
    assert {p: r.state for p, r in rows.items()} == {"src/a.py": "reached", "src/b.py": "reached"}


def test_a_file_the_index_lacks_is_new_and_an_index_without_tests_says_nothing() -> None:
    maps = build_walk_maps(_graph({"src/a.py": False}, []))
    rows = reach_states(maps, ["src/a.py", "src/added.py"])
    assert rows["src/a.py"].state == "no_signal"
    assert rows["src/added.py"].state == "new"


def test_a_types_only_file_is_type_only_whatever_reaches_it() -> None:
    maps = build_walk_maps(_graph({"src/types.py": False, "tests/test_x.py": True}, []))
    content = (
        "from typing import Protocol\n\nclass Store(Protocol):\n    def get(self) -> int: ...\n"
    )
    rows = reach_states(maps, ["src/types.py"], contents={"src/types.py": content})
    assert rows["src/types.py"].state == "type_only"


def test_a_test_the_change_touches_is_evidence_for_the_file_it_names_or_imports() -> None:
    g = _graph(
        {
            "tests/test_other.py": True,
            "tests/test_imports.py": True,
            "src/a.py": False,
            "src/b.py": False,
        },
        [("tests/test_imports.py", "src/b.py", "imports")],
    )
    maps = build_walk_maps(g)
    rows = reach_states(
        maps,
        ["src/a.py", "src/b.py", "src/new.py"],
        # test_a.py is new to the index; conftest.py is support, never evidence.
        changed_tests=[
            "tests/test_a.py",
            "tests/test_imports.py",
            "tests/conftest.py",
            "tests/test_new.py",
        ],
    )
    assert rows["src/a.py"].reached.via == "changed-test"
    assert rows["src/a.py"].reached.tests == ["tests/test_a.py"]
    assert rows["src/b.py"].reached.tests == ["tests/test_imports.py"]
    # A new file with a new test named for it is reached, not new.
    assert rows["src/new.py"].reached.tests == ["tests/test_new.py"]
    untouched = reach_states(maps, ["src/a.py"])["src/a.py"]
    assert untouched.state == "unreached"


def test_inline_tests_reach_their_own_file() -> None:
    g = _graph(
        {
            "src/lib.rs": False,
            "pkg/store.go": False,
            "pkg/store_test.go": True,
            "other/x.go": False,
        },
        [],
    )
    rows = reach_states(
        build_walk_maps(g),
        ["src/lib.rs", "pkg/store.go", "other/x.go"],
        contents={"src/lib.rs": "pub fn f() {}\n\n#[cfg(test)]\nmod tests {}\n"},
    )
    assert rows["src/lib.rs"].reached.via == "inline"
    assert rows["src/lib.rs"].reached.tests == ["src/lib.rs"]
    assert rows["pkg/store.go"].reached.tests == ["pkg/store_test.go"]
    assert rows["other/x.go"].state == "unreached"


# ---------------------------------------------------------------------------
# The pure helpers
# ---------------------------------------------------------------------------


def test_rust_inline_tests_are_read_from_the_attribute_not_a_comment() -> None:
    assert rust_has_inline_tests(b"#[cfg(test)]\nmod tests {}\n")
    assert rust_has_inline_tests("    #[ cfg( test ) ]\nmod t {}")
    assert not rust_has_inline_tests("// #[cfg(test)]\nfn f() {}\n")
    assert not rust_has_inline_tests('#[cfg(feature = "test")]\n')


def test_go_inline_tests_are_the_test_files_of_the_same_directory() -> None:
    tests = ["pkg/a_test.go", "pkg/sub/b_test.go", "pkg/c.go"]
    assert go_inline_tests("pkg/a.go", tests) == ["pkg/a_test.go"]
    assert go_inline_tests("pkg/a_test.go", tests) == []
    assert go_inline_tests("pkg/a.py", tests) == []


@pytest.mark.parametrize(
    ("path", "content", "expected"),
    [
        ("src/api.d.ts", "export const x: number;", True),
        (
            "src/t.ts",
            "import type {A} from './a';\nexport type B = A;\nexport interface C {}\n",
            True,
        ),
        (
            "src/t.ts",
            "import {A} from './a';\ndeclare const v: A;\nexport type {Q} from './q';\n",
            True,
        ),
        ("src/t.ts", "export type B = string;\nexport const k = 1;\n", False),
        ("src/t.ts", "import './polyfill';\nexport type B = string;\n", False),
        ("src/t.ts", "export enum E { a }\n", False),
        ("src/t.ts", "// nothing\n", False),
        ("src/t.tsx", "export interface Props { a: string }\n", True),
        (
            "src/types.py",
            '"""Types."""\nfrom typing import TypedDict, TypeAlias, TypeVar\n'
            "class Row(TypedDict):\n    a: int\n"
            "Rows = list[Row]\nId: TypeAlias = int\nT = TypeVar('T')\nMaybe = Row | None\n"
            "__all__ = ['Row']\n",
            True,
        ),
        (
            "src/types.py",
            "from typing import Protocol\nclass P(Protocol):\n    x: int\nLIMIT = 3\n",
            False,
        ),
        ("src/types.py", "def f():\n    return 1\n", False),
        ("src/types.py", "class P(Protocol\n", False),
        ("src/c.go", "package c\n", False),
    ],
)
def test_type_only_files(path: str, content: str, expected: bool) -> None:
    assert is_type_only(path, content) is expected


def test_a_test_file_is_never_type_only() -> None:
    assert not is_type_only(
        "tests/test_types.py", "from typing import TypedDict\nclass R(TypedDict): ...\n"
    )
    assert not is_type_only("src/types.py", "class R(TypedDict): ...\n", is_test=True)


def test_runnable_tests_stand_on_the_stored_flag_and_leave_support_out() -> None:
    assert is_runnable_test("tests/test_a.py")
    assert is_runnable_test("tests/test_a.py", is_test=True)
    assert not is_runnable_test("tests/test_a.py", is_test=False)
    assert not is_runnable_test("tests/conftest.py", is_test=True)
    assert not is_runnable_test("tests/fixtures/data.py", is_test=True)


# ---------------------------------------------------------------------------
# Listings: a test reaching only through a hub is tagged and listed last
# ---------------------------------------------------------------------------


def _hub_graph() -> nx.MultiDiGraph:
    """``src/t.py::f``: one direct test, one 3 calls out through a plain chain,
    one 3 calls out through ``src/hub.py``, and one with two such callers."""
    files = ["src/t.py", "src/x.py", "src/y.py", "src/hub.py"]
    tests = [
        "tests/test_a_hub.py",
        "tests/test_b_chain.py",
        "tests/test_c_two.py",
        "tests/test_t.py",
    ]
    edges = [(p, f"{p}::f", "defines") for p in files]
    edges += [(t, f"{t}::t", "defines") for t in tests]
    edges += [("tests/test_c_two.py", "tests/test_c_two.py::u", "defines")]
    calls = [
        ("tests/test_t.py::t", "src/t.py::f"),
        ("src/hub.py::f", "src/t.py::f"),
        ("src/x.py::f", "src/hub.py::f"),
        ("tests/test_a_hub.py::t", "src/x.py::f"),
        ("src/y.py::f", "src/t.py::f"),
        ("src/y.py::g", "src/y.py::f"),
        ("tests/test_b_chain.py::t", "src/y.py::g"),
        ("tests/test_c_two.py::t", "src/x.py::f"),
        ("tests/test_c_two.py::u", "src/x.py::f"),
    ]
    edges += [("src/y.py", "src/y.py::g", "defines")]
    edges += [(a, b, "calls") for a, b in calls]
    return _graph({**dict.fromkeys(files, False), **dict.fromkeys(tests, True)}, edges)


def test_a_test_reaching_only_through_a_hub_is_tagged_and_listed_last() -> None:
    maps = build_walk_maps(_hub_graph())
    plain = reaching_by_tier(maps, ["src/t.py"])["src/t.py"]
    assert plain.transitive == frozenset()
    listed = reaching_by_tier(maps, ["src/t.py"], hubs={"src/hub.py"})["src/t.py"]
    assert listed.transitive == {"tests/test_a_hub.py"}
    assert listed.tests == [
        "tests/test_t.py",
        # Through the hub too, but from two test functions: not tagged.
        "tests/test_c_two.py",
        "tests/test_b_chain.py",
        "tests/test_a_hub.py",
    ]
    # Tagged, not dropped: it still runs the target.
    assert listed.total == plain.total == 4


def test_a_call_hub_is_a_file_declaring_a_symbol_most_callers_call() -> None:
    from repowise.core.analysis.test_reach import call_hub_files

    fan_in = {"src/walk.py::Walker::run": 60, "src/leaf.py::f": 2, "tests/test_x.py::helper": 90}
    assert call_hub_files(fan_in, {"tests/test_x.py"}) == {"src/walk.py"}
    tests = [f"tests/test_{i:02}.py" for i in range(60)]
    edges = [(t, f"{t}::t", "defines") for t in tests]
    edges += [("src/walk.py", "src/walk.py::run", "defines")]
    edges += [(f"{t}::t", "src/walk.py::run", "calls") for t in tests]
    g = _graph({**dict.fromkeys(tests, True), "src/walk.py": False}, edges)
    assert build_walk_maps(g).hubs() == {"src/walk.py"}


def test_a_target_that_is_a_hub_still_carries_its_own_symbols() -> None:
    """Seeded at ``t.py::f``, a route in through ``t.py::g`` passes through the
    target's own file, which is a hub here; that is not a route through a hub."""
    g = _hub_graph()
    g.add_node("src/t.py::g", node_type="symbol")
    g.add_edge("src/t.py", "src/t.py::g", edge_type="defines")
    g.add_edge("src/t.py::g", "src/t.py::f", edge_type="calls")
    g.add_node("src/z.py", node_type="file", is_test=False)
    g.add_node("src/z.py::f", node_type="symbol")
    g.add_edge("src/z.py", "src/z.py::f", edge_type="defines")
    g.add_edge("src/z.py::f", "src/t.py::g", edge_type="calls")
    g.add_node("tests/test_d_own.py", node_type="file", is_test=True)
    g.add_node("tests/test_d_own.py::t", node_type="symbol")
    g.add_edge("tests/test_d_own.py", "tests/test_d_own.py::t", edge_type="defines")
    g.add_edge("tests/test_d_own.py::t", "src/z.py::f", edge_type="calls")
    maps = build_walk_maps(g)
    sid = "src/t.py::f"
    listed = reaching_by_tier(
        maps, [sid], symbol_seeds={sid: {sid}}, hubs={"src/hub.py", "src/t.py"}
    )[sid]
    assert listed.reach["tests/test_d_own.py"].hops == 3
    assert listed.transitive == {"tests/test_a_hub.py"}


def test_a_path_level_transitive_entry_ranks_a_test_id_last() -> None:
    from repowise.core.analysis.test_reach import rank_reaching

    tests = ["tests/test_a.py::t", "tests/test_b.py::t"]
    assert rank_reaching("src/x.py", tests, transitive={"tests/test_a.py"}) == [
        "tests/test_b.py::t",
        "tests/test_a.py::t",
    ]


def test_type_only_files_reads_only_what_it_must(tmp_path) -> None:
    from repowise.core.analysis.test_reach import read_under, type_only_files

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "t.ts").write_text("export interface A { a: string }\n", encoding="utf-8")
    (tmp_path / "src" / "c.ts").write_text("export const a = 1;\n", encoding="utf-8")
    asked: list[str] = []
    read = read_under(tmp_path)

    def counting(path: str):
        asked.append(path)
        return read(path)

    paths = ["src/t.ts", "src/c.ts", "src/api.d.ts", "src/gone.py", "src/x.go", "tests/a.d.ts"]
    assert type_only_files(paths, counting) == {"src/t.ts", "src/api.d.ts"}
    assert asked == ["src/t.ts", "src/c.ts", "src/gone.py"]
    assert read_under(None)("src/t.ts") is None


def test_read_under_stays_inside_the_checkout(tmp_path) -> None:
    from repowise.core.analysis.test_reach import read_under

    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "a.ts").write_text("export type A = string;\n", encoding="utf-8")
    (tmp_path / "secret.ts").write_text("export type S = string;\n", encoding="utf-8")
    read = read_under(tmp_path / "repo")
    assert read("a.ts") is not None
    assert read("../secret.ts") is None
    assert read(str(tmp_path / "secret.ts").replace("\\", "/")) is None
    assert read("missing.ts") is None


def test_helper_and_closure_rankings_identical() -> None:
    """Helper and closure walks rank tests consistently without recomputing edited sets."""
    g = _graph(
        {
            "tests/test_h1.py": True,
            "tests/test_h2.py": True,
            "tests/helper.py": False,
            "src/target.py": False,
            "tests/test_c1.py": True,
            "src/m1.py": False,
            "src/m2.py": False,
            "src/leaf.py": False,
        },
        [
            ("tests/test_h1.py", "tests/helper.py", "imports"),
            ("tests/test_h2.py", "tests/helper.py", "imports"),
            ("tests/helper.py", "src/target.py", "imports"),
            ("tests/test_c1.py", "src/m1.py", "imports"),
            ("src/m1.py", "src/m2.py", "imports"),
            ("src/m2.py", "src/leaf.py", "imports"),
        ],
    )
    maps = build_walk_maps(g)
    rows = reach_states(maps, ["src/target.py", "src/leaf.py"])
    assert rows["src/target.py"].state == "reached"
    assert rows["src/target.py"].reached.via == "import-graph"
    assert rows["src/target.py"].reached.tests == ["tests/test_h1.py", "tests/test_h2.py"]

    assert rows["src/leaf.py"].state == "reached"
    assert rows["src/leaf.py"].reached.via == "import-graph"
    assert rows["src/leaf.py"].reached.tests == ["tests/test_c1.py"]


def test_rank_free_reached_ness_returns_same_keys_as_reach_states() -> None:
    """Rank-free reach states returns the same reached keys without paying for ranking."""
    g = _graph(
        {
            "tests/test_a.py": True,
            "tests/test_b.py": True,
            "src/a.py": False,
            "src/b.py": False,
            "src/unreached.py": False,
        },
        [
            *_call("tests/test_a.py", "src/a.py"),
            ("tests/test_b.py", "src/b.py", "imports"),
        ],
    )
    maps = build_walk_maps(g)
    targets = ["src/a.py", "src/b.py", "src/unreached.py"]
    ranked = reach_states(maps, targets, rank=True)
    unranked = reach_states(maps, targets, rank=False)

    assert ranked.keys() == unranked.keys()
    for k in targets:
        assert ranked[k].state == unranked[k].state
    assert {k for k, r in ranked.items() if r.state == "reached"} == {
        k for k, r in unranked.items() if r.state == "reached"
    }
