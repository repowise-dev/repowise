"""``repowise sources sync`` and the sync at the end of ``repowise update``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from repowise.cli.commands import sources_cmd
from repowise.cli.main import cli
from repowise.core.analysis.actions.rules import setup as setup_rules
from repowise.core.analysis.actions.setup import SetupFacts
from repowise.core.source_sync import LEDGER_KEY, plan_sources, unsynced_sources
from tests.unit.cli.test_evidence_cmd import _indexed_copy, _stored
from tests.unit.settings import plugin_double
from tests.unit.settings.plugin_double import DB, SECRET, TOKEN

installed = plugin_double.installed
no_plugins = plugin_double.no_plugins


@pytest.fixture
def root(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path / "source"
    _indexed_copy(root, tmp_path_factory)
    return root


def _configure(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = root / ".repowise" / "config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    config = config or {}
    config["plugins"] = {"synctest": {"db": "app"}}
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setenv(TOKEN.env.name, SECRET)


def _sync(root: Path, *args: str) -> tuple[int, dict]:
    out = CliRunner().invoke(
        cli, ["sources", "sync", "--path", str(root), "--format", "json", *args]
    )
    assert SECRET not in out.output
    return out.exit_code, json.loads(out.output)


def _state(root: Path) -> dict:
    path = root / ".repowise" / "state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def test_a_source_missing_a_setting_says_the_command_that_sets_it(installed, root) -> None:
    code, out = _sync(root)
    assert code == 0
    (r,) = out["sources"]
    assert (r["source"], r["seam"], r["state"]) == ("synctest", "evidence-source", "not_configured")
    assert r["next_command"] == "repowise config set plugins.synctest.db '<value>'"
    assert [m["fix"] for m in r["missing"]] == [
        "repowise config set plugins.synctest.db '<value>'",
        "export REPOWISE_PLUGIN_SYNCTEST_TOKEN=<value>",
    ]
    assert installed.requests == []
    assert LEDGER_KEY not in _state(root)
    # Named, it did not store: non-zero.
    assert _sync(root, "--source", "synctest")[0] == 1


def test_a_configured_source_stores_its_batches_and_resumes_from_its_cursor(
    installed,
    root,
    monkeypatch,
) -> None:
    _configure(root, monkeypatch)
    code, out = _sync(root, "--source", "synctest")
    assert code == 0
    (r,) = out["sources"]
    assert (r["state"], r["batches"], r["next_command"]) == (
        "stored",
        2,
        "repowise evidence status",
    )
    request = installed.requests[0]
    assert request.settings == {DB.key: "app", "plugins.synctest.mode": "fast"}
    assert request.credentials == {TOKEN.key: SECRET}
    assert request.cursor is None
    assert SECRET not in repr(request)
    runs, _costs = _stored(root)
    assert len(runs) == 2
    entry = _state(root)[LEDGER_KEY]["evidence-source:synctest"]
    assert (entry["cursor"], entry["last_state"]) == ("2", "stored")
    assert (entry["last_batches"], entry["last_message"]) == (2, None)
    assert SECRET not in (root / ".repowise" / "state.json").read_text(encoding="utf-8")

    # ``repowise plugins`` reads the same entry back as the source's last sync.
    monkeypatch.chdir(root)
    listing = json.loads(CliRunner().invoke(cli, ["plugins", "--format", "json"]).output)
    (source,) = [s for s in listing["sources"] if s["source"] == "synctest"]
    assert source["configured"] is True
    assert source["next_command"] == "repowise sources sync --source synctest"
    last = source["last_sync"]
    assert (last["state"], last["batches"], last["at"]) == ("stored", 2, entry["last_run_at"])

    installed.batches = 3
    _code, again = _sync(root)
    assert installed.requests[-1].cursor == "2"
    assert again["sources"][0]["batches"] == 1


@pytest.mark.parametrize(
    ("fail", "next_command"),
    [
        ("unavailable", "repowise sources sync --source synctest"),
        ("refused", "repowise config list --area plugins"),
    ],
)
def test_a_failing_source_says_why_and_what_to_run(
    installed,
    root,
    monkeypatch,
    fail,
    next_command,
) -> None:
    _configure(root, monkeypatch)
    installed.fail = fail
    code, out = _sync(root)
    assert code == 0  # no source named
    (r,) = out["sources"]
    assert (r["state"], r["batches"], r["next_command"]) == (fail, 0, next_command)
    assert r["message"]
    assert _sync(root, "--source", "synctest")[0] == 1
    assert _state(root)[LEDGER_KEY]["evidence-source:synctest"]["cursor"] is None
    # It never stored, so it is still a setup step.
    assert unsynced_sources(root) == ("synctest",)


def test_a_failure_message_never_quotes_a_secret(installed, root, monkeypatch) -> None:
    _configure(root, monkeypatch)
    installed.fail = "leaky"
    _code, out = _sync(root)  # _sync asserts SECRET is not in the output
    (r,) = out["sources"]
    assert r["state"] == "failed"
    assert r["message"] == "ConnectionError: cannot connect with ***"


def test_an_unknown_source_is_refused(installed, root) -> None:
    code, out = _sync(root, "--source", "nope")
    assert code == 1
    assert (out["error"], out["next_command"]) == ("unknown_source", "repowise plugins")


def test_with_no_source_installed_sync_says_so(no_plugins, root) -> None:
    assert _sync(root) == (
        0,
        {"repo": str(root), "sources": [], "next_command": "repowise plugins"},
    )


def test_text_output_ends_with_the_next_command(installed, root, monkeypatch) -> None:
    _configure(root, monkeypatch)
    out = CliRunner().invoke(cli, ["sources", "sync", "--path", str(root)])
    assert "stored 2 batches. Next: repowise evidence status" in " ".join(out.output.split())


def test_update_with_no_configured_source_reads_and_writes_nothing(
    installed,
    root,
    capsys,
) -> None:
    path = root / ".repowise" / "state.json"
    path.write_text('{"last_sync_commit": "abc"}', encoding="utf-8")
    state = path.read_bytes()
    sources_cmd.sync_after_update(root)
    assert (root / ".repowise" / "state.json").read_bytes() == state
    assert capsys.readouterr().out == ""
    assert installed.requests == []


def test_update_syncs_a_configured_source(installed, root, monkeypatch, capsys) -> None:
    _configure(root, monkeypatch)
    sources_cmd.sync_after_update(root)
    assert "Sources: evidence-source synctest: stored 2 batches" in " ".join(
        capsys.readouterr().out.split()
    )


def test_a_source_failure_never_fails_the_update(installed, root, monkeypatch, capsys) -> None:
    _configure(root, monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("store is locked")

    monkeypatch.setattr("repowise.core.source_sync.sync_source", boom)
    sources_cmd.sync_after_update(root)  # does not raise
    text = " ".join(capsys.readouterr().out.split())
    assert "synctest: failed: RuntimeError. Next: repowise plugins" in text
    monkeypatch.setattr(sources_cmd, "_sync", boom)
    sources_cmd.sync_after_update(root)
    assert "Sources not synced (RuntimeError). Next: repowise sources sync" in " ".join(
        capsys.readouterr().out.split()
    )


@pytest.mark.parametrize(
    ("outcome", "dry_run", "syncs"),
    [
        ("regenerated", False, True),
        ("noop", False, True),
        ("deferred", False, False),
        ("dry_run", True, False),
        # An up-to-date repo under --dry-run ends in noop: a dry run still writes nothing.
        ("noop", True, False),
    ],
)
def test_update_runs_the_sync_after_its_own_work_only(
    monkeypatch, tmp_path, outcome, dry_run, syncs
) -> None:
    from types import SimpleNamespace

    from repowise.cli.commands.update_cmd import command

    calls: list[Path] = []
    monkeypatch.setattr(
        command,
        "_resolve_target",
        lambda opts: SimpleNamespace(is_workspace=False, repo_path=tmp_path),
    )
    monkeypatch.setattr(
        command, "_update_single_repo", lambda opts, run: command.UpdateOutcome(outcome)
    )
    monkeypatch.setattr(sources_cmd, "sync_after_update", calls.append)
    result = command.run_update(
        path=str(tmp_path),
        provider_name=None,
        model=None,
        since=None,
        reasoning=None,
        cascade_budget=None,
        dry_run=dry_run,
        workspace=False,
        no_workspace=True,
        repo_alias=None,
    )
    assert result.value == outcome
    assert calls == ([tmp_path] if syncs else [])


def test_a_configured_source_that_never_synced_is_a_setup_step(
    installed,
    root,
    monkeypatch,
) -> None:
    assert unsynced_sources(root) == ()  # not configured yet
    _configure(root, monkeypatch)
    assert unsynced_sources(root) == ("synctest",)
    action = setup_rules.sources_unsynced(SetupFacts(unsynced_sources=("synctest",))).actions[0]
    assert (action.command, action.tier) == ("repowise sources sync", "improve_signal")
    _sync(root)
    assert unsynced_sources(root) == ()
    assert setup_rules.sources_unsynced(SetupFacts(unsynced_sources=())).actions == ()
    assert setup_rules.sources_unsynced(SetupFacts()).status == "unavailable"


def test_planned_credentials_never_print(installed, monkeypatch) -> None:
    monkeypatch.setenv(TOKEN.env.name, SECRET)
    (planned,) = plan_sources({})
    assert planned.credentials == {TOKEN.key: SECRET}
    assert SECRET not in repr(planned)
    assert [s.key for s in planned.missing] == [DB.key]


def test_a_source_that_declares_no_settings_is_left_to_its_host(installed, root, capsys) -> None:
    from repowise.core.evidence.sources import evidence_sources

    class HostOnly:
        name = "host-only"
        kinds = ("statement_stats",)

        def fetch(self, request):
            raise AssertionError("repowise must not run a host-only source")

    evidence_sources.register(HostOnly.name, HostOnly())
    code, out = _sync(root, "--source", "host-only")
    assert code == 1
    (r,) = out["sources"]
    assert (r["state"], r["next_command"]) == ("not_configured", "repowise plugins")
    assert "only a host" in r["message"]
    sources_cmd.sync_after_update(root)
    assert capsys.readouterr().out == ""
    assert unsynced_sources(root) == ()


def test_the_cursor_moves_only_once_the_batches_are_committed(installed, root, monkeypatch) -> None:
    from contextlib import asynccontextmanager

    from repowise.core import persistence

    _configure(root, monkeypatch)
    real = persistence.get_session
    opened: list[int] = []

    @asynccontextmanager
    async def commit_fails_for_the_fetch(factory):
        async with real(factory) as session:
            yield session
        opened.append(1)
        if len(opened) == 2:  # the first session only checks the index
            raise RuntimeError("database is locked")

    monkeypatch.setattr(persistence, "get_session", commit_fails_for_the_fetch)
    _code, out = _sync(root)
    assert out["sources"][0]["state"] == "failed"
    assert _state(root)[LEDGER_KEY]["evidence-source:synctest"]["cursor"] is None


def test_a_changed_setting_starts_from_no_cursor(installed, root, monkeypatch) -> None:
    _configure(root, monkeypatch)
    _sync(root)
    assert installed.requests[-1].cursor is None
    _sync(root)
    assert installed.requests[-1].cursor == "2"
    path = root / ".repowise" / "config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["plugins"]["synctest"]["db"] = "other"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    _code, out = _sync(root)
    assert installed.requests[-1].cursor is None
    assert out["sources"][0]["batches"] == 2
