"""Plugin discovery, SeamRegistry selection, and the ``repowise plugins`` listing."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from repowise.cli._instrumented_group import InstrumentedGroup
from repowise.core import registry as registry_pkg
from repowise.core.providers.embedding import registry as embedders
from repowise.core.providers.llm import registry as llm_providers
from repowise.core.registry import (
    CLIRegistry,
    MCPToolRegistry,
    PipelineHookRegistry,
    plugins,
)
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

    def make(body: str, prefix: str = "rw_test_plugin") -> str:
        name = f"{prefix}_{uuid.uuid4().hex[:8]}"
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
    assert payload["api_version"] == plugins.PLUGIN_API_VERSION == registry_pkg.PLUGIN_API_VERSION
    assert [(p["name"], p["status"], p["seams"]) for p in payload["plugins"]] == [
        (f"{good}:register", "loaded", ["demo:acme"]),
        ("no_such_module_xyz", "failed", []),
    ]
    assert payload["seams"]["demo"] == {"default": "acme", "implementations": ["local", "acme"]}

    table = CliRunner().invoke(plugins_command, [])
    assert table.exit_code == 0, table.output
    assert "loaded" in table.output and "failed" in table.output
    assert "seam demo: acme" in table.output
    assert f"plugin API version {plugins.PLUGIN_API_VERSION}" in table.output


def test_plugins_command_with_nothing_installed():
    from repowise.cli.commands.plugins_cmd import plugins_command

    result = CliRunner().invoke(plugins_command, [])
    assert result.exit_code == 0
    assert "No plugins loaded" in result.output


def _fail_import_of(monkeypatch, target, error):
    from repowise.cli.commands import plugins_cmd

    real = importlib.import_module

    def broken(name, *args, **kwargs):
        if name == target:
            raise error
        return real(name, *args, **kwargs)

    monkeypatch.setattr(plugins_cmd.importlib, "import_module", broken)
    return plugins_cmd.plugins_command


@pytest.mark.parametrize(
    "error",
    [
        ImportError("cannot import name 'CloudEvent' from 'repowise.core.events.model'"),
        ModuleNotFoundError("No module named 'repowise.core.gone'", name="repowise.core.gone"),
    ],
)
def test_plugins_command_fails_when_a_builtin_module_is_broken(monkeypatch, error):
    command = _fail_import_of(monkeypatch, "repowise.core.events.sinks", error)
    result = CliRunner().invoke(command, ["--format", "json"])
    assert result.exit_code != 0
    assert result.exception is error


def test_plugins_command_fails_on_a_third_party_import_error_that_is_not_a_missing_package(
    monkeypatch,
):
    error = ImportError("cannot import name 'x' from 'fastapi'", name="fastapi")
    command = _fail_import_of(monkeypatch, "repowise.server.auth", error)
    result = CliRunner().invoke(command, ["--format", "json"])
    assert result.exit_code != 0
    assert result.exception is error


def test_plugins_command_skips_a_builtin_module_missing_a_third_party_package(monkeypatch):
    error = ModuleNotFoundError("No module named 'fastapi'", name="fastapi")
    command = _fail_import_of(monkeypatch, "repowise.server.auth", error)
    result = CliRunner().invoke(command, ["--format", "json"])
    assert result.exit_code == 0, result.output
    assert "seams" in json.loads(result.output)


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


def test_all_returns_every_implementation_after_loading_plugins(monkeypatch, make_module):
    seam = SeamRegistry("demo")
    seam.register("local", "LOCAL")
    monkeypatch.setenv(plugins.ENV_VAR, f"{make_module(_REGISTER_ACME)}:register")

    assert seam.all() == [("local", "LOCAL"), ("acme", "ACME")]
    assert plugins._records is not None


_REGISTER_EVERYTHING = """
    from repowise.core import registry
    from repowise.core.providers.embedding import registry as embedders
    from repowise.core.providers.llm import registry as llm_providers
    from repowise.core.registry.plugins import seams

    async def acme_tool() -> dict:
        return {}

    def register():
        seams()["demo"].register("acme", "ACME")
        registry.cli_registry.register_lazy("acme-hello", "acme.cli:hello")
        registry.mcp_tool_registry.register()(acme_tool)
        registry.pipeline_hooks.register("parse", lambda phase: None, when="post")
        llm_providers.register_provider("acme-llm", lambda **kw: "LLM")
        embedders.register_embedder("acme-embed", lambda **kw: "EMBED")
