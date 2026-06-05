"""`repowise update` must honor the persisted submodule flags.

A repo indexed with ``init --include-submodules`` records
``include_submodules: true`` in state.json; its incremental updates must
rebuild the graph with the same boundary semantics. ``_build_repo_graph``
previously constructed FileTraverser and GraphBuilder without the flags —
silently dropping submodule files (and their manifests) on every update.
Same class of bug as the ``git_tier`` gap (see test_update_git_tier.py).
"""

from __future__ import annotations

from repowise.cli.commands.update_cmd import _build_repo_graph, _rebuild_graph_and_git


def _init_repo_with_initialized_submodule(tmp_path):
    """A parent repo containing an *initialized* submodule (`.git` file)."""
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / ".gitmodules").write_text(
        '[submodule "libs/sub"]\n'
        "    path = libs/sub\n"
        "    url = https://github.com/example/sub.git\n"
    )
    sub = tmp_path / "libs" / "sub"
    sub.mkdir(parents=True)
    (sub / ".git").write_text("gitdir: ../../.git/modules/libs/sub\n")
    (sub / "mod.py").write_text("y = 2\n")
    repo.index.add(["a.py", ".gitmodules"])
    repo.index.commit("feat: add module a + submodule")
    repo.close()


def _graph_paths(tmp_path, **kwargs) -> set[str]:
    parsed_files, _source_map, _builder, _structure, _count = _build_repo_graph(
        tmp_path, [], **kwargs
    )
    return {p.file_info.path for p in parsed_files}


def test_update_graph_keeps_submodule_files_when_flag_set(tmp_path):
    """Equivalence with init: a submodule-indexed repo must keep its
    submodule files in the update-built graph."""
    _init_repo_with_initialized_submodule(tmp_path)

    paths = _graph_paths(tmp_path, include_submodules=True)

    assert "a.py" in paths
    assert "libs/sub/mod.py" in paths


def test_update_graph_drops_submodule_files_by_default(tmp_path):
    """Legacy behavior: missing state key means submodules stay excluded."""
    _init_repo_with_initialized_submodule(tmp_path)

    paths = _graph_paths(tmp_path)

    assert "a.py" in paths
    assert "libs/sub/mod.py" not in paths


def test_rebuild_threads_include_submodules(tmp_path):
    """The full incremental rebuild (graph + git re-index) honors the flag."""
    _init_repo_with_initialized_submodule(tmp_path)

    parsed_files, _sm, _gb, _rs, _fc, _gm = _rebuild_graph_and_git(
        tmp_path, [], {}, [], include_submodules=True
    )

    paths = {p.file_info.path for p in parsed_files}
    assert "libs/sub/mod.py" in paths
