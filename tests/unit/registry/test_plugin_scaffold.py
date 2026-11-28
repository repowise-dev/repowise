"""``repowise plugins new``: the generated package loads, passes the contract checks and matches PLUGINS_REFERENCE.md."""

from __future__ import annotations

import importlib.metadata
import json
import re
import sys
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.plugins_cmd import plugins_command
from repowise.cli.plugin_scaffold import SEAMS, render_module
from repowise.core.plugin_testing import check_plugin
from repowise.core.registry import plugins
from repowise.core.registry.plugins import PLUGIN_API_VERSION, seams
from tests.unit.settings.plugin_double import _restore, _tables

_DOCS = Path(__file__).resolve().parents[3] / "docs" / "reference" / "PLUGINS_REFERENCE.md"
_FILES = [
    "pyproject.toml",
    "src/repowise_{pkg}/__init__.py",
    "tests/test_plugin.py",
    "README.md",
    "AGENTS.md",
]


@pytest.fixture
def isolated(monkeypatch):
    """No installed plugins; seam implementations, settings and imported plugin modules put back after."""
    monkeypatch.delenv(plugins.ENV_VAR, raising=False)
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: [])
    from repowise.cli.commands.plugins_cmd import _import_builtin_seams

    _import_builtin_seams()
    saved_settings = _tables()
    saved_seams = {n: (dict(r._impls), dict(r._owners), r._chosen) for n, r in seams().items()}
    modules = set(sys.modules)
    plugins.reset()
    yield
    plugins.reset()
    for name, (impls, owners, chosen) in saved_seams.items():
        registry = seams()[name]
        registry._impls.clear()
        registry._impls.update(impls)
        registry._owners.clear()
        registry._owners.update(owners)
        registry._chosen = chosen
    _restore(saved_settings)
    for name in set(sys.modules) - modules:
        if name.startswith("repowise_"):
            del sys.modules[name]


