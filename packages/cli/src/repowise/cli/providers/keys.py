"""The embedder credential every CLI command needs.

Resolution is shared with the HTTP and MCP servers in
:mod:`repowise.core.providers.embedding.resolve`: process env > the repo's
``.repowise/.env`` > ``~/.repowise/config.yaml``'s ``embedder_api_key``. These
names stay importable for the CLI's callers. Nothing here mutates
``os.environ``: a workspace resolves several repos in one process, and a merge
would let the first repo's key answer for every sibling.
"""

from __future__ import annotations

from typing import Any

from repowise.core.providers.embedding.resolve import (
    EMBEDDER_KEY_ENVS,
    KeyLookup,
    global_config_embedder,
    resolve_embedder_key,
)


def embedder_key_env_vars(embedder_name: str) -> tuple[str, ...]:
    """Env vars carrying *embedder_name*'s credential; empty when it needs none."""
    return EMBEDDER_KEY_ENVS.get(embedder_name, ())


def resolve_embedder_api_key(embedder_name: str, repo_path: Any = None) -> KeyLookup:
    """Find *embedder_name*'s API key, in the order the servers use."""
    return resolve_embedder_key(embedder_name, repo_path)


__all__ = [
    "KeyLookup",
    "embedder_key_env_vars",
    "global_config_embedder",
    "resolve_embedder_api_key",
]
