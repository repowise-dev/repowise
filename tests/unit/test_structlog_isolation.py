"""Machine-output log silencing is limited to its command's lifetime."""

from __future__ import annotations

import structlog
from structlog.testing import capture_logs

from repowise.cli.helpers import silence_logs_for_machine_output


def test_machine_output_silences_logs_only_within_its_scope() -> None:
    structlog.configure(cache_logger_on_first_use=True)

    with silence_logs_for_machine_output():
        with capture_logs() as logs:
            structlog.get_logger(__name__).warning("silenced_here")
        assert logs == []

    assert structlog.get_config()["cache_logger_on_first_use"] is True
    with capture_logs() as logs:
        structlog.get_logger(__name__).warning("visible_again")
    assert [entry["event"] for entry in logs] == ["visible_again"]


def test_a_one_shot_command_prints_no_library_info_lines() -> None:
    """``status`` and ``doctor`` interleaved graph-loading info lines with their tables."""
    import click
    from click.testing import CliRunner

    from repowise.cli.main import cli

    seen: dict = {}

    @click.command("log-probe")
    def probe() -> None:
        with capture_logs() as logs:
            structlog.get_logger("repowise.core.ingestion.graph").info("graph.rehydrated")
        seen["logs"] = logs

    cli.add_command(probe)
    try:
        result = CliRunner().invoke(cli, ["log-probe"])
    finally:
        cli.commands.pop("log-probe", None)

    assert result.exit_code == 0, result.output
    assert seen["logs"] == []
    with capture_logs() as logs:
        structlog.get_logger(__name__).info("visible_after")
    assert [entry["event"] for entry in logs] == ["visible_after"]