def _new(*args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(plugins_command, ["new", *args, "--format", "json"])
    return result.exit_code, json.loads(result.output)


def test_a_scaffolded_issue_source_loads_and_passes_plugins_check(tmp_path, monkeypatch, isolated):
    dest = tmp_path / "pkg"
    code, out = _new("issue-source", "--name", "acme", "--dir", str(dest))

    assert code == 0
    assert out["files"] == [f.format(pkg="acme") for f in _FILES]
    assert out["next_commands"] == [
        f"pip install -e {dest}",
        "repowise plugins check",
        "repowise sources sync --source acme",
    ]
    # Loaded the way a user tries it before installing: REPOWISE_PLUGINS, no pip.
    monkeypatch.syspath_prepend(str(dest / "src"))
    monkeypatch.setenv(plugins.ENV_VAR, "repowise_acme:register")
    result = CliRunner().invoke(plugins_command, ["check", "--format", "json"])

    report = json.loads(result.output)
    assert result.exit_code == 0, report
    assert report["ok"] is True
    assert {(c["kind"], c["name"], c["status"]) for c in report["checks"]} == {
        ("issue-source", "acme", "pass"),
        ("setting", "plugins.acme.url", "skipped"),
        ("setting", "REPOWISE_PLUGIN_ACME_TOKEN", "skipped"),
    }


@pytest.mark.parametrize("seam", sorted(SEAMS))
def test_every_template_passes_the_contract_checks_and_its_own_test(
    seam, tmp_path, monkeypatch, isolated
):
    dest = tmp_path / "pkg"
    code, _out = _new(seam, "--dir", str(dest))
    assert code == 0
    name = SEAMS[seam].default_name
    monkeypatch.syspath_prepend(str(dest / "src"))

    [check] = [c for c in check_plugin(f"repowise_{name}:register") if c.kind == seam]
    assert (check.name, check.status, check.failures) == (name, "pass", [])
    pyproject = tomllib.loads((dest / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    assert project["keywords"] == ["repowise-plugin"]
    assert project["entry-points"]["repowise.plugins"] == {name: f"repowise_{name}:register"}
    assert re.fullmatch(r"repowise>=\d+\.\d+", project["dependencies"][0])
    module = (dest / "src" / f"repowise_{name}" / "__init__.py").read_text(encoding="utf-8")
    assert module.endswith(f"MIN_PLUGIN_API_VERSION = {PLUGIN_API_VERSION}\n")
    assert f'assert_plugin_passes("repowise_{name}:register")' in (
        dest / "tests" / "test_plugin.py"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("seam", sorted(SEAMS))
def test_each_template_is_the_example_in_plugins_md(seam):
    blocks = re.findall(r"```python\n(.*?)```", _DOCS.read_text(encoding="utf-8"), re.S)
    rendered = render_module(seam, SEAMS[seam].default_name)
    assert rendered in blocks, (
        f"The {seam} example in docs/reference/PLUGINS_REFERENCE.md and the `repowise plugins new` "
        "template in repowise/cli/plugin_scaffold.py differ: change them together."
    )


@pytest.mark.parametrize(
    ("seam", "check"),
    [
        ("issue-source", "repowise changes show <sha>"),
        ("issue-source", "repowise context <one of those files> --format json"),
        ("evidence-source", "repowise evidence status --format json"),
    ],
)
def test_agents_md_runs_update_then_checks_the_effect_for_a_fetching_seam(seam, check, tmp_path):
    dest = tmp_path / "pkg"
    _new(seam, "--name", "pg", "--dir", str(dest))
    agents = (dest / "AGENTS.md").read_text(encoding="utf-8")
    steps = [
        'pip install -e ".[test]"',
        "repowise plugins check",
        "repowise config set plugins.pg.url",
        "export REPOWISE_PLUGIN_PG_TOKEN=",
        "repowise sources sync --source pg",
        "repowise update\n",
        check,
    ]
    positions = [agents.find(step) for step in steps]
    assert -1 not in positions, dict(zip(steps, positions, strict=True))
    assert positions == sorted(positions)
    done = agents.split("## Done when", 1)[1]
    assert "`repowise update` has run" in done
    assert check.split(" <", 1)[0] in done


def test_agents_md_carries_the_installed_contract_and_links_the_installed_version(tmp_path):
    import inspect

    from repowise.core import __version__
    from repowise.core.issues import sources

    dest = tmp_path / "pkg"
    _new("issue-source", "--name", "acme", "--dir", str(dest))
    agents = (dest / "AGENTS.md").read_text(encoding="utf-8")
    readme = (dest / "README.md").read_text(encoding="utf-8")

    for cls in (sources.IssueSource, sources.IssueRequest, sources.IssueLink):
        assert inspect.getsource(cls).strip() in agents
    for rule in (
        "`SourceUnavailable`",
        "`SourceRefused`",
        "def plugin_setting(",
        "ISSUE_TYPES = (",
    ):
        assert rule in agents
    # The docstring's reST reads as Markdown.
    assert ":class:" not in agents.split("From `", 1)[0]
    tag = f"/blob/v{__version__}/docs/reference/"
    assert f"{tag}PLUGINS_REFERENCE.md" in agents
    assert f"{tag}PLUGINS.md" in readme
    for text in (agents, readme):
        assert "/blob/main/" not in text and ".md#" not in text


def test_an_unknown_seam_is_refused_with_the_supported_list(tmp_path):
    code, out = _new("event-sink", "--dir", str(tmp_path / "pkg"))
    assert code == 1
    assert out["error"] == "unknown_seam"
    assert out["supported"] == sorted(SEAMS)
    assert not (tmp_path / "pkg").exists()


def test_a_bad_name_or_a_used_directory_writes_nothing(tmp_path):
    code, out = _new("issue-source", "--name", "Linear", "--dir", str(tmp_path / "a"))
    assert (code, out["error"]) == (1, "bad_name")
    assert not (tmp_path / "a").exists()

    code, out = _new("issue-source", "--name", "repowise-linear", "--dir", str(tmp_path / "c"))
    assert (code, out["name"]) == (0, "linear")

    used = tmp_path / "b"
    used.mkdir()
    (used / "keep.txt").write_text("mine", encoding="utf-8")
    code, out = _new("issue-source", "--dir", str(used))
    assert (code, out["error"]) == (1, "dir_not_empty")
    assert [p.name for p in used.iterdir()] == ["keep.txt"]


def test_plain_output_ends_with_the_next_three_commands(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(plugins_command, ["new", "authorizer"])
    assert result.exit_code == 0, result.output
    assert result.output.rstrip().splitlines()[-3:] == [
        "  pip install -e repowise-roles",
        "  repowise plugins check",
        "  repowise plugins",
    ]
