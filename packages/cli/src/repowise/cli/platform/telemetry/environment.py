"""Coarse, non-identifying environment facts attached to every event.

Deliberately low-resolution: OS family, CPU architecture, a major.minor Python
version, and a CI boolean. Nothing here can identify a machine or a person — no
hostname, no username, no full version string, no IP (the server never records
the source address).
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


def is_ci() -> bool:
    """Return whether we appear to be running in a CI environment."""
    return any(os.environ.get(var) for var in _CI_ENV_VARS)


def base_facts() -> dict[str, object]:
    """Return the environment facts common to every telemetry envelope."""
    return {
        "os": os_family(),
        "arch": arch(),
        "python_version": python_version(),
        "is_ci": is_ci(),
    }
