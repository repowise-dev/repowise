"""`repowise health` / `repowise dead-code` must honor the persisted submodule flags.

A repo indexed with ``init --include-submodules`` records
``include_submodules: true`` in state.json. The standalone analysis commands
rebuild the graph in-process; constructing FileTraverser / GraphBuilder
without the flags made them analyze a different (smaller) file set than was
indexed — submodule files silently vanished from health scores and dead-code
findings. Same class of bug as the update-path gap
(see test_update_include_submodules.py).
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from repowise.cli.commands.dead_code_cmd import dead_code_command
from repowise.cli.commands.health_cmd import health_command


def _init_repo_with_initialized_submodule(tmp_path, *, state: dict | None = None):
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

    if state is not None:
        repowise_dir = tmp_path / ".repowise"
        repowise_dir.mkdir()
        (repowise_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _health_metric_paths(tmp_path) -> set[str]:
    result = CliRunner().invoke(
        health_command, [str(tmp_path), "--format", "json", "--no-workspace"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output[result.output.index("{") :])
    return {m["file_path"] for m in payload["metrics"]}


def _dead_code_finding_paths(tmp_path) -> set[str]:
    result = CliRunner().invoke(
        dead_code_command, [str(tmp_path), "--format", "json", "--no-workspace"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output[result.output.index("[") :])
    return {f["file_path"] for f in payload}


def test_health_includes_submodule_files_when_flag_persisted(tmp_path):
    """Equivalence with init: a submodule-indexed repo must keep its
    submodule files in the health-scored graph."""
    _init_repo_with_initialized_submodule(tmp_path, state={"include_submodules": True})

    paths = _health_metric_paths(tmp_path)

    assert "a.py" in paths
    assert "libs/sub/mod.py" in paths


def test_health_drops_submodule_files_by_default(tmp_path):
    """Legacy behavior: missing state key means submodules stay excluded."""
    _init_repo_with_initialized_submodule(tmp_path, state={})

    paths = _health_metric_paths(tmp_path)

    assert "a.py" in paths
    assert "libs/sub/mod.py" not in paths


def test_dead_code_includes_submodule_files_when_flag_persisted(tmp_path):
    """Dead-code reachability must see the same file set that was indexed —
    both leaf modules have in_degree 0, so both should be reported."""
    _init_repo_with_initialized_submodule(tmp_path, state={"include_submodules": True})

    paths = _dead_code_finding_paths(tmp_path)

    assert "libs/sub/mod.py" in paths


def test_dead_code_drops_submodule_files_by_default(tmp_path):
    """Legacy behavior: missing state key means submodules stay excluded."""
    _init_repo_with_initialized_submodule(tmp_path, state={})

    paths = _dead_code_finding_paths(tmp_path)

    assert "libs/sub/mod.py" not in paths
