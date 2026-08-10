"""Every command with a machine-readable mode spells it ``--format``.

Phase 1 built the seam (``output.py``) and gave ``search`` the first
``--format json``. This pins the rollout across the rest of the agent-facing
surface, plus the three legacy spellings (``--json`` on ``hook stats`` and the
``workspace`` reports, ``--output`` on ``security scan``) that now resolve onto
``--format`` while continuing to work.

Two properties matter more than any individual payload and are tested as
properties rather than per-command:

1. **``--format`` is the only documented spelling.** A command that grows a
   fourth flag name for the same axis is the problem this phase exists to fix,
   so the alias flags must be hidden.
2. **stdout under json is one parseable document.** Notices, warnings and tips
   go to stderr. A print in a *called* module cannot be diverted by the
   command, which is why ``providers/vector_store.py`` is pinned here too.
"""

from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner

from repowise.cli.output import (
    emit_json,
    format_option,
    json_option,
    notice_console,
    resolve_format,
)

# (import path, attribute) for every command that gained --format in phase 2,
# plus the three that had a legacy spelling folded onto it.
_FORMAT_COMMANDS = [
    ("repowise.cli.commands.status_cmd", "status_command"),
    ("repowise.cli.commands.costs_cmd", "costs_command"),
    ("repowise.cli.commands.saved_cmd", "saved_command"),
    ("repowise.cli.commands.corrections_cmd", "corrections_command"),
    ("repowise.cli.commands.whats_new_cmd", "whats_new_command"),
    ("repowise.cli.commands.decision_cmd", "decision_list"),
    ("repowise.cli.commands.decision_cmd", "decision_show"),
    ("repowise.cli.commands.decision_cmd", "decision_health"),
    ("repowise.cli.commands.coverage_cmd", "coverage_status"),
    ("repowise.cli.commands.security_cmd", "security_scan"),
    ("repowise.cli.commands.hook_cmd", "hook_stats"),
    ("repowise.cli.commands.workspace_cmd", "workspace_check"),
    ("repowise.cli.commands.workspace_cmd", "workspace_diagnostics"),
    ("repowise.cli.commands.workspace_cmd", "workspace_metrics"),
]

# Commands that shipped a machine mode under a different name. The old flag
# keeps working (scripts and CI jobs already call it) but is hidden, so
# ``--help`` documents exactly one spelling.
_LEGACY_ALIASES = [
    ("repowise.cli.commands.hook_cmd", "hook_stats", "--json"),
    ("repowise.cli.commands.workspace_cmd", "workspace_check", "--json"),
    ("repowise.cli.commands.workspace_cmd", "workspace_diagnostics", "--json"),
    ("repowise.cli.commands.workspace_cmd", "workspace_metrics", "--json"),
    ("repowise.cli.commands.security_cmd", "security_scan", "--output"),
]


def _load(module: str, attr: str):
    import importlib

    return getattr(importlib.import_module(module), attr)


def _params(cmd) -> dict[str, click.Parameter]:
    return {opt: p for p in cmd.params for opt in p.opts}


def _split_runner() -> CliRunner:
    """A runner that keeps stderr out of ``result.stdout``.

    The whole point of the notice diversion is that the two streams are
    separate, so a runner that merges them cannot see whether it worked.
    ``mix_stderr`` exists on click 8.1 and was removed in 8.2, where the
    streams are already separate — hence the signature check rather than a
    version comparison.
    """
    import inspect

    if "mix_stderr" in inspect.signature(CliRunner.__init__).parameters:
        return CliRunner(mix_stderr=False)
    return CliRunner()


# ---------------------------------------------------------------------------
# One convention
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("module", "attr"), _FORMAT_COMMANDS)
def test_command_offers_format_json(module: str, attr: str) -> None:
    param = _params(_load(module, attr)).get("--format")
    assert param is not None, f"{attr} has no --format"
    assert "json" in param.type.choices
    assert param.default == "table", "table stays the default; agents opt in"


@pytest.mark.parametrize(("module", "attr", "flag"), _LEGACY_ALIASES)
def test_legacy_alias_still_accepted_but_hidden(module: str, attr: str, flag: str) -> None:
    param = _params(_load(module, attr))[flag]
    assert param.hidden, f"{attr} {flag} should be hidden, not documented"


@pytest.mark.parametrize(("module", "attr", "flag"), _LEGACY_ALIASES)
def test_legacy_alias_absent_from_help(module: str, attr: str, flag: str) -> None:
    result = CliRunner().invoke(_load(module, attr), ["--help"])
    assert result.exit_code == 0
    assert "--format" in result.output
    assert flag not in result.output


