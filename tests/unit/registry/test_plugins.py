"""Plugin discovery, SeamRegistry selection, and the ``repowise plugins`` listing."""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
import textwrap
import uuid

import click
import pytest
from click.testing import CliRunner

from repowise.cli._instrumented_group import InstrumentedGroup
from repowise.core import registry as registry_pkg
from repowise.core.registry import CLIRegistry, plugins
from repowise.core.registry.plugins import SeamRegistry, load_plugins


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(plugins, "_SEAMS", {})
    monkeypatch.delenv(plugins.ENV_VAR, raising=False)
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: [])
    plugins.reset()
    yield
    plugins.reset()


@pytest.fixture
def make_module(tmp_path, monkeypatch):
    """Write a uniquely named module onto sys.path and return its name."""
    monkeypatch.syspath_prepend(str(tmp_path))

    def make(body: str) -> str:
        name = f"rw_test_plugin_{uuid.uuid4().hex[:8]}"
        (tmp_path / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")
        return name

    return make


_REGISTER_ACME = """
    from repowise.core.registry.plugins import seams
    calls = []

    def register():
        calls.append(1)
        seams()["demo"].register("acme", "ACME", default=True)
"""


def _entry_points(monkeypatch, *pairs: tuple[str, str]) -> None:
    eps = [importlib.metadata.EntryPoint(n, v, plugins.ENTRY_POINT_GROUP) for n, v in pairs]
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: eps if kw.get("group") == plugins.ENTRY_POINT_GROUP else [],
    )


def test_entry_point_plugin_registers_into_a_seam(monkeypatch, make_module):
    seam = SeamRegistry("demo")
    seam.register("local", "LOCAL")
    mod = make_module(_REGISTER_ACME)
    _entry_points(monkeypatch, ("acme", f"{mod}:register"))

    [record] = load_plugins()
    assert (record.name, record.source, record.status) == ("acme", "entry point", "loaded")
    assert record.seams == ["demo:acme"]
    assert seam.get() == "ACME"


def test_env_var_specs_load_modules_and_callables(monkeypatch, make_module):
    plain = make_module("import sys\nsys.modules[__name__].imported = True\n")
    seam = SeamRegistry("demo")
    seam.register("local", "LOCAL")
    func = make_module(_REGISTER_ACME)
    monkeypatch.setenv(plugins.ENV_VAR, f" {plain} ,{func}:register,")

    records = load_plugins()
    assert [(r.target, r.source, r.status) for r in records] == [
        (plain, "env", "loaded"),
        (f"{func}:register", "env", "loaded"),
    ]
    assert sys.modules[plain].imported is True
    assert seam.get() == "ACME"


def test_broken_plugins_are_reported_not_raised(monkeypatch, make_module):
    raises = make_module("def register():\n    raise RuntimeError('boom')\n")
    exits = make_module("import sys\nsys.exit(3)\n")
    exits_on_call = make_module("import sys\ndef register():\n    sys.exit('nope')\n")
    good = make_module("loaded = True\n")
    monkeypatch.setenv(
        plugins.ENV_VAR,
        f"no_such_module_xyz,{raises}:register,{exits},{exits_on_call}:register,"
        f"{good}:missing_attr,{good}",
    )

    records = load_plugins()
    status = {r.target: (r.status, r.error) for r in records}
    assert status["no_such_module_xyz"][0] == "failed"
    assert "ModuleNotFoundError" in status["no_such_module_xyz"][1]
    assert status[f"{raises}:register"] == ("failed", "RuntimeError: boom")
    assert status[exits] == ("failed", "SystemExit: 3")
    assert status[f"{exits_on_call}:register"] == ("failed", "SystemExit: nope")
    assert status[f"{good}:missing_attr"][0] == "failed"
    assert status[good] == ("loaded", None)


def test_loading_is_idempotent_and_deduplicated(monkeypatch, make_module):
    SeamRegistry("demo")
    mod = make_module(_REGISTER_ACME)
    _entry_points(monkeypatch, ("acme", f"{mod}:register"))
    monkeypatch.setenv(plugins.ENV_VAR, f"{mod}:register")

    first = load_plugins()
    assert load_plugins() == first
    assert [(r.source, r.status) for r in first] == [("entry point", "loaded"), ("env", "skipped")]
    assert first[1].error == "duplicate of the entry point plugin"
    assert sys.modules[mod].calls == [1]


def test_a_plugin_may_resolve_a_seam_while_loading(monkeypatch, make_module):
    seam = SeamRegistry("demo")
    seam.register("local", "LOCAL")
    mod = make_module(
        """
        from repowise.core.registry.plugins import seams
        seen = []

        def register():
            seen.append(seams()["demo"].get())
        """
    )
    monkeypatch.setenv(plugins.ENV_VAR, f"{mod}:register")
    [record] = load_plugins()
    assert record.status == "loaded"
    assert sys.modules[mod].seen == ["LOCAL"]


def test_an_interrupt_mid_load_lets_a_later_call_retry(monkeypatch, make_module):
    mod = make_module("def register():\n    raise KeyboardInterrupt\n")
    monkeypatch.setenv(plugins.ENV_VAR, f"{mod}:register")
    with pytest.raises(KeyboardInterrupt):
        load_plugins()
    assert plugins._records is None


