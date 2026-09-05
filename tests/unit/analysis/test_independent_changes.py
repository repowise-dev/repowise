"""Partitioning a diff into changes the index does not connect.

Every test seeds a real wiki.db, because the whole question is what the stored
graph and git rows say: a fake session would only assert that the fake agrees
with itself.
"""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from repowise.core.analysis.independent_changes import independent_changes
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import (
    GitMetadata,
    GraphEdge,
    GraphNode,
    Repository,
    _new_uuid,
)

_REPO_ID = "repo1"


async def _seed(
    tmp_path,
    *,
    files: list[str],
    test_files: tuple[str, ...] = (),
    symbols: tuple[tuple[str, str], ...] = (),
    edges: tuple[tuple[str, str, str], ...] = (),
    co_change: dict[str, list[tuple[str, float]]] | None = None,
):
    """A session factory over a wiki.db holding the given nodes, edges and history."""
    db_path = tmp_path / "wiki.db"
    # NullPool so each session closes its connection inside the running loop:
    # a pooled one is finalised after the loop is gone and warns on the way out.
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}", poolclass=NullPool)
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Repository(id=_REPO_ID, name="repo", local_path=str(tmp_path)))
        for path in [*files, *test_files]:
            session.add(
                GraphNode(
                    id=_new_uuid(),
                    repository_id=_REPO_ID,
                    node_id=path,
                    node_type="file",
                    is_test=path in test_files,
                )
            )
        for symbol_id, owner in symbols:
            session.add(
                GraphNode(
                    id=_new_uuid(),
                    repository_id=_REPO_ID,
                    node_id=symbol_id,
                    node_type="symbol",
                    file_path=owner,
                )
            )
        for source, target, edge_type in edges:
            session.add(
                GraphEdge(
                    id=_new_uuid(),
                    repository_id=_REPO_ID,
                    source_node_id=source,
                    target_node_id=target,
                    edge_type=edge_type,
                )
            )
        for path, partners in (co_change or {}).items():
            session.add(
                GitMetadata(
                    id=_new_uuid(),
                    repository_id=_REPO_ID,
                    file_path=path,
                    co_change_partners_json=json.dumps(
                        [
                            {"file_path": partner, "co_change_count": weight, "frequency": 4}
                            for partner, weight in partners
                        ]
                    ),
                )
            )
        await session.commit()
    return factory


async def test_two_groups_with_nothing_between_them(tmp_path):
    """The headline case: one diff, two changes, biggest first."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "c.py", "x.py", "y.py"],
        edges=(
            ("a.py", "b.py", "imports"),
            ("b.py", "c.py", "imports"),
            ("x.py", "y.py", "imports"),
        ),
    )
    async with factory() as session:
        result = await independent_changes(
            session, _REPO_ID, ["a.py", "b.py", "c.py", "x.py", "y.py"]
        )

    assert result is not None
    assert [g.files for g in result.groups] == [("a.py", "b.py", "c.py"), ("x.py", "y.py")]
    assert result.ungrouped_files == ()


async def test_a_call_between_symbols_merges_their_files(tmp_path):
    """Edges are stored symbol to symbol; the grouping is over files."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "x.py", "other.py"],
        symbols=(("a.py::run", "a.py"), ("b.py::helper", "b.py")),
        edges=(("a.py::run", "b.py::helper", "calls"), ("x.py", "other.py", "imports")),
    )
    async with factory() as session:
        result = await independent_changes(session, _REPO_ID, ["a.py", "b.py", "x.py"])

    assert result is not None
    assert [g.files for g in result.groups] == [("a.py", "b.py"), ("x.py",)]