@pytest.mark.parametrize(
    ("module", "attr", "args"),
    [
        ("repowise.cli.commands.workspace_cmd", "workspace_check", ["--json"]),
        ("repowise.cli.commands.workspace_cmd", "workspace_diagnostics", ["--json"]),
        ("repowise.cli.commands.workspace_cmd", "workspace_metrics", ["--json"]),
        ("repowise.cli.commands.hook_cmd", "hook_stats", ["--json"]),
        ("repowise.cli.commands.security_cmd", "security_scan", ["--output", "json"]),
    ],
)
def test_legacy_alias_actually_selects_json(module: str, attr: str, args, tmp_path) -> None:
    """Hidden is not enough — the alias has to still *work*.

    Checking ``param.hidden`` passes even if the ``resolve_format`` call that
    folds the alias into ``fmt`` were deleted from the body, which is exactly
    the regression the aliasing introduces. So each one is invoked and its
    stdout parsed. The workspace commands abort without a workspace and
    ``hook stats`` without a ledger; both are run from a bare ``tmp_path``, so
    what is pinned is the *no-crash, stdout-is-json-or-the-abort-path*
    contract rather than a populated payload.
    """
    cmd = _load(module, attr)
    runner = _split_runner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cmd, args)
    if result.exit_code == 0 and result.stdout.strip():
        json.loads(result.stdout)  # must be one parseable document
    else:
        # A ClickException abort is fine; an unhandled traceback is not.
        assert not isinstance(result.exception, (TypeError, KeyError, NameError)), (
            result.exception
        )


def test_resolve_format_lets_the_alias_only_select_json() -> None:
    assert resolve_format("table", True) == "json"
    assert resolve_format("json", True) == "json"
    assert resolve_format("json", False) == "json"
    # The alias is a flag, so its unset state must not veto --format json.
    assert resolve_format("table", False) == "table"


# ---------------------------------------------------------------------------
# stdout stays parseable
# ---------------------------------------------------------------------------


def test_notice_console_is_stderr_under_json() -> None:
    from repowise.cli.helpers import console, err_console

    assert notice_console("json") is err_console
    assert notice_console("table") is console


def test_vector_store_refusal_prints_to_stderr(monkeypatch, tmp_path, capsys) -> None:
    """A print two modules down cannot be diverted by the command.

    ``build_vector_store`` refuses to overwrite a real index with mock vectors
    and says so. On the shared stdout console that sentence lands inside the
    JSON document of whichever command asked for a store, and the keyless repo
    that triggers it is the common case, not an edge one.
    """
    from repowise.cli import providers
    from repowise.core.providers.embedding.base import MockEmbedder

    monkeypatch.setattr(
        "repowise.cli.providers.vector_store.existing_vector_dim", lambda _d: 1536
    )
    assert providers.build_vector_store(tmp_path, MockEmbedder()) is None

    captured = capsys.readouterr()
    assert "Search index left unchanged" in captured.err
    assert captured.out == ""


def test_emit_json_writes_one_parseable_document(capsys) -> None:
    emit_json({"a": 1, "nested": {"b": [1, 2]}})
    assert json.loads(capsys.readouterr().out) == {"a": 1, "nested": {"b": [1, 2]}}


# ---------------------------------------------------------------------------
# The seam's own options
# ---------------------------------------------------------------------------


def test_format_option_binds_to_fmt() -> None:
    @click.command()
    @format_option()
    def cmd(fmt: str) -> None:
        click.echo(fmt)

    assert CliRunner().invoke(cmd, ["--format", "json"]).output.strip() == "json"
    assert CliRunner().invoke(cmd, []).output.strip() == "table"


def test_json_option_binds_to_as_json_and_is_hidden() -> None:
    @click.command()
    @format_option()
    @json_option()
    def cmd(fmt: str, as_json: bool) -> None:
        click.echo(resolve_format(fmt, as_json))

    assert CliRunner().invoke(cmd, ["--json"]).output.strip() == "json"
    assert "--json" not in CliRunner().invoke(cmd, ["--help"]).output


