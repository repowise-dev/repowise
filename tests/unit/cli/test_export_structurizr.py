"""Tests for ``repowise export --format structurizr``.

The model building is covered elsewhere; what matters here is the surface a
user actually touches — where the file lands, and whether the message tells
them what to do next when the default output is a fragment that looks
incomplete on its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands.export_structurizr import (
    FRAGMENT_FILENAME,
    STANDALONE_FILENAME,
    _counts,
    _resolve_output,
    export_structurizr,
)
from repowise.server.services.c4_builder.models import (
    C4Model,
    Component,
    Container,
    ExternalSystemView,
    Relation,
    System,
)


def _model(*, containers: bool = True) -> C4Model:
    core = Container(
        id="pkg:packages/core",
        name="core",
        path="packages/core",
        language="python",
        file_count=10,
        symbol_count=20,
    )
    return C4Model(
        system=System(id="sys:abc", name="demo"),
        people=[],
        containers=[core] if containers else [],
        components_by_container={
            core.id: [
                Component(
                    id="cmp:packages/core/ingestion",
                    name="ingestion",
                    path="packages/core/ingestion",
                    container_id=core.id,
                    file_count=4,
                    symbol_count=9,
                )
            ]
        }
        if containers
        else {},
        external_systems=[
            ExternalSystemView(
                id="ext:fastapi",
                name="fastapi",
                display_name="FastAPI",
                category="framework",
                ecosystem="pypi",
            )
        ]
        if containers
        else [],
        container_relations=[Relation(source_id=core.id, target_id="ext:fastapi", label="imports")]
        if containers
        else [],
        component_relations=[],
    )


@pytest.fixture
def patched(monkeypatch):
    """Swap the database build for a fixed model."""
    model = _model()

    async def fake_build(repo_path, *, include_components):
        return model

    monkeypatch.setattr("repowise.cli.commands.export_structurizr._build", fake_build)
    return model


# ---------------------------------------------------------------------------
# Output path
# ---------------------------------------------------------------------------


def test_output_defaults_to_the_repo_root(tmp_path: Path) -> None:
    assert _resolve_output(None, tmp_path, standalone=False) == tmp_path / FRAGMENT_FILENAME
    assert _resolve_output(None, tmp_path, standalone=True) == tmp_path / STANDALONE_FILENAME


def test_a_dsl_path_names_the_file_and_anything_else_is_a_directory(
    tmp_path: Path,
) -> None:
    named = _resolve_output(str(tmp_path / "arch.dsl"), tmp_path, standalone=False)
    assert named == tmp_path / "arch.dsl"

    directory = _resolve_output(str(tmp_path / "out"), tmp_path, standalone=False)
    assert directory == tmp_path / "out" / FRAGMENT_FILENAME


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_writes_a_fragment_by_default(tmp_path: Path, patched, capsys) -> None:
    code = export_structurizr(
        tmp_path,
        output=None,
        standalone=False,
        include_components=False,
        include_externals=True,
    )
    assert code == 0
    written = (tmp_path / FRAGMENT_FILENAME).read_text(encoding="utf-8")
    assert written.lstrip().startswith("#")
    assert "\nmodel {\n" in written
    assert "component " not in written


def test_standalone_writes_a_whole_workspace(tmp_path: Path, patched) -> None:
    code = export_structurizr(
        tmp_path,
        output=None,
        standalone=True,
        include_components=True,
        include_externals=True,
    )
    assert code == 0
    written = (tmp_path / STANDALONE_FILENAME).read_text(encoding="utf-8")
    assert "workspace " in written
    assert "views {" in written


def test_a_missing_parent_directory_is_created(tmp_path: Path, patched) -> None:
    target = tmp_path / "nested" / "deeper" / "arch.dsl"
    code = export_structurizr(
        tmp_path,
        output=str(target),
        standalone=False,
        include_components=False,
        include_externals=True,
    )
    assert code == 0
    assert target.is_file()


# ---------------------------------------------------------------------------
# What the user is told
# ---------------------------------------------------------------------------


def test_the_message_says_what_to_do_next(tmp_path: Path, patched, capsys) -> None:
    """A filename alone leaves the user staring at a file with no views."""
    export_structurizr(
        tmp_path,
        output=None,
        standalone=False,
        include_components=False,
        include_externals=True,
    )
    out = capsys.readouterr().out
    assert f"!include {FRAGMENT_FILENAME}" in out
    assert "workspace" in out
    # The system identifier is filled in, not left as a placeholder to edit.
    assert "systemContext sys_demo" in out
    assert "<system id>" not in out
    assert "structurizr/structurizr" in out


def test_the_standalone_hint_only_appears_when_there_is_no_workspace(
    tmp_path: Path, patched, capsys
) -> None:
    export_structurizr(
        tmp_path,
        output=None,
        standalone=False,
        include_components=False,
        include_externals=True,
    )
    assert "--standalone" in capsys.readouterr().out

    (tmp_path / STANDALONE_FILENAME).write_text("workspace {}", encoding="utf-8")
    export_structurizr(
        tmp_path,
        output=None,
        standalone=False,
        include_components=False,
        include_externals=True,
    )
    assert "--standalone" not in capsys.readouterr().out


def test_counts_are_reported_so_an_empty_index_is_obvious() -> None:
    summary = _counts(_model(), include_components=True, include_externals=True)
    assert "1 containers" in summary
    assert "1 components" in summary
    assert "1 external systems" in summary


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_an_unindexed_repo_names_the_command_that_fixes_it(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    async def no_model(repo_path, *, include_components):
        return None

    monkeypatch.setattr("repowise.cli.commands.export_structurizr._build", no_model)
    code = export_structurizr(
        tmp_path,
        output=None,
        standalone=False,
        include_components=False,
        include_externals=True,
    )
    assert code == 1
    assert "repowise init" in capsys.readouterr().out
    assert not (tmp_path / FRAGMENT_FILENAME).exists()


def test_an_empty_model_fails_instead_of_writing_a_useless_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A valid file with nothing in it reads as "this repo has no structure"."""
    empty = _model(containers=False)

    async def build_empty(repo_path, *, include_components):
        return empty

    monkeypatch.setattr("repowise.cli.commands.export_structurizr._build", build_empty)
    code = export_structurizr(
        tmp_path,
        output=None,
        standalone=False,
        include_components=False,
        include_externals=True,
    )
    assert code == 1
    assert "No containers" in capsys.readouterr().out
    assert not (tmp_path / FRAGMENT_FILENAME).exists()


def test_an_uppercase_dsl_suffix_is_still_a_file(tmp_path) -> None:
    """``--out MODEL.DSL`` used to create a directory of that name."""
    from repowise.cli.commands.export_structurizr import _resolve_output

    resolved = _resolve_output(str(tmp_path / "MODEL.DSL"), tmp_path, standalone=False)
    assert resolved.name == "MODEL.DSL"
    assert resolved.parent == tmp_path.resolve()


async def test_a_database_with_no_tables_reads_as_not_indexed(tmp_path) -> None:
    """The path a fresh clone with a stray .repowise/ dir takes.

    Every other CLI test stubs ``_build`` wholesale, so this branch — the one
    that turns a raw "no such table: repositories" traceback into the message
    naming the command that fixes it — had never run.
    """
    from repowise.cli.commands.export_structurizr import _build

    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    # An empty file where the index belongs: opens as SQLite, has no tables.
    (repo / ".repowise" / "wiki.db").touch()

    assert await _build(repo, include_components=False) is None