async def test_a_co_change_pair_merges_files_with_no_graph_edge(tmp_path):
    """History links files no import does: templates, configs, generated pairs."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "x.py"],
        co_change={"a.py": [("b.py", 4.0)], "x.py": [("other.py", 2.0)]},
    )
    async with factory() as session:
        result = await independent_changes(session, _REPO_ID, ["a.py", "b.py", "x.py"])

    assert result is not None
    assert [g.files for g in result.groups] == [("a.py", "b.py"), ("x.py",)]


async def test_containment_edges_link_nothing(tmp_path):
    """A file defining its own symbol is not a link between two changes."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "other.py"],
        symbols=(("a.py::run", "a.py"), ("b.py::helper", "b.py")),
        edges=(
            ("a.py", "a.py::run", "defines"),
            ("b.py", "b.py::helper", "defines"),
            ("a.py", "other.py", "imports"),
            ("b.py", "other.py", "imports"),
        ),
    )
    async with factory() as session:
        result = await independent_changes(session, _REPO_ID, ["a.py", "b.py"])

    assert result is not None
    assert [g.files for g in result.groups] == [("a.py",), ("b.py",)]


async def test_a_file_with_no_node_is_reported_and_never_grouped(tmp_path):
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "other.py"],
        edges=(("a.py", "other.py", "imports"), ("b.py", "other.py", "imports")),
    )
    async with factory() as session:
        result = await independent_changes(session, _REPO_ID, ["a.py", "b.py", "README.md"])

    assert result is not None
    assert result.ungrouped_files == ("README.md",)
    assert all("README.md" not in g.files for g in result.groups)


async def test_one_group_says_nothing(tmp_path):
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py"],
        edges=(("a.py", "b.py", "imports"),),
    )
    async with factory() as session:
        assert await independent_changes(session, _REPO_ID, ["a.py", "b.py"]) is None


async def test_fewer_than_two_indexed_files_says_nothing(tmp_path):
    factory = await _seed(tmp_path, files=["a.py"])
    async with factory() as session:
        assert await independent_changes(session, _REPO_ID, ["a.py", "README.md"]) is None
        assert await independent_changes(session, _REPO_ID, ["a.py"]) is None


async def test_an_edge_leaving_the_diff_does_not_count(tmp_path):
    """``other.py`` is indexed but unchanged, so it cannot join two groups."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "other.py"],
        edges=(("a.py", "other.py", "imports"), ("b.py", "other.py", "imports")),
    )
    async with factory() as session:
        result = await independent_changes(session, _REPO_ID, ["a.py", "b.py"])

    assert result is not None
    assert [g.files for g in result.groups] == [("a.py",), ("b.py",)]


async def test_bridging_files_name_the_file_holding_a_group_together(tmp_path):
    """a - b - c: take b out and the group splits. A pair has no such file."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "c.py", "x.py", "y.py"],
        edges=(
            ("a.py", "b.py", "imports"),
            ("b.py", "c.py", "imports"),
            ("x.py", "y.py", "imports"),
        ),
    )
    async with factory() as session:
        result = await independent_changes(
            session, _REPO_ID, ["a.py", "b.py", "c.py", "x.py", "y.py"]
        )

    assert result is not None
    chain, pair = result.groups
    assert chain.bridging_files == ("b.py",)
    assert pair.bridging_files == ()


async def test_to_dict_keys_and_summary(tmp_path):
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "c.py", "d.py", "e.py", "x.py", "y.py"],
        edges=(
            ("a.py", "b.py", "imports"),
            ("b.py", "c.py", "imports"),
            ("c.py", "d.py", "imports"),
            ("d.py", "e.py", "imports"),
            ("x.py", "y.py", "imports"),
        ),
    )
    async with factory() as session:
        result = await independent_changes(
            session,
            _REPO_ID,
            ["a.py", "b.py", "c.py", "d.py", "e.py", "x.py", "y.py", "notes.txt"],
        )

    assert result is not None
    payload = result.to_dict()
    assert set(payload) == {"count", "groups", "ungrouped_files", "basis", "summary"}
    assert set(payload["groups"][0]) == {"files", "bridging_files"}
    assert payload["count"] == 2
    assert payload["ungrouped_files"] == ["notes.txt"]
    assert result.commits_known is False
    assert payload["basis"] == (
        "no import, call, type reference or co-change pair in the index links "
        "one group to another"
    )
    assert payload["summary"] == (
        "This diff is 2 independent changes: 5 files and 2 files. "
        "1 changed file is left out of the grouping: docs, config, tests, "
        "files not in the index, or files it has never linked."
    )


