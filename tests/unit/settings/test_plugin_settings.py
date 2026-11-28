"""Settings a plugin declares: keys, variables, the registry's checks and every listing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.core import settings
from repowise.core.registry import plugins
from repowise.core.settings import registry
from repowise.core.settings.plugins import (
    fix_command,
    plugin_setting,
    register_settings,
    source_values,
)
from repowise.core.settings.views import HIDDEN
from tests.unit.settings.plugin_double import (  # noqa: F401
    DB,
    MODE,
    SECRET,
    TOKEN,
    installed,
    no_plugins,
)


def test_a_plugin_setting_is_keyed_under_its_plugin() -> None:
    assert (DB.key, DB.area, DB.scope, DB.env.name) == (
        "plugins.synctest.db",
        "plugins",
        "repo",
        "REPOWISE_PLUGIN_SYNCTEST_DB",
    )
    assert DB.path == ".repowise/config.yaml" and not DB.env.wins
    # A secret is read from its variable only, keyed by it, as every credential is.
    assert (TOKEN.key, TOKEN.scope, TOKEN.secret) == ("REPOWISE_PLUGIN_SYNCTEST_TOKEN", "env", True)
    assert plugin_setting("my-tracker", "api-key", "str", None, "K.").env.name == (
        "REPOWISE_PLUGIN_MY_TRACKER_API_KEY"
    )
    with pytest.raises(ValueError, match="lowercase"):
        plugin_setting("Linear", "team", "str", None, "T.")
    with pytest.raises(ValueError, match="lowercase"):
        plugin_setting("linear", "a.b", "str", None, "T.")


def test_registered_settings_join_every_lookup(installed, monkeypatch) -> None:  # noqa: F811
    assert settings.get("plugins.synctest.db") == DB
    assert settings.by_env("REPOWISE_PLUGIN_SYNCTEST_TOKEN") == TOKEN
    assert {s.key for s in settings.settings_in("plugins")} == {DB.key, MODE.key, TOKEN.key}
    assert DB in settings.all_settings(include_internal=False)
    monkeypatch.setenv(DB.env.name, "from-env")
    assert settings.resolve(DB, {"plugins": {"synctest": {"db": "app"}}}).value == "app"
    assert settings.resolve(DB, {}).value == "from-env"
    (record,) = plugins.load_plugins()
    assert "setting:plugins.synctest.db" in record.seams
    assert "setting:REPOWISE_PLUGIN_SYNCTEST_TOKEN" in record.seams


def test_a_clash_adds_nothing_and_fails_the_registration(no_plugins) -> None:  # noqa: F811
    before = settings.all_settings()
    fresh = plugin_setting("other", "x", "str", None, "X.")
    taken = plugin_setting("other", "y", "str", None, "Y.", env=settings.Env("REPOWISE_API_KEY"))
    with pytest.raises(ValueError, match="REPOWISE_API_KEY is registered twice"):
        register_settings(fresh, taken)
    assert settings.all_settings() == before
    with pytest.raises(ValueError, match="plugins area"):
        register_settings(settings.Setting("other.z", "str", None, "Z.", "evidence"))
    register_settings(fresh)
    with pytest.raises(ValueError, match="registered twice"):
        register_settings(fresh)


def test_a_setting_registered_before_the_first_lookup_is_collected(no_plugins) -> None:  # noqa: F811
    registry._by_key = None  # as in a fresh process: nothing collected yet
    register_settings(plugin_setting("early", "x", "str", None, "X."))
    assert settings.get("plugins.early.x").area == "plugins"
    assert settings.get("decisions.mode").area == "decisions"


def test_an_uninstalled_plugins_key_is_unknown(no_plugins, tmp_path: Path) -> None:  # noqa: F811
    with pytest.raises(KeyError):
        settings.get("plugins.synctest.db")
    out = CliRunner().invoke(
        cli,
        ["config", "explain", "plugins.synctest.db", "--path", str(tmp_path), "--format", "json"],
    )
    assert out.exit_code == 1
    assert json.loads(out.output)["error"] == "unknown_setting"


def test_doctor_names_a_key_no_loaded_plugin_declares(no_plugins) -> None:  # noqa: F811
    from repowise.cli.commands.doctor_cmd._checks.settings import _file_settings, _unknown_rows

    (row,) = _unknown_rows({"plugins": {"synctest": {"db": "app"}}}, _file_settings())
    assert row.level == "warn"
    assert "no loaded plugin declares it" in row.detail


def test_config_lists_explains_and_sets_an_installed_plugins_settings(
    installed,
    tmp_path: Path,
    monkeypatch,  # noqa: F811
) -> None:
    (tmp_path / ".repowise").mkdir()
    monkeypatch.setenv(TOKEN.env.name, SECRET)
    runner = CliRunner()
    set_out = runner.invoke(
        cli, ["config", "set", DB.key, "app", "--path", str(tmp_path), "--format", "json"]
    )
    assert set_out.exit_code == 0, set_out.output
    written = yaml.safe_load((tmp_path / ".repowise" / "config.yaml").read_text(encoding="utf-8"))
    assert written == {"plugins": {"synctest": {"db": "app"}}}
    listed = runner.invoke(
        cli, ["config", "list", "--area", "plugins", "--path", str(tmp_path), "--format", "json"]
    )
    rows = {r["key"]: r for r in json.loads(listed.output)["settings"]}
    assert rows[DB.key]["value"] == "app"
    assert rows[TOKEN.key]["value"] == HIDDEN
    assert SECRET not in listed.output
    explained = runner.invoke(
        cli, ["config", "explain", DB.key, "--path", str(tmp_path), "--format", "json"]
    )
    assert explained.exit_code == 0, explained.output


def test_source_values_split_plain_and_secret_and_name_what_is_missing(monkeypatch) -> None:
    monkeypatch.delenv(TOKEN.env.name, raising=False)
    monkeypatch.delenv(DB.env.name, raising=False)
    plain, secrets, missing = source_values((DB, MODE, TOKEN), {}, environ={})
    assert (plain, secrets, missing) == ({MODE.key: "fast"}, {}, [DB, TOKEN])
    plain, secrets, missing = source_values(
        (DB, MODE, TOKEN),
        {"plugins": {"synctest": {"db": "app"}}},
        environ={TOKEN.env.name: SECRET},
    )
    assert plain == {DB.key: "app", MODE.key: "fast"}
    assert secrets == {TOKEN.key: SECRET} and missing == []
    # A blank variable is unset.
    assert source_values((TOKEN,), {}, environ={TOKEN.env.name: " "})[2] == [TOKEN]


def test_fix_command_is_the_one_command_that_sets_it() -> None:
    assert fix_command(DB) == "repowise config set plugins.synctest.db '<value>'"
    assert fix_command(TOKEN) == "export REPOWISE_PLUGIN_SYNCTEST_TOKEN=<value>"


def test_plugins_lists_what_each_plugin_still_needs(installed, tmp_path, monkeypatch) -> None:  # noqa: F811
    monkeypatch.chdir(tmp_path)
    out = CliRunner().invoke(cli, ["plugins", "--format", "json"])
    (row,) = [r for r in json.loads(out.output)["plugins"] if r["name"] == "synctest"]
    assert row["configured"] is False
    assert row["missing"] == [
        {"key": DB.key, "fix": fix_command(DB)},
        {"key": TOKEN.key, "fix": fix_command(TOKEN)},
    ]
    monkeypatch.setenv(DB.env.name, "app")
    monkeypatch.setenv(TOKEN.env.name, SECRET)
    out = CliRunner().invoke(cli, ["plugins"])
    assert "not configured" not in out.output
    assert SECRET not in out.output
