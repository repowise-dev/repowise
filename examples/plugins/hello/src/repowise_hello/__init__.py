"""A minimal repowise plugin.

repowise calls :func:`register` once per process, from the ``repowise.plugins``
entry point in ``pyproject.toml`` (or from ``REPOWISE_PLUGINS=repowise_hello:register``).
Each registration below is a few lines against a public registry or seam.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import click

from repowise.core.events.sinks import event_sinks
from repowise.core.registry import mcp_tool_registry, register_command
from repowise.core.report.model import Section, check, report_sections


@click.command("hello")
@click.argument("name", default="world")
def hello_command(name: str) -> None:
    """Say hello from the repowise-hello plugin."""
    click.echo(f"hello, {name}")


async def hello_greet(name: str) -> dict[str, Any]:
    """Return a greeting. A minimal MCP tool from the repowise-hello plugin."""
    return {"greeting": f"hello, {name}"}


class StderrCountSink:
    """An event sink that prints how many events each delivery carries."""

    async def deliver(self, events: Sequence[Any], *, repo_root: Path) -> None:
        print(f"repowise-hello: {len(events)} event(s) for {repo_root}", file=sys.stderr)


async def collect_hello(ctx: Any) -> list[dict[str, Any]] | None:
    """One evidence item: how many files sit at the repository root."""
    count = sum(1 for p in Path(ctx.repo_root).iterdir() if p.is_file())
    return [
        check(
            "hello:root-files",
            reason=f"{count} file(s) at the repository root",
            method="Counts files directly under the repository root.",
            basis="measured",
            data={"count": count},
        )
    ]


def register() -> None:
    """Called once by repowise when the plugin loads."""
    register_command(hello_command)
    mcp_tool_registry.register(default=False)(hello_greet)
    event_sinks.register("hello-stderr", StderrCountSink())
    report_sections.register(
        "hello",
        Section(
            title="Hello",
            summary="Files at the repository root, from the repowise-hello plugin.",
            collect=collect_hello,
            order=900,
        ),
    )