async def test_the_basis_names_shared_commits_once_they_were_checked(tmp_path):
    """The basis states what was looked at, so it changes when the commits are."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "x.py", "y.py"],
        edges=(("a.py", "b.py", "imports"), ("x.py", "y.py", "imports")),
    )
    async with factory() as session:
        result = await independent_changes(
            session,
            _REPO_ID,
            ["a.py", "b.py", "x.py", "y.py"],
            commit_sets=[["a.py", "b.py"]],
        )

    assert result is not None
    assert result.commits_known is True
    payload = result.to_dict()
    assert set(payload) == {"count", "groups", "ungrouped_files", "basis", "summary"}
    assert payload["basis"] == (
        "no import, call, type reference, co-change pair or shared commit in the "
        "index and this range links one group to another"
    )


async def test_the_same_diff_gives_the_same_answer_twice(tmp_path):
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "c.py", "x.py", "y.py", "z.py"],
        symbols=(("x.py::run", "x.py"),),
        edges=(
            ("a.py", "b.py", "imports"),
            ("b.py", "c.py", "imports"),
            ("x.py::run", "y.py", "calls"),
        ),
        co_change={"y.py": [("z.py", 3.0)]},
    )
    paths = ["a.py", "b.py", "c.py", "x.py", "y.py", "z.py"]
    async with factory() as session:
        first = await independent_changes(session, _REPO_ID, paths)
        second = await independent_changes(session, _REPO_ID, list(reversed(paths)))

    assert first is not None and second is not None
    assert first.to_dict() == second.to_dict()
    assert [g.files for g in first.groups] == [("a.py", "b.py", "c.py"), ("x.py", "y.py", "z.py")]


async def test_a_page_no_resolver_can_link_is_not_a_second_change(tmp_path):
    """A lone markdown page beside one code change is not two changes."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "docs/guide.md"],
        edges=(("a.py", "b.py", "imports"),),
    )
    async with factory() as session:
        result = await independent_changes(session, _REPO_ID, ["a.py", "b.py", "docs/guide.md"])

    assert result is None


async def test_a_co_changed_page_is_still_left_out(tmp_path):
    """A page co-changes with whatever shipped beside it, which is not a link."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "x.py", "other.py", "docs/guide.md"],
        edges=(("a.py", "b.py", "imports"), ("x.py", "other.py", "imports")),
        co_change={"a.py": [("docs/guide.md", 5.0)]},
    )
    async with factory() as session:
        result = await independent_changes(
            session, _REPO_ID, ["a.py", "b.py", "x.py", "docs/guide.md"]
        )

    assert result is not None
    assert [g.files for g in result.groups] == [("a.py", "b.py"), ("x.py",)]
    assert result.ungrouped_files == ("docs/guide.md",)


async def test_a_manifest_alone_is_reported_rather_than_grouped(tmp_path):
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "x.py", "y.py", "pyproject.toml"],
        edges=(("a.py", "b.py", "imports"), ("x.py", "y.py", "imports")),
    )
    async with factory() as session:
        result = await independent_changes(
            session, _REPO_ID, ["a.py", "b.py", "x.py", "y.py", "pyproject.toml"]
        )

    assert result is not None
    assert result.to_dict()["count"] == 2
    assert result.ungrouped_files == ("pyproject.toml",)
    assert all("pyproject.toml" not in g.files for g in result.groups)


async def test_a_lone_test_file_is_not_a_second_change(tmp_path):
    """No index sees the tie from a test to the code it exercises.

    The test file carries a real import edge here, so it is its test-ness alone
    that keeps it out of a group.
    """
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "other.py"],
        test_files=("tests/test_a.py",),
        edges=(("a.py", "b.py", "imports"), ("tests/test_a.py", "other.py", "imports")),
    )
    async with factory() as session:
        result = await independent_changes(session, _REPO_ID, ["a.py", "b.py", "tests/test_a.py"])

    assert result is None


async def test_a_file_whose_only_edge_leaves_the_repository_is_not_a_change(tmp_path):
    """An import of a third-party package links this file to nothing here."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "c.py"],
        edges=(("a.py", "b.py", "imports"), ("c.py", "external:requests", "imports")),
    )
    async with factory() as session:
        result = await independent_changes(session, _REPO_ID, ["a.py", "b.py", "c.py"])

    assert result is None


