"""Precedent: dated things that happened to this code, bound to the code.

The episode store (:mod:`repowise.core.precedent.store`) is deliberately
stdlib-only so hook-time readers can open it without paying for the core
import graph. Derivation lives beside it, one module per tier.
"""

from __future__ import annotations

from .store import Episode, EpisodeStore, default_store_path

__all__ = ["Episode", "EpisodeStore", "default_store_path"]
