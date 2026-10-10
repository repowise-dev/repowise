"""Registered forges, and which one a host belongs to.

A host resolves in this order: an explicit override (``forges.hosts`` in the
repo's ``.repowise/config.yaml``, then ``REPOWISE_FORGE_HOSTS``), then a
forge claiming the host by name, else generic. Nothing is probed over the
network, so a self-managed host with a neutral name needs an override.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from functools import lru_cache
from pathlib import Path

from .base import BaseForge, ForgeKind, split_host_port

#: ``git.corp.com=gitlab,tfs.corp=azure``: CI sets this where no config is committed.
HOSTS_ENV_VAR = "REPOWISE_FORGE_HOSTS"

_FORGES: dict[ForgeKind, BaseForge] = {}


def register(forge: BaseForge) -> None:
    """Add or replace a forge. Registration order is host-claim order."""
    _FORGES[forge.kind] = forge
    _claimed.cache_clear()
    from .identity import clear_caches  # identity reads this registry

    clear_caches()


def all_forges() -> Iterator[BaseForge]:
    return iter(_FORGES.values())


def get_forge(kind: ForgeKind | str) -> BaseForge:
    return _FORGES[ForgeKind(kind)]


@lru_cache(maxsize=1024)
def _claimed(host: str) -> BaseForge:
    for forge in _FORGES.values():
        if forge.claims_host(host):
            return forge
    return _FORGES[ForgeKind.GENERIC]


def forge_for_host(host: str, hosts: Mapping[str, ForgeKind] | None = None) -> BaseForge:
    """The forge serving *host*: an override in *hosts* first, then the name."""
    if hosts:
        kind = hosts.get(host)
        if kind is None:
            kind = next((v for k, v in hosts.items() if host_key(k) == host), None)
        if kind is not None:
            return get_forge(kind)
    return _claimed(host)


def host_key(raw: str) -> str:
    """An override key as a parsed remote's host, ``""`` when it names none.

    ``Git.Corp.com:8443`` and ``https://git.corp.com/`` become ``git.corp.com``;
    ``[::1]:80`` and a bare ``::1`` become ``::1``.
    """
    authority = raw.strip().split("://", 1)[-1].split("/", 1)[0].rpartition("@")[2]
    if authority.count(":") > 1 and not authority.startswith("["):
        authority = f"[{authority}]"  # a bare IPv6 address
    hp = split_host_port(authority)
    return hp[0] if hp else ""


def _merge(out: dict[str, ForgeKind], items: Iterator[tuple[object, object]]) -> None:
    for raw_host, raw_kind in items:
        try:
            kind = ForgeKind(str(raw_kind).strip().lower())
        except ValueError:
            continue  # an unknown forge name is ignored, not fatal
        host = host_key(str(raw_host))
        if host:
            out[host] = kind


def forge_hosts(
    repo_root: Path | str | None = None, env: Mapping[str, str] | None = None
) -> dict[str, ForgeKind]:
    """Self-managed host overrides for a repo: its config, then the env on top.

    The env wins so a CI job can correct a committed mapping. A config that
    cannot be read contributes nothing rather than failing detection.
    """
    out: dict[str, ForgeKind] = {}
    if repo_root is not None:
        from ..repo_config import RepoConfigError, load_repo_config

        try:
            config = load_repo_config(repo_root)
        except (OSError, RepoConfigError):
            config = {}
        section = config.get("forges")
        hosts = section.get("hosts") if isinstance(section, dict) else None
        if isinstance(hosts, dict):
            _merge(out, iter(hosts.items()))
    raw = (os.environ if env is None else env).get(HOSTS_ENV_VAR, "")
    pairs = (item.partition("=") for item in raw.split(",") if "=" in item)
    _merge(out, ((h, k) for h, _, k in pairs))
    return out
