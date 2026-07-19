"""Tests for the `--verbose/-v` flag on `repowise export`.

The export flow itself (markdown/html/json output) isn't covered here; this
only asserts the flag reaches `configure_cli_logging`, matching #930.
"""

from __future__ import annotations

import asyncio

from click.testing import CliRunner

from repowise.cli.commands import export_cmd
from repowise.cli.helpers import ensure_repowise_dir, get_db_url_for_repo
from repowise.cli.main import cli
from repowise.core.persistence import create_engine, init_db


def _init_empty_db(repo_path):
    """Create the wiki DB schema, mirroring what `repowise init` would do."""
    ensure_repowise_dir(repo_path)
    engine = create_engine(get_db_url_for_repo(repo_path))
    asyncio.run(init_db(engine))


def test_export_verbose_flag_reaches_configure_cli_logging(tmp_path, monkeypatch):
    _init_empty_db(tmp_path)
    calls = []
    monkeypatch.setattr(
        export_cmd, "configure_cli_logging", lambda *, verbose: calls.append(verbose)
    )

    result = CliRunner().invoke(cli, ["export", str(tmp_path), "-v"])

    assert result.exit_code == 0, result.output
    assert calls == [True]


def test_export_defaults_to_quiet(tmp_path, monkeypatch):
    _init_empty_db(tmp_path)
    calls = []
    monkeypatch.setattr(
        export_cmd, "configure_cli_logging", lambda *, verbose: calls.append(verbose)
    )

    result = CliRunner().invoke(cli, ["export", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert calls == [False]
