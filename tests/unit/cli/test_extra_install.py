"""Choosing a provider that ships as an extra: one question, then the install; never silent."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

import click
import pytest
from rich.console import Console

from repowise.cli.ui import extra_install, provider_selection
from repowise.cli.ui.brand import THEME
from repowise.core import extras
from repowise.core.extras import InstallPlan

_PLAN = InstallPlan("litellm", "venv", ("python", "-m", "pip", "install", "litellm<2"), True)
_PRINT_ONLY = InstallPlan("litellm", "editable", _PLAN.argv, False, "this is a source checkout")


def _console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, force_terminal=False, width=200, theme=THEME), buf


@pytest.fixture
def missing(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """litellm absent; records installs and makes a successful one stick."""
    state: dict[str, Any] = {"installed": False, "runs": [], "plan": _PLAN}
    monkeypatch.setattr(extras, "is_installed", lambda _e: state["installed"])
    monkeypatch.setattr(extras, "plan_install", lambda _e, **_k: state["plan"])

    def install(extra: str, *, plan: InstallPlan | None = None, **_k: Any) -> InstallPlan:
        state["runs"].append(plan)
        state["installed"] = True
        return plan or _PLAN

    monkeypatch.setattr(extras, "install", install)
    return state


def test_a_yes_installs_and_reports(monkeypatch: pytest.MonkeyPatch, missing: dict) -> None:
    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: True)
    console, buf = _console()
    assert extra_install.offer_extra(console, "litellm", "The litellm provider", interactive=True)
    assert missing["runs"] == [_PLAN]
    assert "litellm installed" in buf.getvalue()


def test_a_no_installs_nothing_and_prints_the_command(
    monkeypatch: pytest.MonkeyPatch, missing: dict
) -> None:
    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: False)
    console, buf = _console()
    assert not extra_install.offer_extra(
        console, "litellm", "The litellm provider", interactive=True
    )
    assert missing["runs"] == []
    assert _PLAN.command in buf.getvalue()


def test_a_non_interactive_run_never_installs(
    monkeypatch: pytest.MonkeyPatch, missing: dict
) -> None:
    def ask(*_a: object, **_k: object) -> bool:
        raise AssertionError("asked without a terminal")

    monkeypatch.setattr(click, "confirm", ask)
    console, buf = _console()
    assert not extra_install.offer_extra(
        console, "litellm", "The litellm provider", interactive=False
    )
    assert missing["runs"] == []
    assert "repowise[litellm]" in buf.getvalue()
    assert _PLAN.command in buf.getvalue()


def test_a_print_only_plan_is_printed_even_at_a_terminal(
    monkeypatch: pytest.MonkeyPatch, missing: dict
) -> None:
    missing["plan"] = _PRINT_ONLY
    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: pytest.fail("asked"))
    console, buf = _console()
    assert not extra_install.offer_extra(
        console, "litellm", "The litellm provider", interactive=True
    )
    assert "source checkout" in buf.getvalue()


def test_a_failed_install_says_so(monkeypatch: pytest.MonkeyPatch, missing: dict) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise extras.ExtraInstallError("pip exited with 1")

    monkeypatch.setattr(extras, "install", boom)
    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: True)
    console, buf = _console()
    assert not extra_install.offer_extra(
        console, "litellm", "The litellm provider", interactive=True
    )
    assert "Install failed" in buf.getvalue()


# --- the init provider picker ------------------------------------------------


def _pick(monkeypatch: pytest.MonkeyPatch, *answers: str) -> tuple[str | None, str]:
    providers = list(provider_selection._PROVIDER_CHOICES)
    picks = iter(str(providers.index(a) + 1) for a in answers)
    monkeypatch.setattr(provider_selection.Prompt, "ask", lambda *_a, **_k: next(picks))
    monkeypatch.setattr(
        provider_selection,
        "_detect_provider_status",
        lambda *_a: {"litellm": "LITELLM_API_KEY", "openai": "OPENAI_API_KEY"},
    )
    console, buf = _console()
    return provider_selection._interactive_provider_name(console, None), buf.getvalue()


def test_picking_litellm_installs_it_after_one_yes(
    monkeypatch: pytest.MonkeyPatch, missing: dict
) -> None:
    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: True)
    chosen, out = _pick(monkeypatch, "litellm")
    assert chosen == "litellm"
    assert missing["runs"] == [_PLAN]
    assert "installs on pick" in out


def test_declining_the_install_goes_back_to_the_picker(
    monkeypatch: pytest.MonkeyPatch, missing: dict
) -> None:
    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: False)
    chosen, _out = _pick(monkeypatch, "litellm", "openai")
    assert chosen == "openai"
    assert missing["runs"] == []


def test_an_installed_extra_asks_nothing(monkeypatch: pytest.MonkeyPatch, missing: dict) -> None:
    missing["installed"] = True
    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: pytest.fail("asked"))
    chosen, out = _pick(monkeypatch, "litellm")
    assert chosen == "litellm"
    assert "installs on pick" not in out


# --- resolution, doctor, config set -------------------------------------------


def test_a_non_interactive_run_fails_with_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: dict
) -> None:
    from repowise.cli._helpers.providers import resolve_provider_or_prompt

    with pytest.raises(click.ClickException) as caught:
        resolve_provider_or_prompt("litellm", None, tmp_path, interactive=False)
    assert caught.value.reason == "provider_extra_missing"
    assert caught.value.message.endswith(_PLAN.command)
    assert missing["runs"] == []


def test_the_first_interactive_run_offers_the_install_then_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: dict
) -> None:
    from repowise.cli._helpers.providers import resolve_provider_or_prompt

    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: True)
    provider = resolve_provider_or_prompt("litellm", "groq/x", tmp_path, interactive=True)
    assert provider.provider_name == "litellm"
    assert missing["runs"] == [_PLAN]


def test_doctor_fails_the_chosen_provider_whose_package_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: dict
) -> None:
    from repowise.cli.commands.doctor_cmd._checks import providers

    monkeypatch.setenv("REPOWISE_PROVIDER", "litellm")
    row = providers._provider_extra_check(tmp_path)
    assert row is not None and not row.ok
    assert row.fix == _PLAN.command
    assert "repowise[litellm]" in row.detail

    missing["installed"] = True
    assert providers._provider_extra_check(tmp_path) is None


def test_config_set_provider_offers_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: dict
) -> None:
    from repowise.cli.commands import config_cmd

    offered: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        extra_install,
        "offer_extra",
        lambda _c, extra, _w, *, interactive: offered.append((extra, interactive)) or True,
    )
    monkeypatch.setattr(config_cmd, "_repo", lambda *_a: tmp_path)
    config_cmd._write("set", "provider", "litellm", None, "text")
    assert offered and offered[0][0] == "litellm"


def test_config_set_provider_json_carries_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: dict, capsys: Any
) -> None:
    import json

    from repowise.cli.commands import config_cmd

    monkeypatch.setattr(config_cmd, "_repo", lambda *_a: tmp_path)
    config_cmd._write("set", "provider", "litellm", None, "json")
    assert json.loads(capsys.readouterr().out)["install_command"] == _PLAN.command
    assert missing["runs"] == []


def test_update_yes_never_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: dict
) -> None:
    from repowise.cli._helpers.providers import resolve_provider_or_prompt

    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: pytest.fail("asked"))
    with pytest.raises(click.ClickException):
        resolve_provider_or_prompt("litellm", None, tmp_path, interactive=True, allow_install=False)
    assert missing["runs"] == []


def test_init_offers_the_install_for_a_saved_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: dict
) -> None:
    from repowise.cli._helpers.providers import resolve_explicit_provider_or_prompt

    monkeypatch.setenv("REPOWISE_PROVIDER", "litellm")
    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: True)
    provider = resolve_explicit_provider_or_prompt(None, "groq/x", tmp_path, interactive=True)
    assert provider.provider_name == "litellm"
    assert missing["runs"] == [_PLAN]
