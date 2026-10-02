"""The opt-in that shows what the finding registry holds back by default."""

from __future__ import annotations

from fastapi import Query

UnverifiedQuery = Query(
    False,
    description=(
        "Also return finding types and per-language layers measured below the "
        "precision bar. Without it, a response counts what it held back in `gated`."
    ),
)