def test_format_option_rejects_an_unknown_format() -> None:
    @click.command()
    @format_option()
    def cmd(fmt: str) -> None:  # pragma: no cover - never reached
        click.echo(fmt)

    result = CliRunner().invoke(cmd, ["--format", "yaml"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Payload shape where a caller would depend on it
# ---------------------------------------------------------------------------


def test_whats_new_json_carries_every_selected_release() -> None:
    """json applies none of the table path's 5-release / 8-bullet caps.

    The caps exist to keep a panel readable. Applying them to json would be
    the same silent truncation this phase removed from the table path, one
    layer down.
    """
    from repowise.cli.commands.whats_new_cmd import whats_new_command

    result = CliRunner().invoke(whats_new_command, ["--all", "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert len(payload["releases"]) > 5
    assert all("version" in r and "sections" in r for r in payload["releases"])
    # The cap that matters is the 8-bullet one, so at least one release has to
    # carry more than 8 bullets. Asserting only that the keys exist would pass
    # against a payload whose sections are all empty.
    assert max(
        sum(len(s["items"]) for s in r["sections"]) for r in payload["releases"]
    ) > 8


def test_status_json_reports_absence_rather_than_only_a_notice(tmp_path) -> None:
    """An unindexed repo still owes stdout a document.

    A command that prints a human notice and nothing else leaves the agent
    parsing an empty string, which is indistinguishable from a crash.
    """
    from repowise.cli.commands.status_cmd import status_command

    result = _split_runner().invoke(
        status_command, [str(tmp_path), "--no-workspace", "--format", "json"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["indexed"] is False


def test_corrections_json_write_does_not_prune_when_the_scan_found_nothing(
    monkeypatch, tmp_path
) -> None:
    """``--write`` must do the same thing in both formats.

    The table path returns before it can write when no rules were found. json
    emits its document first, so without an explicit guard the same invocation
    would fall through and *remove* the managed block from a file the user
    maintains — a destructive difference between two spellings of one command.
    """
    from repowise.cli.commands import corrections_cmd

    monkeypatch.setattr(
        "repowise.core.distill.corrections.scan_corrections",
        lambda *_a, **_k: {"rules": []},
    )
    wrote: list = []
    monkeypatch.setattr(
        corrections_cmd, "_write_managed_blocks", lambda *a, **k: wrote.append(a)
    )

    result = _split_runner().invoke(
        corrections_cmd.corrections_command,
        [str(tmp_path), "--write", "--format", "json"],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["rules"] == []
    assert wrote == [], "an empty scan must not touch the managed block"


def test_workspace_rows_give_every_repo_the_same_keys(tmp_path) -> None:
    """An unindexed repo reports ``None``, not a missing key.

    ``status --format json`` fans out over a workspace, and a consumer that
    has to branch on ``indexed`` before it may read ``stale`` is barely better
    off than one parsing the table. Pins the row contract and the table path's
    "not indexed" branch at the same time.
    """
    from types import SimpleNamespace

    from repowise.cli.commands.status_cmd import _workspace_rows

    indexed = tmp_path / "api"
    (indexed / ".repowise").mkdir(parents=True)
    (tmp_path / "web").mkdir()

    target = SimpleNamespace(
        ws_root=tmp_path,
        ws_config=SimpleNamespace(
            default_repo="api",
            repos=[
                SimpleNamespace(alias="api", path="api", indexed_at=None,
                                last_commit_at_index=None),
                SimpleNamespace(alias="web", path="web", indexed_at=None,
                                last_commit_at_index=None),
            ],
        ),
    )
    rows = _workspace_rows(target)

    assert [r["alias"] for r in rows] == ["api", "web"]
    assert [r["indexed"] for r in rows] == [True, False]
    assert [r["primary"] for r in rows] == [True, False]
    assert set(rows[0]) == set(rows[1]), "row shape must not depend on indexed"
    unindexed = rows[1]
    assert all(
        unindexed[k] is None
        for k in ("files", "symbols", "pages", "docs_mode", "storage_bytes", "head", "stale")
    )
    # The whole payload has to survive json.dumps; a Path or a rich string here
    # would only show up at emit time.
    assert json.loads(json.dumps(rows, default=str))


def test_forgone_rows_keeps_what_it_read_when_a_later_surface_errors(
    monkeypatch, tmp_path
) -> None:
    """A mid-loop sqlite error must not retract earlier surfaces.

    The printer this was split out of printed each surface as it went, so a
    failure on surface 2 left surface 1's line on screen. Accumulating into a
    list makes ``return []`` silently lose it, which is a reporting regression
    no test would otherwise catch.
    """
    import sqlite3

    from repowise.cli.commands import saved_cmd

    class _Row:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    calls = {"n": 0}

    class _Con:
        def execute(self, *_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Row((3, 900, 100))
            raise sqlite3.OperationalError("database is locked")

        def close(self) -> None:
            pass

    # ``_forgone_rows`` imports sqlite3 inside the function, so the module
    # attribute is what it resolves at call time.
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: _Con())
    monkeypatch.setattr(
        "repowise.cli.commands.augment_cmd._shared.hook_flag_enabled", lambda *_a: False
    )

    rows = saved_cmd._forgone_rows(tmp_path, tmp_path / "omissions.db", None)
    assert len(rows) == 1, "the surface read before the error must survive"
    assert rows[0]["forgone_tokens"] == 800


def test_security_scan_json_without_history_still_emits_a_document() -> None:
    """And the human notice that accompanies it is on stderr, not in the payload."""
    from repowise.cli.commands.security_cmd import security_scan

    result = _split_runner().invoke(security_scan, ["--format", "json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "scanned": False,
        "reason": "history-mode-not-requested",
    }
    assert "Working-tree scanning" in result.stderr