async def test_a_test_file_neither_joins_a_group_nor_merges_two(tmp_path):
    """An integration test imports both sides; that is not one change."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "x.py", "y.py"],
        test_files=("tests/test_all.py",),
        edges=(
            ("a.py", "b.py", "imports"),
            ("x.py", "y.py", "imports"),
            ("tests/test_all.py", "a.py", "imports"),
            ("tests/test_all.py", "x.py", "imports"),
        ),
    )
    async with factory() as session:
        result = await independent_changes(
            session, _REPO_ID, ["a.py", "b.py", "x.py", "y.py", "tests/test_all.py"]
        )

    assert result is not None
    assert [g.files for g in result.groups] == [("a.py", "b.py"), ("x.py", "y.py")]
    assert result.ungrouped_files == ("tests/test_all.py",)


async def test_a_commit_set_covering_the_whole_diff_adds_nothing(tmp_path):
    """A range of one commit restates the diff, which is evidence about nothing."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "x.py", "y.py"],
        edges=(("a.py", "b.py", "imports"), ("x.py", "y.py", "imports")),
    )
    async with factory() as session:
        result = await independent_changes(
            session,
            _REPO_ID,
            ["a.py", "b.py", "x.py", "y.py"],
            commit_sets=[["a.py", "b.py", "x.py", "y.py"]],
        )

    assert result is not None
    assert [g.files for g in result.groups] == [("a.py", "b.py"), ("x.py", "y.py")]


async def test_one_shared_commit_groups_files_the_index_never_linked(tmp_path):
    """The author put them in one commit, which outranks a missing edge."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "x.py", "y.py"],
        edges=(("x.py", "y.py", "imports"),),
    )
    async with factory() as session:
        result = await independent_changes(
            session,
            _REPO_ID,
            ["a.py", "b.py", "x.py", "y.py"],
            commit_sets=[["a.py", "b.py"]],
        )

    assert result is not None
    assert [g.files for g in result.groups] == [("a.py", "b.py"), ("x.py", "y.py")]


async def test_a_commit_set_naming_files_outside_the_grouping_adds_nothing(tmp_path):
    """A commit that also touched a page or a file this range never changed."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "x.py", "y.py", "docs/guide.md"],
        edges=(("a.py", "b.py", "imports"), ("x.py", "y.py", "imports")),
    )
    async with factory() as session:
        result = await independent_changes(
            session,
            _REPO_ID,
            ["a.py", "b.py", "x.py", "y.py", "docs/guide.md"],
            commit_sets=[["a.py", "unrelated.py"], ["x.py", "docs/guide.md"]],
        )

    assert result is not None
    assert [g.files for g in result.groups] == [("a.py", "b.py"), ("x.py", "y.py")]
    assert result.ungrouped_files == ("docs/guide.md",)


async def test_a_shared_file_is_the_bridge_between_two_commits(tmp_path):
    """Two commits meeting on one file make one group that file holds together."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "shared.py", "b.py", "x.py", "y.py"],
        edges=(("x.py", "y.py", "imports"),),
    )
    async with factory() as session:
        result = await independent_changes(
            session,
            _REPO_ID,
            ["a.py", "shared.py", "b.py", "x.py", "y.py"],
            commit_sets=[["a.py", "shared.py"], ["shared.py", "b.py"]],
        )

    assert result is not None
    joined, pair = result.groups
    assert joined.files == ("a.py", "b.py", "shared.py")
    assert joined.bridging_files == ("shared.py",)
    assert pair.files == ("x.py", "y.py")


async def test_an_inbound_link_from_outside_the_diff_still_counts(tmp_path):
    """``c.py`` has edges, they just point the other way, so it is a real change."""
    factory = await _seed(
        tmp_path,
        files=["a.py", "b.py", "c.py", "other.py"],
        edges=(("a.py", "b.py", "imports"), ("other.py", "c.py", "imports")),
    )
    async with factory() as session:
        result = await independent_changes(session, _REPO_ID, ["a.py", "b.py", "c.py"])

    assert result is not None
    assert [g.files for g in result.groups] == [("a.py", "b.py"), ("c.py",)]
    assert result.ungrouped_files == ()
