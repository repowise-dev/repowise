"""The live ``repowise dead-code`` analysis builds the graph the index builds.

It used to build a bare graph of its own, without the dynamic hints that
``init`` and ``update`` add, so a module named only by a lazy-registry string
read as unreachable live while the stored index called it reachable. It also
must not write into an indexed repo: the command only reads, and its table
output carries no log lines. ``health --recompute`` builds the same graph.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.commands.dead_code_cmd import dead_code_command

_FILES = {
    "src/app/__init__.py": "",
    "src/app/commands/__init__.py": "",
    "src/app/commands/status_cmd.py": "def status_command():\n    return 0\n",
    "src/app/main.py": (
        "def register(name, target):\n"
        "    return name, target\n"
        '_PKG = "app.commands"\n'
        '_COMMANDS = (("status", "status_cmd:status_command"),)\n'
        "for name, target in _COMMANDS:\n"
        '    register(name, f"{_PKG}.{target}")\n'
    ),
}


def _repo(tmp_path: Path) -> Path:
    for rel, text in _FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def _live(repo: Path) -> list[dict]:
    result = CliRunner().invoke(
        dead_code_command,
        [str(repo), "--no-workspace", "--min-confidence", "0.0", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output[result.output.index("[") :])


def test_a_module_named_by_a_lazy_registry_string_is_reachable_live(tmp_path: Path) -> None:
    findings = _live(_repo(tmp_path))

    unreachable = {f["file_path"] for f in findings if f["kind"] == "unreachable_file"}
    assert "src/app/commands/status_cmd.py" not in unreachable


def test_the_live_analysis_writes_nothing_into_an_indexed_repo(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "init"]):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True
        )
    # An index directory is what turns on the parse, centrality, episode and
    # git-window caches; an empty one stands in for an indexed repo.
    (repo / ".repowise").mkdir()

    _live(repo)

    assert list((repo / ".repowise").iterdir()) == []


def test_the_live_table_keeps_log_lines_off_stdout(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        dead_code_command, [str(_repo(tmp_path)), "--no-workspace", "--min-confidence", "0.0"]
    )

    assert result.exit_code == 0, result.output
    assert "Dead Code" in result.output
    for level in ("[debug", "[info", "[warning"):
        assert level not in result.output


def test_health_recompute_builds_the_same_graph(tmp_path: Path) -> None:
    from repowise.cli.commands.health_cmd.command import _parse_tree

    graph_builder, _parsed = _parse_tree(_repo(tmp_path))

    graph = graph_builder.graph()
    assert graph.has_edge("src/app/main.py", "src/app/commands/status_cmd.py")
