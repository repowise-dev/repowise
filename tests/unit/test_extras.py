"""repowise.core.extras: install-method detection, the install plan, and the missing-extra error."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from repowise.core import extras
from repowise.core.extras import (
    CONDA,
    EDITABLE,
    PIPX,
    SYSTEM,
    UV_TOOL,
    VENV,
    ExtraInstallError,
    InstallPlan,
    MissingExtraError,
    detect_install_method,
    plan_install,
)

PY = "/envs/repowise/bin/python"


def _env(tmp_path: Path, marker: str | None = None, *, folder: bool = False) -> Path:
    root = tmp_path / "env"
    root.mkdir()
    if marker:
        (root / marker).mkdir() if folder else (root / marker).write_text("")
    return root


# --- detection -------------------------------------------------------------


def test_a_uv_tool_environment_is_known_by_its_receipt(tmp_path: Path) -> None:
    root = _env(tmp_path, "uv-receipt.toml")
    assert detect_install_method(prefix=root, base_prefix="/usr", editable=False) == UV_TOOL


def test_a_pipx_environment_is_known_by_its_metadata(tmp_path: Path) -> None:
    root = _env(tmp_path, "pipx_metadata.json")
    assert detect_install_method(prefix=root, base_prefix="/usr", editable=False) == PIPX


def test_a_path_named_pipx_is_not_proof_of_pipx(tmp_path: Path) -> None:
    root = tmp_path / "pipx" / "venvs" / "repowise"
    root.mkdir(parents=True)
    assert detect_install_method(prefix=root, base_prefix="/usr", editable=False) == VENV


def test_a_conda_environment_is_known_by_conda_meta(tmp_path: Path) -> None:
    root = _env(tmp_path, "conda-meta", folder=True)
    assert detect_install_method(prefix=root, base_prefix=root, editable=False) == CONDA


def test_a_plain_virtualenv_differs_from_its_base(tmp_path: Path) -> None:
    root = _env(tmp_path)
    assert detect_install_method(prefix=root, base_prefix=tmp_path, editable=False) == VENV


def test_the_base_interpreter_itself_is_system(tmp_path: Path) -> None:
    root = _env(tmp_path)
    assert detect_install_method(prefix=root, base_prefix=root, editable=False) == SYSTEM


def test_a_source_checkout_wins_over_the_environment_it_sits_in(tmp_path: Path) -> None:
    root = _env(tmp_path, "uv-receipt.toml")
    assert detect_install_method(prefix=root, base_prefix="/usr", editable=True) == EDITABLE


# --- the plan --------------------------------------------------------------


def _which(*present: str):
    return lambda name: f"/bin/{name}" if name in present else None


def _plan(method: str, **kwargs: Any) -> InstallPlan:
    kwargs.setdefault("which", _which())
    kwargs.setdefault("has_pip", True)
    kwargs.setdefault("writable", True)
    kwargs.setdefault("externally_managed", False)
    return plan_install("litellm", method=method, python=PY, **kwargs)


def test_uv_tool_adds_the_package_with_uv_pip_against_its_own_interpreter() -> None:
    plan = _plan(UV_TOOL, which=_which("uv"))
    assert plan.runnable
    assert plan.argv[:5] == ("uv", "pip", "install", "--python", PY)
    assert plan.argv[5].startswith("litellm")


def test_uv_tool_without_uv_on_path_prints_the_command() -> None:
    plan = _plan(UV_TOOL)
    assert not plan.runnable
    assert "uv is not on PATH" in plan.reason


def test_pipx_uses_the_venvs_own_pip_so_the_target_is_this_interpreter() -> None:
    plan = _plan(PIPX, which=_which("pipx"))
    assert plan.runnable
    assert plan.argv[:4] == (PY, "-m", "pip", "install")


def test_pipx_without_pip_injects_into_the_venv_by_its_name() -> None:
    plan = _plan(PIPX, has_pip=False, which=_which("pipx"))
    assert plan.runnable
    assert plan.argv[:3] == ("pipx", "inject", "repowise")


def test_pipx_with_neither_pip_nor_pipx_prints_the_command() -> None:
    plan = _plan(PIPX, has_pip=False)
    assert not plan.runnable
    assert "pipx is not on PATH" in plan.reason


@pytest.mark.parametrize("method", [CONDA, VENV, SYSTEM])
def test_pip_environments_use_this_interpreters_pip(method: str) -> None:
    plan = _plan(method)
    assert plan.runnable
    assert plan.argv[:4] == (PY, "-m", "pip", "install")


def test_a_venv_without_pip_uses_uv_when_there_is_one() -> None:
    plan = _plan(VENV, has_pip=False, which=_which("uv"))
    assert plan.runnable
    assert plan.argv[:2] == ("uv", "pip")


def test_a_venv_with_neither_pip_nor_uv_prints_the_command() -> None:
    plan = _plan(VENV, has_pip=False)
    assert not plan.runnable


def test_a_source_checkout_is_never_installed_into() -> None:
    plan = _plan(EDITABLE, which=_which("uv", "pipx"))
    assert not plan.runnable
    assert "source checkout" in plan.reason


def test_a_system_managed_python_is_never_installed_into() -> None:
    plan = _plan(SYSTEM, externally_managed=True)
    assert not plan.runnable
    assert "managed by the system" in plan.reason


def test_an_environment_this_user_cannot_write_is_never_installed_into() -> None:
    plan = _plan(VENV, writable=False)
    assert not plan.runnable
    assert "cannot write" in plan.reason


def test_the_pins_come_from_the_extra() -> None:
    (req,) = extras.requirements("litellm")
    assert req.startswith("litellm") and "<2" in req


def test_the_printed_command_quotes_what_a_shell_would_redirect() -> None:
    command = _plan(VENV).command
    assert '"litellm' in command and command.endswith('"')
    assert command.startswith(f"{PY} -m pip install ")


def test_an_unknown_extra_is_refused() -> None:
    with pytest.raises(ValueError, match="Unknown extra"):
        plan_install("nope")


# --- install ---------------------------------------------------------------


def _done(code: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout="", stderr=stderr)


def test_install_runs_the_plan_then_imports_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[list[str]] = []
    imported: list[str] = []
    monkeypatch.setattr(extras.importlib, "import_module", imported.append)
    plan = _plan(VENV)

    extras.install("litellm", plan=plan, run=lambda argv, **_k: ran.append(argv) or _done(0))

    assert ran == [list(plan.argv)]
    assert imported == ["litellm"]


def test_a_failed_command_raises_with_its_tail() -> None:
    with pytest.raises(ExtraInstallError, match="exited with 1:\nno space left"):
        extras.install("litellm", plan=_plan(VENV), run=lambda *_a, **_k: _done(1, "no space left"))


def test_an_extra_that_still_does_not_import_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(name: str) -> None:
        raise ImportError(f"no module {name}")

    monkeypatch.setattr(extras.importlib, "import_module", fail)
    with pytest.raises(ExtraInstallError, match="still does not import"):
        extras.install("litellm", plan=_plan(VENV), run=lambda *_a, **_k: _done(0))


def test_an_import_that_fails_with_any_error_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_name: str) -> None:
        raise AttributeError("module has no attribute 'x'")

    monkeypatch.setattr(extras.importlib, "import_module", broken)
    with pytest.raises(ExtraInstallError, match="still does not import"):
        extras.install("litellm", plan=_plan(VENV), run=lambda *_a, **_k: _done(0))


def test_an_installer_that_cannot_start_is_a_failure_not_a_traceback() -> None:
    def missing(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("uv")

    with pytest.raises(ExtraInstallError, match="Could not run"):
        extras.install("litellm", plan=_plan(VENV), run=missing)


def test_a_print_only_plan_is_never_run() -> None:
    def run(*_a: object, **_k: object) -> None:
        raise AssertionError("ran a print-only command")

    with pytest.raises(ExtraInstallError, match="Run it yourself"):
        extras.install("litellm", plan=_plan(EDITABLE), run=run)


# --- the missing-extra error -----------------------------------------------


def _missing(monkeypatch: pytest.MonkeyPatch) -> InstallPlan:
    plan = _plan(VENV)
    monkeypatch.setattr(extras, "is_installed", lambda _extra: False)
    monkeypatch.setattr(extras, "plan_install", lambda _extra, **_k: plan)
    return plan


def test_require_names_the_extra_and_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _missing(monkeypatch)
    with pytest.raises(MissingExtraError) as caught:
        extras.require("litellm", "The litellm provider")
    message = str(caught.value)
    assert "repowise[litellm]" in message
    assert message.endswith(plan.command)
    assert caught.value.command == plan.command


def test_the_registry_refuses_litellm_without_its_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.core.providers.llm.registry import get_provider, provider_missing_extra

    plan = _missing(monkeypatch)
    assert provider_missing_extra("litellm") == "litellm"
    with pytest.raises(MissingExtraError, match="The litellm provider needs") as caught:
        get_provider("litellm", model="groq/x", api_key="k")
    assert caught.value.command == plan.command


def test_only_litellm_ships_as_an_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.core.providers.llm.registry import provider_missing_extra
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    _missing(monkeypatch)
    assert {n for n in PROVIDER_SPECS if provider_missing_extra(n)} == {"litellm"}


def test_the_providers_lazy_import_names_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    from repowise.core.providers.llm import litellm as module

    real_import = builtins.__import__

    def no_litellm(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "litellm":
            raise ImportError("No module named 'litellm'")
        return real_import(name, *args, **kwargs)

    plan = _missing(monkeypatch)
    monkeypatch.setattr(builtins, "__import__", no_litellm)
    with pytest.raises(MissingExtraError, match="Install it with") as caught:
        module._import_litellm()
    assert caught.value.command == plan.command


def test_the_server_refuses_litellm_without_its_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.server.provider_config import set_active_provider

    plan = _missing(monkeypatch)
    with pytest.raises(ValueError, match=r"repowise\[litellm\]") as caught:
        set_active_provider("litellm", "groq/x")
    assert str(caught.value).endswith(plan.command)
