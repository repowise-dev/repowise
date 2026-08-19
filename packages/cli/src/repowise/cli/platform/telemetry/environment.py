"""Coarse, non-identifying environment facts attached to every event.

Deliberately low-resolution: OS family, CPU architecture, a major.minor Python
version, and an automated-run boolean. Nothing here can identify a machine or a
person — no hostname, no username, no full version string, no IP (the server
never records the source address).
"""

from __future__ import annotations

import os
import platform

# Common CI signals across providers. ``CI`` covers GitHub Actions, GitLab,
# CircleCI, Travis; the rest catch platforms that don't set a generic ``CI``.
_CI_ENV_VARS = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "BUILDKITE",
    "JENKINS_URL",
    "TEAMCITY_VERSION",
    "TF_BUILD",
)


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
    a subprocess boundary: the suite drives real ``repowise`` commands, and a
    child interpreter sees the inherited env vars but has its own module table.
    ``PYTEST_CURRENT_TEST`` covers the window while a test runs;
    ``PYTEST_VERSION`` (pytest 8.1+) also covers collection and teardown, when
    no test is current but an atexit flush can still fire.
    """
    return bool(os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("PYTEST_VERSION"))


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
