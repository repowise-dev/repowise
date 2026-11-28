"""The plugin developer kit: contract checks, ``repowise plugins check`` and the API version gate."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import textwrap
import uuid

import pytest
from click.testing import CliRunner

from repowise.cli.commands.plugins_cmd import _import_builtin_seams, plugins_command
from repowise.core.events.sinks import event_sinks
from repowise.core.plugin_testing import (
    CONTRACTS,
    assert_plugin_passes,
    check_implementation,
    check_plugin,
)
from repowise.core.registry import plugins
from repowise.core.report.model import Section


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv(plugins.ENV_VAR, raising=False)
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: [])
    impls, owners = dict(event_sinks._impls), dict(event_sinks._owners)
    plugins.reset()
    yield
    plugins.reset()
    event_sinks._impls.clear()
    event_sinks._impls.update(impls)
    event_sinks._owners.clear()
    event_sinks._owners.update(owners)


@pytest.fixture
def make_module(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))

    def make(body: str) -> str:
        name = f"rw_test_kit_{uuid.uuid4().hex[:8]}"
        (tmp_path / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")
        return name

    return make


_GOOD_SINK = """
    from repowise.core.events.sinks import event_sinks

    class Sink:
        async def deliver(self, events, *, repo_root):
            pass

    def register():
        event_sinks.register("kit-good", Sink())
"""

_SYNC_SINK = """
    from repowise.core.events.sinks import event_sinks

    class Sink:
        def deliver(self, events, *, repo_root):
            pass

    def register():
        event_sinks.register("kit-sync", Sink())
"""


def test_every_builtin_implementation_meets_its_contract():
    from repowise.core.registry.plugins import seams

    _import_builtin_seams()
    for seam in CONTRACTS:
        for name, impl in seams()[seam]._impls.items():
            assert check_implementation(seam, impl, name) == []


class _SyncSink:
    def deliver(self, events, *, repo_root):
        pass


class _NoRepoRoot:
    async def deliver(self, events):
        pass


@pytest.mark.parametrize(
    ("seam", "impl", "rule", "fix"),
    [
        ("event-sink", _SyncSink(), "deliver is not async", "make `deliver` an `async def`"),
        ("event-sink", object(), "has no method deliver", "define `async def deliver(self, events"),
        (
            "event-sink",
            _NoRepoRoot(),
            "deliver does not accept the arguments repowise passes",
            "give it the signature `async def deliver(self, events",
        ),
        (
            "report-section",
            object(),
            "is a object, not a Section",
            "repowise.core.report.model.Section(...)",
        ),
        (
            "report-renderer",
            lambda: "",
            "does not accept the 1 positional arguments",
            "1 positional",
        ),
        ("no-such-seam", object(), "repowise has no seam named", "event-sink"),
    ],
)
def test_a_broken_implementation_names_the_rule_and_the_fix(seam, impl, rule, fix):
    [failure] = check_implementation(seam, impl, "x")
    assert (failure.seam, failure.name) == (seam, "x")
    assert failure.rule.startswith(rule), failure.rule
    assert fix in failure.fix, failure.fix
    assert str(failure).startswith(f"{seam} x: {failure.rule}. Fix: ")


def test_seam_rules_past_the_shape_are_checked():
    class Source:
        name = "s"
        kinds = ("no_such_kind",)

        def fetch(self, request):
            return []

    class Extractor:
        extensions = frozenset({"pdf"})
        version = "1"

        def extract(self, path, data):
            return ""

    [kind] = check_implementation("evidence-source", Source())
    assert "unknown evidence kinds ['no_such_kind']" in kind.rule
    [ext] = check_implementation("document-extractors", Extractor())
    assert "invalid extension 'pdf'" in ext.rule


def test_a_dataclass_seam_is_checked_by_its_type():
    async def collect(ctx):
        return None

    assert check_implementation("report-section", Section("t", "s", collect)) == []


def test_check_plugin_passes_a_good_plugin_and_fails_a_broken_one(make_module):
    good = make_module(_GOOD_SINK)
    [check] = assert_plugin_passes(f"{good}:register")
    assert (check.kind, check.name, check.status) == ("event-sink", "kit-good", "pass")
    assert "kit-good" not in event_sinks.names  # the check puts every seam back

    bad = make_module(_SYNC_SINK)
    [check] = check_plugin(f"{bad}:register")
    assert check.status == "fail" and check.failures[0].rule == "deliver is not async"
    with pytest.raises(AssertionError, match="event-sink kit-sync: deliver is not async"):
        assert_plugin_passes(f"{bad}:register")


def test_check_plugin_reports_a_plugin_that_does_not_load(make_module):
    broken = make_module("def register():\n    raise RuntimeError('boom')\n")
    [check] = check_plugin(f"{broken}:register")
    assert (check.kind, check.status) == ("load", "fail")
    assert check.failures[0].fix == "RuntimeError: boom"


@pytest.mark.parametrize(
    ("declared", "status", "error"),
    [
        (None, "loaded", None),
        (plugins.PLUGIN_API_VERSION, "loaded", None),
        (
            plugins.PLUGIN_API_VERSION + 1,
            "failed",
            f"needs plugin API {plugins.PLUGIN_API_VERSION + 1}, this repowise has plugin API "
            f"{plugins.PLUGIN_API_VERSION}: upgrade repowise",
        ),
        ("2", "failed", "MIN_PLUGIN_API_VERSION = '2' is not a positive integer"),
    ],
)
def test_a_plugin_that_needs_a_newer_api_is_failed_and_the_rest_load(
    make_module, monkeypatch, declared, status, error
):
    line = "" if declared is None else f"MIN_PLUGIN_API_VERSION = {declared!r}\n"
    needy = make_module(line + "called = []\ndef register():\n    called.append(1)\n")
    other = make_module("def register():\n    pass\n")
    monkeypatch.setenv(plugins.ENV_VAR, f"{needy}:register,{other}:register")
    first, second = plugins.load_plugins()
    assert first.status == status
    if error is None:
        assert first.error is None
    else:
        assert error in first.error
        assert importlib.import_module(needy).called == []  # register() never ran
    assert second.status == "loaded"


def test_plugins_check_command_json_and_exit_code(make_module, monkeypatch):
    good, bad = make_module(_GOOD_SINK), make_module(_SYNC_SINK)
    monkeypatch.setenv(plugins.ENV_VAR, f"{good}:register")
    result = CliRunner().invoke(plugins_command, ["check", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert [(c["kind"], c["name"], c["status"]) for c in payload["checks"]] == [
        ("event-sink", "kit-good", "pass")
    ]

    plugins.reset()
    monkeypatch.setenv(plugins.ENV_VAR, f"{good}:register,{bad}:register")
    result = CliRunner().invoke(plugins_command, ["check", "--format", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    [failed] = [c for c in payload["checks"] if c["status"] == "fail"]
    assert failed["failures"] == [
        {
            "seam": "event-sink",
            "name": "kit-sync",
            "rule": "deliver is not async",
            "fix": "make `deliver` an `async def`",
        }
    ]

    plugins.reset()
    table = CliRunner().invoke(plugins_command, ["check"])
    assert table.exit_code == 1
    assert "deliver is not async. Fix: make `deliver` an `async def`" in table.output


def test_plugins_check_with_nothing_installed_passes():
    result = CliRunner().invoke(plugins_command, ["check"])
    assert result.exit_code == 0
    assert "nothing to check" in result.output
