"""Coarse, non-identifying environment facts attached to every event.

Deliberately low-resolution: OS family, CPU architecture, a major.minor Python
version, and an automated-run boolean. Nothing here can identify a machine or a
person — no hostname, no username, no full version string, no IP (the server
never records the source address).
"""

from __future__ import annotations

import os
import platform

from repowise.core.ci.base import CI_ENV_VARS as _CI_ENV_VARS
from repowise.core.platform.telemetry import pytest_in_env


def os_family() -> str:
    """Return a coarse OS family: ``darwin`` / ``linux`` / ``windows``."""
    return platform.system().lower() or "unknown"


def arch() -> str:
    """Return the CPU architecture (e.g. ``arm64``, ``x86_64``)."""
    return platform.machine().lower() or "unknown"


def python_version() -> str:
    """Return only ``major.minor`` (e.g. ``3.12``) — never the patch level."""
    info = platform.python_version_tuple()
    return f"{info[0]}.{info[1]}"


def under_pytest() -> bool:
    """Return whether a pytest session is in progress, judged from the env.

    Read from the environment rather than ``sys.modules`` so the answer survives
    a subprocess boundary. Shared with the core emitter so the two cannot drift.
    """
    return pytest_in_env()


def is_ci() -> bool:
    """Return whether this looks like an automated rather than a human run.

    Counts a pytest session as automated. Test fixtures relocate the home
    directory that holds ``anon_id``, so every run would otherwise register as
    a brand new install whose usage is not a user's, which is the same reason
    CI is flagged.
    """
    return under_pytest() or any(os.environ.get(var) for var in _CI_ENV_VARS)


def base_facts() -> dict[str, object]:
    """Return the environment facts common to every telemetry envelope."""
    return {
        "os": os_family(),
        "arch": arch(),
        "python_version": python_version(),
        "is_ci": is_ci(),
    }


def client() -> str:
    """Return the host that launched this process, as one fixed label.

    See :func:`repowise.core.platform.telemetry.detect_client` for the labels.
    A command that knows better (a hook handed ``--client``) overrides it
    through :func:`..add_command_outcome`.
    """
    from repowise.core.platform.telemetry import detect_client

    return detect_client()