"""


@pytest.fixture
def isolated_registries(monkeypatch):
    for attr, cls in (
        ("cli_registry", CLIRegistry),
        ("mcp_tool_registry", MCPToolRegistry),
        ("pipeline_hooks", PipelineHookRegistry),
    ):
        monkeypatch.setattr(registry_pkg, attr, cls())
    monkeypatch.setattr(llm_providers, "_custom_providers", {})
    monkeypatch.setattr(embedders, "_custom_embedders", {})


def test_every_registration_kind_is_recorded_while_a_plugin_loads(
    monkeypatch, make_module, isolated_registries
):
    SeamRegistry("demo")
    monkeypatch.setenv(plugins.ENV_VAR, f"{make_module(_REGISTER_EVERYTHING)}:register")

    [record] = load_plugins()
    assert record.status == "loaded", record.error
    assert record.seams == [
        "demo:acme",
        "cli-command:acme-hello",
        "mcp-tool:acme_tool",
        "pipeline-hook:parse.post",
        "llm-provider:acme-llm",
        "embedder:acme-embed",
    ]
    registry_pkg.pipeline_hooks.register("graph", lambda phase: None)
    assert record.seams[-1] == "embedder:acme-embed"  # outside a load, nothing is recorded


def test_a_plugin_provider_resolves_by_name(monkeypatch, make_module, isolated_registries):
    monkeypatch.setenv(plugins.ENV_VAR, f"{make_module(_REGISTER_EVERYTHING)}:register")
    SeamRegistry("demo")

    assert llm_providers.get_provider("acme-llm") == "LLM"
    assert embedders.get_embedder("acme-embed") == "EMBED"
    assert "acme-llm" in llm_providers.list_providers()
    assert "acme-embed" in embedders.list_embedders()
    with pytest.raises(ValueError, match="acme-llm"):
        llm_providers.get_provider("no-such-llm")
    with pytest.raises(ValueError, match="acme-embed"):
        embedders.get_embedder("no-such-embedder")


def test_a_builtin_provider_never_runs_discovery(isolated_registries):
    llm_providers.get_provider("mock", with_rate_limiter=False)
    embedders.get_embedder("mock")
    assert plugins._records is None


_IMPORTS_A_BUILTIN = """
    from repowise.core.registry import pipeline_hooks
    from repowise.core.registry.plugins import seams

    def register():
        import repowise.core.events.sinks as sinks  # first import registers jsonl, stdout
        from repowise.core.report.model import Section

        sinks.event_sinks.register("acme", object())
        # An instance of a repowise class, still the plugin's own.
        seams()["report-section"].register("acme", Section(title="t", summary="s", collect=None))
        pipeline_hooks.register("parse", lambda phase: None)
"""


def test_builtins_imported_while_a_plugin_loads_are_not_its_own(
    monkeypatch, make_module, isolated_registries
):
    import repowise.core.events as events_pkg

    # Unload the built-in so the plugin's import runs its registrations again.
    monkeypatch.setattr(events_pkg, "sinks", importlib.import_module("repowise.core.events.sinks"))
    monkeypatch.delitem(sys.modules, "repowise.core.events.sinks")
    SeamRegistry("report-section")
    monkeypatch.setenv(plugins.ENV_VAR, f"{make_module(_IMPORTS_A_BUILTIN)}:register")

    [record] = load_plugins()
    assert record.status == "loaded", record.error
    assert record.seams == ["event-sink:acme", "report-section:acme", "pipeline-hook:parse.post"]
    assert plugins.seams()["event-sink"].names == ["jsonl", "stdout", "acme"]


def test_a_plugin_package_named_like_repowise_is_still_recorded(monkeypatch, make_module):
    SeamRegistry("demo")
    body = """
        from repowise.core.registry.plugins import seams
        seams()["demo"].register("hello", "HELLO")
    """
    monkeypatch.setenv(plugins.ENV_VAR, make_module(body, prefix="repowise_hello"))

    [record] = load_plugins()
    assert record.seams == ["demo:hello"]


_REPO = Path(__file__).resolve().parents[3]
_SEAM_DEFINITION = re.compile(r"=\s*SeamRegistry(?:\[[^\]]*\])?\(\s*[\"']([^\"']+)[\"']")


@pytest.fixture(scope="module")
def plugins_json() -> dict:
    """``repowise plugins --format json`` in a fresh process with no plugins."""
    probe = (
        "from click.testing import CliRunner; "
        "from repowise.cli.commands.plugins_cmd import plugins_command; "
        "print(CliRunner().invoke(plugins_command, ['--format', 'json']).output)"
    )
    env = {k: v for k, v in os.environ.items() if k != plugins.ENV_VAR}
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, env=env
    )
    return json.loads(out.stdout)


def test_plugins_command_lists_builtin_implementations(plugins_json):
    seams = plugins_json["seams"]
    assert {"bus-factor", "hotspots"} <= set(seams["report-section"]["implementations"])
    assert seams["event-sink"] == {"default": "jsonl", "implementations": ["jsonl", "stdout"]}


def test_plugins_command_imports_every_seam_defined_in_packages(plugins_json):
    defined = {
        m.group(1)
        for path in (_REPO / "packages").glob("*/src/**/*.py")
        for m in _SEAM_DEFINITION.finditer(path.read_text(encoding="utf-8"))
    }
    assert "report-section" in defined
    missing = defined - set(plugins_json["seams"])
    assert not missing, f"add the modules defining {sorted(missing)} to BUILTIN_SEAM_MODULES"
