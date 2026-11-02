"""The opt-in that shows what the finding registry holds back by default."""

from __future__ import annotations

from fastapi import Query

UnverifiedQuery = Query(
    False,
    description=(
        "Also return provisional finding types and the per-language layers held back "
        "pending verification. Without it, a response counts what it held back in `gated`."
    ),
)
