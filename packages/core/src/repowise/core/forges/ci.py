"""Which CI system runs this process, and what it says about the change.

Each CI's variable names live with its forge (``BaseForge.ci``); a CI that is
no forge's own, like Jenkins, is listed here. Everything is a pure read of an
env mapping: no git, no network.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from .base import CiSystem, ForgeKind, looks_like_sha
from .registry import all_forges, forge_hosts
from .remote import parse_remote, strip_credentials


@dataclass(frozen=True, slots=True)
class CiContext:
    """One CI run, as its env vars describe it. ``None`` where a fact is unset."""

    ci: str | None  # "github_actions" | "gitlab_ci" | "azure_pipelines" | ... | None
    forge: ForgeKind | None
    base_branch: str | None  # refs/heads/ already stripped
    base_sha: str | None
    head_sha: str | None
    change_number: int | None
    repo_url: str | None  # credentials stripped


_NO_CI = CiContext(None, None, None, None, None, None, None)

_JENKINS = CiSystem(
    name="jenkins",
    markers=("JENKINS_URL",),
    base_branch=("CHANGE_TARGET",),  # multibranch pull requests
    head_sha=("GIT_COMMIT",),
    change_number=("CHANGE_ID",),
    repo_url=(("GIT_URL",),),
)

#: CIs that belong to no forge, tried after the forges' own.
_STANDALONE = (_JENKINS,)

# A bare number, or GitHub's ``refs/pull/N/merge``. Nine digits keeps the
# value an ordinary int and rejects GitHub's internal PR ids that Azure
# Pipelines reports for GitHub repos.
_CHANGE_RE = re.compile(r"(?:refs/pull/)?([0-9]{1,9})(?:/merge|/head)?")
_HEADS = "refs/heads/"


def ci_systems() -> Iterator[tuple[ForgeKind | None, CiSystem]]:
    """Every known CI with the forge it belongs to, in detection order."""
    for forge in all_forges():
        if forge.ci is not None:
            yield forge.kind, forge.ci
    for system in _STANDALONE:
        yield None, system


def _first(env: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        if value := (env.get(name) or "").strip():
            return value
    return None


def read_base_branch(system: CiSystem, env: Mapping[str, str]) -> str | None:
    value = _first(env, system.base_branch)
    if value is None:
        return None
    return value.removeprefix(_HEADS) or None


def _sha(names: tuple[str, ...], env: Mapping[str, str]) -> str | None:
    value = _first(env, names)
    return value if value and looks_like_sha(value) else None


def _change_number(system: CiSystem, env: Mapping[str, str]) -> int | None:
    value = _first(env, system.change_number)
    match = _CHANGE_RE.fullmatch(value) if value else None
    number = int(match.group(1)) if match else 0
    return number or None


def _repo_url(system: CiSystem, env: Mapping[str, str]) -> str | None:
    for group in system.repo_url:
        values = [(env.get(name) or "").strip().strip("/") for name in group]
        if all(values):
            return strip_credentials("/".join(values)) or None
    return None


def _forge(
    system: CiSystem, owner: ForgeKind | None, env: Mapping[str, str], repo_url: str | None
) -> ForgeKind | None:
    """The forge hosting the repo: the provider var, then the CI's own forge,
    then the repo URL (with any ``REPOWISE_FORGE_HOSTS`` override)."""
    if system.provider_var:
        provider = (env.get(system.provider_var) or "").strip().lower()
        kind = next((k for name, k in system.providers if name == provider), None)
        if kind is not None:
            return kind
    elif owner is not None:
        return owner
    ref = parse_remote(repo_url, hosts=forge_hosts(None, env)) if repo_url else None
    return ref.forge if ref else None


def _read(system: CiSystem, owner: ForgeKind | None, env: Mapping[str, str]) -> CiContext:
    """*system*'s facts from *env*, whether or not its markers are set."""
    repo_url = _repo_url(system, env)
    return CiContext(
        ci=system.name,
        forge=_forge(system, owner, env, repo_url),
        base_branch=read_base_branch(system, env),
        base_sha=_sha(system.base_sha, env),
        head_sha=_sha(system.head_sha, env),
        change_number=_change_number(system, env),
        repo_url=repo_url,
    )


def detect_ci(env: Mapping[str, str] | None = None) -> CiContext:
    """The CI this runs in, from *env* (``os.environ`` when ``None``); all ``None`` outside one."""
    env = os.environ if env is None else env
    for owner, system in ci_systems():
        if system.active(env):
            return _read(system, owner, env)
    return _NO_CI
