"""The vocabulary for the agents repowise integrates with.

Core declares who an agent *is*; the other packages bind behaviour to it.
:mod:`repowise.cli.agent_targets` wires an agent up, ``cli.agent_adapters``
and :mod:`repowise.core.sessions.adapters` implement its hook and transcript
surfaces, and :mod:`repowise.core.savings` attributes accounting to it. Core is
the leaf package, so every one of those can read this and none of them could
have read a copy owned by one of the others.

Deliberately not the same thing as :mod:`repowise.core.registry`, which is a
set of plugin extension seams. This is a vocabulary.
"""

from __future__ import annotations

from repowise.core.agents.identity import (
    CLIENT_IDENTITY_MAPPING_VERSION,
    UNKNOWN_AGENT,
    AgentIdentity,
    all_identities,
    display_name_for,
    get_identity,
    identity_for_target_id,
    is_agent_slug,
    normalize_client_name,
    register_identity,
    resolve_client_identity,
    unregister_identity,
)

__all__ = [
    "CLIENT_IDENTITY_MAPPING_VERSION",
    "UNKNOWN_AGENT",
    "AgentIdentity",
    "all_identities",
    "display_name_for",
    "get_identity",
    "identity_for_target_id",
    "is_agent_slug",
    "normalize_client_name",
    "register_identity",
    "resolve_client_identity",
    "unregister_identity",
]
