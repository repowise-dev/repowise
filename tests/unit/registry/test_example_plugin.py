"""The example plugin in examples/plugins/hello loads and registers what its README says."""

from __future__ import annotations

import asyncio
import importlib.metadata
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core import registry as registry_pkg
from repowise.core.events.sinks import event_sinks
from repowise.core.registry import cli_registry, mcp_tool_registry, plugins
from repowise.core.report.model import report_sections

EXAMPLE_SRC = Path(__file__).resolve().parents[3] / "examples" / "plugins" / "hello" / "src"

needs_api_surface = pytest.mark.skipif(
    not hasattr(registry_pkg, "PLUGIN_API_VERSION"),
    reason="cli-command and mcp-tool entries in a plugin's seams need PLUGIN_API_VERSION",
)


@pytest.fixture
def hello(monkeypatch):
    """Load only the example plugin, then put every registry back as it was."""
    monkeypatch.syspath_prepend(str(EXAMPLE_SRC))
    monkeypatch.setenv(plugins.ENV_VAR, "repowise_hello:register")
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: [])
    monkeypatch.setattr(event_sinks, "_impls", dict(event_sinks._impls))
    monkeypatch.setattr(event_sinks, "_chosen", event_sinks._chosen)
    monkeypatch.setattr(report_sections, "_impls", dict(report_sections._impls))
    monkeypatch.setattr(cli_registry, "_entries", list(cli_registry._entries))
    monkeypatch.setattr(mcp_tool_registry, "_entries", list(mcp_tool_registry._entries))
    sys.modules.pop("repowise_hello", None)
    plugins.reset()
    try:
        [record] = plugins.load_plugins()
        yield record
    finally:
        plugins.reset()
        sys.modules.pop("repowise_hello", None)


def test_example_plugin_loads_and_registers_its_seams(hello):
    assert (hello.name, hello.source, hello.status, hello.error) == (
        "repowise_hello:register",
        "env",
        "loaded",
        None,
    )
    assert "event-sink:hello-stderr" in hello.seams
    assert "report-section:hello" in hello.seams
    # The example adds a sink without taking over the default.
    assert event_sinks.default == "jsonl"


def test_example_plugin_registers_a_command_and_an_opt_in_tool(hello):
    import repowise_hello

    assert repowise_hello.hello_command in cli_registry.commands()
    [tool] = [e for e in mcp_tool_registry.entries() if e.name == "hello_greet"]
    assert (tool.default, tool.tier) == (False, "specialist")


def test_example_report_section_collects_a_valid_check(hello, tmp_path):
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    section = report_sections.get("hello")
    [item] = asyncio.run(section.collect(SimpleNamespace(repo_root=tmp_path)))
    assert (item["name"], item["score"], item["data"]) == ("hello:root-files", None, {"count": 1})


@needs_api_surface
def test_example_plugin_lists_its_command_and_tool(hello):
    assert "cli-command:hello" in hello.seams
    assert "mcp-tool:hello_greet" in hello.seams
