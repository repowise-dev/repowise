"""Provider resolution and cost figures for the pre-launch estimate endpoints.

Shared by ``POST /{repo_id}/generate/estimate`` and ``POST /{repo_id}/preflight``,
which both price a run with the repo's configured provider before any job
exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_provider(repo_path: str) -> tuple[Any, str | None, str | None, str | None]:
    """``(client, provider name, model name, error)`` for the repo's chat provider.

    A provider that fails to resolve is reported through ``error`` with a
    ``None`` client rather than raised: both callers still answer with a
    partial payload.
    """
    llm_client = None
    provider_name: str | None = None
    model_name: str | None = None
    provider_error: str | None = None
    try:
        from repowise.server.provider_config import get_chat_provider_instance

        llm_client = get_chat_provider_instance(repo_path=repo_path)
        provider_name = getattr(llm_client, "provider_name", None)
        model_name = getattr(llm_client, "model_name", None)
    except Exception as exc:
        provider_error = str(exc)
    return llm_client, provider_name, model_name, provider_error


async def probe_provider(repo_path: str) -> dict[str, Any]:
    """Live smoke test of the repo's provider (the same probe the CLI uses at init)."""
    llm_client, provider_name, model_name, provider_error = resolve_provider(repo_path)
    provider_ok = False
    if llm_client is not None:
        try:
            await llm_client.generate("You are a test.", "Reply with OK.", max_tokens=50)
            provider_ok = True
        except Exception as exc:
            provider_error = str(exc)
    return {
        "ok": provider_ok,
        "name": provider_name,
        "model": model_name,
        "error": provider_error,
    }


def count_files(repo_path: str, exclude_patterns: list[str] | None) -> int:
    """Files the indexer would walk. A fast traversal: nothing is parsed."""
    from repowise.core.ingestion import FileTraverser

    traverser = FileTraverser(
        Path(repo_path),
        extra_exclude_patterns=exclude_patterns or None,
    )
    return sum(1 for _ in traverser.traverse())


def cost_fields(est: Any) -> dict[str, Any]:
    """The cost figures of a ``CostEstimate``, rounded for the wire."""
    return {
        "estimated_cost_usd": round(est.estimated_cost_usd, 4),
        "cost_low_usd": round(est.cost_range.low, 4) if est.cost_range else None,
        "cost_high_usd": round(est.cost_range.high, 4) if est.cost_range else None,
        "estimated_input_tokens": est.estimated_input_tokens,
        "estimated_output_tokens": est.estimated_output_tokens,
        "is_calibrated": est.is_calibrated,
    }
