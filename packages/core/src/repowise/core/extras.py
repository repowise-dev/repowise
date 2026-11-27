"""Optional extras: whether one is installed here, and the one command that adds it.

An extra is a group of packages left out of the default install and named
after the bracket that brings them in (``repowise[litellm]``). Every surface
that needs one (the provider picker, ``repowise config set``, doctor, the
provider registry, the server) asks this module, so the install command a user
sees is always the one that matches how *this* copy of repowise was installed:

* ``uv tool``: the environment holds ``uv-receipt.toml``; ``uv pip install
  --python <this interpreter>`` adds the packages without reinstalling the
  tool (whose running ``repowise.exe`` Windows keeps locked).
* ``pipx``: the environment holds ``pipx_metadata.json``; its own pip adds
  the package to exactly this venv, and ``pipx upgrade`` keeps it.
  ``pipx inject`` is the fallback when the venv has no pip.
* conda, a virtualenv, or a writable system Python: ``<python> -m pip install``
  (``uv pip install --python`` when the environment has no pip, as ``uv venv``
  makes them).
* An editable source checkout, an externally managed Python, or a
  site-packages this user cannot write: the command is printed, never run.

:func:`install` runs the command and then imports the extra's modules, so
success means the import works in this process, not that the installer
exited 0.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "EXTRAS",
    "Extra",
    "ExtraInstallError",
    "InstallPlan",
    "MissingExtraError",
    "detect_install_method",
    "install",
    "is_installed",
    "missing_message",
    "plan_install",
    "require",
]


@dataclass(frozen=True, slots=True)
class Extra:
    """One optional extra of the ``repowise`` distribution."""

    name: str
    #: Importable exactly when the extra is installed.
    modules: tuple[str, ...]
    #: Used only when the installed metadata cannot be read (a source run).
    #: The pins themselves live in pyproject.toml.
    fallback_requirements: tuple[str, ...]
    #: Download size, shown before asking.
    size: str


EXTRAS: dict[str, Extra] = {
    "litellm": Extra(
        name="litellm",
        modules=("litellm",),
        fallback_requirements=("litellm>=1.90.7,<2",),
        size="about 215 MB",
    ),
}


class MissingExtraError(ImportError):
    """An extra this code path needs is not installed; the message names the fix."""

    def __init__(self, message: str, *, extra: str, command: str) -> None:
        super().__init__(message)
        self.extra = extra
        self.command = command


class ExtraInstallError(RuntimeError):
    """The install command failed, or ran and the extra still does not import."""


# Methods. Plain strings, so they read as-is in JSON and messages.
UV_TOOL = "uv tool"
PIPX = "pipx"
CONDA = "conda"
VENV = "venv"
SYSTEM = "system"
EDITABLE = "editable"


@dataclass(frozen=True, slots=True)
class InstallPlan:
    """How to add one extra to the running environment."""

    extra: str
    method: str
    argv: tuple[str, ...]
    #: False: print :attr:`command` for the user, never run it.
    runnable: bool
    #: Why the command is print-only, for the message.
    reason: str = ""

    @property
    def command(self) -> str:
        return _display(self.argv)


def _get(extra: str) -> Extra:
    try:
        return EXTRAS[extra]
    except KeyError:
        raise ValueError(f"Unknown extra {extra!r}; known: {', '.join(EXTRAS)}") from None


def is_installed(extra: str) -> bool:
    """Whether every module of *extra* can be found, importing none of them."""
    for module in _get(extra).modules:
        try:
            if importlib.util.find_spec(module) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def requirements(extra: str) -> tuple[str, ...]:
    """The extra's requirement strings, read from the installed ``repowise`` metadata.

    Reading them keeps pyproject.toml the one place the pins are written.
    """
    from importlib.metadata import PackageNotFoundError, requires

    from packaging.requirements import InvalidRequirement, Requirement

    try:
        declared = requires("repowise") or []
    except PackageNotFoundError:
        declared = []
    found: list[str] = []
    for line in declared:
        try:
            req = Requirement(line)
        except InvalidRequirement:
            continue
        if req.marker is None or not req.marker.evaluate({"extra": extra}):
            continue
        if not req.marker.evaluate({"extra": ""}):  # only under this extra
            req.marker = None
            found.append(str(req))
    return tuple(found) or _get(extra).fallback_requirements


def _editable() -> bool:
    """Whether ``repowise`` runs from a source checkout rather than an installed wheel.

    Read from where the code lives, the rule ``repowise doctor``'s update
    check uses: a wheel always lands under ``site-packages``/``dist-packages``.
    """
    import repowise.core

    parts = {part.lower() for part in Path(repowise.core.__file__).resolve().parts}
    return not parts & {"site-packages", "dist-packages"}


def detect_install_method(
    *,
    prefix: str | os.PathLike[str] | None = None,
    base_prefix: str | os.PathLike[str] | None = None,
    editable: bool | None = None,
) -> str:
    """How this copy of repowise was installed: one of the method constants."""
    root = Path(prefix if prefix is not None else sys.prefix)
    base = Path(base_prefix if base_prefix is not None else sys.base_prefix)
    if editable if editable is not None else _editable():
        return EDITABLE
    if (root / "uv-receipt.toml").is_file():
        return UV_TOOL
    if (root / "pipx_metadata.json").is_file():
        return PIPX
    if (root / "conda-meta").is_dir():
        return CONDA
    if root.resolve() != base.resolve():
        return VENV
    return SYSTEM


def _externally_managed() -> bool:
    stdlib = sysconfig.get_path("stdlib")
    return bool(stdlib) and (Path(stdlib) / "EXTERNALLY-MANAGED").is_file()


def _site_writable() -> bool:
    purelib = sysconfig.get_path("purelib")
    return bool(purelib) and os.access(purelib, os.W_OK)


def _has_pip() -> bool:
    try:
        return importlib.util.find_spec("pip") is not None
    except (ImportError, ValueError):
        return False


def plan_install(
    extra: str,
    *,
    method: str | None = None,
    python: str | None = None,
    which=shutil.which,
    has_pip: bool | None = None,
    writable: bool | None = None,
    externally_managed: bool | None = None,
) -> InstallPlan:
    """The command that adds *extra* here, and whether it is safe to run.

    The keyword arguments exist for tests; callers pass only *extra*.
    """
    _get(extra)
    method = method or detect_install_method()
    python = python or sys.executable or "python"
    pip_ok = _has_pip() if has_pip is None else has_pip
    argv, blocker = _command(method, python, requirements(extra), which, pip_ok)
    if blocker is None and method != UV_TOOL and argv[0] != "pipx":
        blocker = _environment_blocker(method, writable, externally_managed)
    return InstallPlan(extra, method, argv, blocker is None, blocker or "")


def _command(
    method: str, python: str, reqs: tuple[str, ...], which, pip_ok: bool
) -> tuple[tuple[str, ...], str | None]:
    """The command for *method*, and why it must not run here (None: it may)."""
    # Bare names: the process resolves them on PATH, and the printed command
    # stays short. The interpreter is a full path, since it is the target.
    uv_pip = ("uv", "pip", "install", "--python", python, *reqs)
    pip = (python, "-m", "pip", "install", *reqs)
    has_uv = which("uv") is not None
    if method == UV_TOOL:
        return uv_pip, None if has_uv else "uv is not on PATH"
    if method == PIPX and not pip_ok and which("pipx") is not None:
        # pip in the venv targets exactly this interpreter and survives
        # `pipx upgrade`; `pipx inject` resolves the venv by name in pipx's
        # own home, which may not be this one, so it is the fallback.
        return ("pipx", "inject", Path(python).parent.parent.name, *reqs), None
    argv = pip if pip_ok or not has_uv else uv_pip
    if method == EDITABLE:
        return argv, "this is a source checkout"
    if not pip_ok and not has_uv:
        return pip, "pipx is not on PATH" if method == PIPX else "this environment has no pip"
    return argv, None


def _environment_blocker(
    method: str, writable: bool | None, externally_managed: bool | None
) -> str | None:
    """Why pip must not write to this environment, or None."""
    managed = _externally_managed() if externally_managed is None else externally_managed
    if method == SYSTEM and managed:
        return "this Python is managed by the system"
    if not (_site_writable() if writable is None else writable):
        return "this user cannot write to the Python environment"
    return None


def missing_message(extra: str, what: str) -> str:
    """One line: *what* needs *extra*, and the exact command that installs it here."""
    return (
        f"{what} needs the repowise[{extra}] extra, which is not installed. "
        f"Install it with: {plan_install(extra).command}"
    )


def require(extra: str, what: str) -> None:
    """Raise :class:`MissingExtraError` naming the fix when *extra* is missing."""
    if not is_installed(extra):
        plan = plan_install(extra)
        raise MissingExtraError(missing_message(extra, what), extra=extra, command=plan.command)


def install(extra: str, *, run=subprocess.run, plan: InstallPlan | None = None) -> InstallPlan:
    """Install *extra* into this environment, then import it to prove it worked.

    Callers confirm with the user first; this never asks. Raises
    :class:`ExtraInstallError` for a print-only plan, a failed command, or an
    extra that still does not import.
    """
    plan = plan or plan_install(extra)
    if not plan.runnable:
        raise ExtraInstallError(f"Not run here ({plan.reason}). Run it yourself: {plan.command}")
    try:
        result = run(list(plan.argv), capture_output=True, text=True, check=False)
    except OSError as exc:
        raise ExtraInstallError(f"Could not run {plan.command}: {exc}") from exc
    if result.returncode != 0:
        tail = "\n".join((result.stderr or result.stdout or "").strip().splitlines()[-5:])
        raise ExtraInstallError(
            f"{plan.command} exited with {result.returncode}" + (f":\n{tail}" if tail else "")
        )
    importlib.invalidate_caches()
    for module in _get(extra).modules:
        try:
            importlib.import_module(module)
        except ImportError as exc:
            raise ExtraInstallError(
                f"{plan.command} finished, but {module} still does not import: {exc}"
            ) from exc
    return plan


_PLAIN = re.compile(r"[\w@%+=:./\\-]+")


def _display(argv: tuple[str, ...]) -> str:
    """A command a user can paste into cmd, PowerShell or a POSIX shell.

    Requirement strings carry ``<`` and ``>``, which every shell reads as a
    redirect, so anything outside a plain word is double-quoted.
    """
    return " ".join(arg if _PLAIN.fullmatch(arg) else f'"{arg}"' for arg in argv)
