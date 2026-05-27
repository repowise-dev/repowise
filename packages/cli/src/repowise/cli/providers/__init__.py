"""Shared CLI provider/store construction helpers.

These collect the embedder / vector-store / cost-tracker construction logic that
was previously copy-pasted across ``init_cmd`` and ``update_cmd`` so every CLI
command builds them the same way.
"""

from __future__ import annotations

from repowise.cli.providers.cost_tracking import build_cost_tracker, make_cost_tracker
from repowise.cli.providers.embedders import build_embedder, resolve_embedder
from repowise.cli.providers.vector_store import build_vector_store

__all__ = [
    "build_cost_tracker",
    "build_embedder",
    "build_vector_store",
    "make_cost_tracker",
    "resolve_embedder",
]
