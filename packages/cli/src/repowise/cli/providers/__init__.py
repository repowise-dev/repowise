"""Shared CLI provider/store construction helpers.

These collect the embedder / vector-store / cost-tracker construction logic that
was previously copy-pasted across ``init_cmd`` and ``update_cmd`` so every CLI
command builds them the same way.
"""

from __future__ import annotations

from repowise.cli.providers.cost_tracking import (
    build_cost_tracker,
    cost_tracking_disabled,
    flush_cost_tracker,
    make_cost_tracker,
)
from repowise.cli.providers.embedders import (
    build_embedder,
    embedder_was_requested,
    pin_names_an_embedder,
    resolve_embedder,
    resolve_embedder_for_repo,
)
from repowise.cli.providers.vector_store import build_vector_store, existing_vector_dim

__all__ = [
    "build_cost_tracker",
    "build_embedder",
    "build_vector_store",
    "cost_tracking_disabled",
    "embedder_was_requested",
    "existing_vector_dim",
    "flush_cost_tracker",
    "make_cost_tracker",
    "pin_names_an_embedder",
    "resolve_embedder",
    "resolve_embedder_for_repo",
]
