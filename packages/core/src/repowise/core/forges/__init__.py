"""Git hosts (forges): remote parsing, URL builders, change refs and identity.

Every forge-specific fact (hosts, URL shapes, noreply domains, bot names,
merge-message conventions) lives in that forge's module here; callers ask
this package instead of matching hosts themselves. Adding a forge is one
module that subclasses ``BaseForge`` and calls ``register``.
"""

from __future__ import annotations

# Forge modules register themselves on import. Their host claims are
# disjoint, so the order does not matter; generic claims none and is the fallback.
from . import azure, bitbucket, generic, github, gitlab  # noqa: F401
from .base import BaseForge, CiSystem, Forge, ForgeKind, RemoteRef
from .changes import CHANGE_BODY_MARKERS, change_number, change_refs
from .detect import detect_forge, read_remote_url
from .identity import canonical_email, is_bot, noreply_login, normalize_identity
from .registry import HOSTS_ENV_VAR, all_forges, forge_hosts, get_forge, register
from .remote import canonical_key, parse_remote, strip_credentials

__all__ = [
    "CHANGE_BODY_MARKERS",
    "HOSTS_ENV_VAR",
    "BaseForge",
    "CiSystem",
    "Forge",
    "ForgeKind",
    "RemoteRef",
    "all_forges",
    "canonical_email",
    "canonical_key",
    "change_number",
    "change_refs",
    "detect_forge",
    "forge_hosts",
    "get_forge",
    "is_bot",
    "noreply_login",
    "normalize_identity",
    "parse_remote",
    "read_remote_url",
    "register",
    "strip_credentials",
]
