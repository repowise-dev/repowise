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
            ("tests/conftest.py", "tests/test_a.py", "dynamic_uses", {"hint_source": "pytest_conftest"}),
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
        [("tests/test_a.py", "tests/support.py", "imports"), ("tests/support.py", "src/a.py", "imports")],
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


# ---------------------------------------------------------------------------
# reach_states: the negative claim, and the states beside it
# ---------------------------------------------------------------------------


def test_unreached_needs_the_whole_import_closure_to_miss() -> None:
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
            # Source imports top.py too, so the helper hop does not pass through it.
            ("src/alone.py", "src/top.py", "imports"),
        ],
    )
    maps = build_walk_maps(g)
    assert "src/leaf.py" not in reaching_by_tier(maps, ["src/leaf.py"])
    rows = reach_states(maps, ["src/leaf.py", "src/alone.py"])
    assert rows["src/leaf.py"].state == "reached"
    assert rows["src/leaf.py"].reached.entries == {"tests/test_top.py": frozenset({"src/top.py"})}
    assert rows["src/alone.py"].state == "unreached"


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
    content = "from typing import Protocol\n\nclass Store(Protocol):\n    def get(self) -> int: ...\n"
    rows = reach_states(maps, ["src/types.py"], contents={"src/types.py": content})
    assert rows["src/types.py"].state == "type_only"


def test_a_test_the_change_touches_is_evidence_for_the_file_it_names_or_imports() -> None:
    g = _graph(
        {"tests/test_other.py": True, "tests/test_imports.py": True, "src/a.py": False, "src/b.py": False},
        [("tests/test_imports.py", "src/b.py", "imports")],
    )
    maps = build_walk_maps(g)
    rows = reach_states(
        maps,
        ["src/a.py", "src/b.py", "src/new.py"],
        # test_a.py is new to the index; conftest.py is support, never evidence.
        changed_tests=["tests/test_a.py", "tests/test_imports.py", "tests/conftest.py", "tests/test_new.py"],
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
        {"src/lib.rs": False, "pkg/store.go": False, "pkg/store_test.go": True, "other/x.go": False},
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
        ("src/t.ts", "import type {A} from './a';\nexport type B = A;\nexport interface C {}\n", True),
        ("src/t.ts", "import {A} from './a';\ndeclare const v: A;\nexport type {Q} from './q';\n", True),
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
        ("src/types.py", "from typing import Protocol\nclass P(Protocol):\n    x: int\nLIMIT = 3\n", False),
        ("src/types.py", "def f():\n    return 1\n", False),
        ("src/types.py", "class P(Protocol\n", False),
        ("src/c.go", "package c\n", False),
    ],
)
def test_type_only_files(path: str, content: str, expected: bool) -> None:
    assert is_type_only(path, content) is expected


def test_a_test_file_is_never_type_only() -> None:
    assert not is_type_only("tests/test_types.py", "from typing import TypedDict\nclass R(TypedDict): ...\n")
    assert not is_type_only("src/types.py", "class R(TypedDict): ...\n", is_test=True)


def test_runnable_tests_stand_on_the_stored_flag_and_leave_support_out() -> None:
    assert is_runnable_test("tests/test_a.py")
    assert is_runnable_test("tests/test_a.py", is_test=True)
    assert not is_runnable_test("tests/test_a.py", is_test=False)
    assert not is_runnable_test("tests/conftest.py", is_test=True)
    assert not is_runnable_test("tests/fixtures/data.py", is_test=True)