def test_local_default_wins_until_something_selects_another(monkeypatch):
    seam: SeamRegistry[str] = SeamRegistry("demo-seam")
    seam.register("local", "LOCAL")
    seam.register("other", "OTHER")
    assert seam.default == "local"
    assert seam.get() == "LOCAL"
    assert seam.get("other") == "OTHER"

    monkeypatch.setenv("REPOWISE_SEAM_DEMO_SEAM", " other ")
    assert seam.get() == "OTHER"
    monkeypatch.delenv("REPOWISE_SEAM_DEMO_SEAM")

    seam.register("chosen", "CHOSEN", default=True)
    assert seam.get() == "CHOSEN"
    assert seam.names == ["local", "other", "chosen"]


def test_seam_errors_are_loud():
    seam = SeamRegistry("demo")
    with pytest.raises(LookupError):
        seam.get()
    seam.register("local", "LOCAL")
    with pytest.raises(KeyError, match="no implementation 'missing'"):
        seam.get("missing")
    assert SeamRegistry("demo") is seam  # a reload reuses what plugins registered
    for bad in ("", "1st", "has space", "a.b"):
        with pytest.raises(ValueError):
            SeamRegistry(bad)


def test_a_discovered_plugin_adds_a_cli_command(monkeypatch, make_module):
    isolated = CLIRegistry()
    monkeypatch.setattr(registry_pkg, "cli_registry", isolated)
    mod = make_module(
        """
        import click
        from repowise.core import registry

        @click.command("acme-hello")
        def hello():
            click.echo("hi")

        registry.cli_registry.register(hello)
        """
    )
    monkeypatch.setenv(plugins.ENV_VAR, mod)

    plain = InstrumentedGroup("root")
    assert plain.get_command(click.Context(plain), "acme-hello") is None
    assert plugins._records is None  # a group that does not discover never loads

    root = InstrumentedGroup("root", discover_plugins=True)
    isolated.apply(root)
    ctx = click.Context(root)
    assert "acme-hello" in root.list_commands(ctx)
    assert root.get_command(ctx, "acme-hello").name == "acme-hello"


def test_a_plugin_cannot_take_a_builtin_name_even_after_help(monkeypatch, make_module):
    isolated = CLIRegistry()
    monkeypatch.setattr(registry_pkg, "cli_registry", isolated)
    mod = make_module(
        """
        import click
        from repowise.core import registry

        @click.command("expand")
        def impostor():
            click.echo("impostor")

        registry.cli_registry.register(impostor)
        """
    )
    monkeypatch.setenv(plugins.ENV_VAR, mod)
    root = InstrumentedGroup("root", discover_plugins=True)
    root.add_lazy_command("expand", "repowise.cli.commands.expand_cmd:expand_command")

    assert CliRunner().invoke(root, ["--help"]).exit_code == 0  # the listing loads plugins
    assert plugins._records is not None
    resolved = root.get_command(click.Context(root), "expand")
    assert resolved is not sys.modules[mod].impostor


def test_dotenv_never_sets_plugin_or_seam_selection(tmp_path, monkeypatch, capsys):
    from repowise.cli.ui import env_persistence

    monkeypatch.setattr(env_persistence, "_warned_plugin_keys", False)
    monkeypatch.delenv("REPOWISE_SEAM_AUTHENTICATOR", raising=False)
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / ".env").write_text(
        "REPOWISE_PLUGINS=evil_module:run\n"
        "export REPOWISE_SEAM_AUTHENTICATOR=evil\n"
        "RW_TEST_DOTENV_KEY=kept\n",
        encoding="utf-8",
    )
    try:
        env_persistence.load_dotenv(tmp_path)
        assert plugins.ENV_VAR not in os.environ
        assert "REPOWISE_SEAM_AUTHENTICATOR" not in os.environ
        assert os.environ["RW_TEST_DOTENV_KEY"] == "kept"
    finally:
        os.environ.pop("RW_TEST_DOTENV_KEY", None)
    assert capsys.readouterr().err.count("ignoring") == 1


def test_plugins_command_json_and_table(monkeypatch, make_module):
    from repowise.cli.commands.plugins_cmd import plugins_command

    seam = SeamRegistry("demo")
    seam.register("local", "LOCAL")
    good = make_module(_REGISTER_ACME)
    monkeypatch.setenv(plugins.ENV_VAR, f"{good}:register,no_such_module_xyz")

    result = CliRunner().invoke(plugins_command, ["--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [(p["name"], p["status"], p["seams"]) for p in payload["plugins"]] == [
        (f"{good}:register", "loaded", ["demo:acme"]),
        ("no_such_module_xyz", "failed", []),
    ]
    assert payload["seams"] == {"demo": {"default": "acme", "implementations": ["local", "acme"]}}

    table = CliRunner().invoke(plugins_command, [])
    assert table.exit_code == 0, table.output
    assert "loaded" in table.output and "failed" in table.output
    assert "seam demo: acme" in table.output


def test_plugins_command_with_nothing_installed():
    from repowise.cli.commands.plugins_cmd import plugins_command

    result = CliRunner().invoke(plugins_command, [])
    assert result.exit_code == 0
    assert "No plugins loaded" in result.output


def test_dispatching_a_builtin_command_never_runs_discovery():
    """Discovery costs ~100 ms, so a known command must not pay it."""
    probe = (
        "from click.testing import CliRunner; "
        "from repowise.cli.main import cli; "
        "from repowise.core.registry import plugins; "
        "res = CliRunner().invoke(cli, ['expand', '--help']); "
        "assert res.exit_code == 0, res.output; "
        "print(plugins._records is None)"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip().splitlines()[-1] == "True"
