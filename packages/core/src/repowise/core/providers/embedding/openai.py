"""OpenAI embedding support for repowise semantic search.

Uses the openai SDK with text-embedding-3-small by default (1536 dims).
Runs the synchronous SDK call in a thread pool to avoid blocking asyncio.

Installation:
    pip install openai

Usage:
    import asyncio
    from repowise.core.providers.embedding.openai import OpenAIEmbedder
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    embedder = OpenAIEmbedder(api_key="sk-...")
    store = InMemoryVectorStore(embedder)
    await store.embed_and_upsert("page-1", "Some wiki content...", {})
    results = await store.search("auth service", limit=5)

Dimensions:
    text-embedding-3-small  → 1536 dims
    text-embedding-3-large  → 3072 dims
    text-embedding-ada-002  → 1536 dims

    REPOWISE_EMBEDDING_DIMS (or the ``dimensions`` arg) overrides the width and
    is passed to the API so the returned vectors match — for a model that
    supports a variable (Matryoshka) width.

    REPOWISE_EMBEDDING_DECLARED_DIMS (or the ``declared_dimensions`` arg)
    overrides the width *without* passing it to the API, for a model whose
    width is fixed and that rejects the ``dimensions`` parameter outright.
    See ``OpenAIEmbedder``.
"""

from __future__ import annotations

import asyncio
import math
import os
from typing import Any, ClassVar

from repowise.core.providers.embedding.base import resolve_embedding_timeout


class OpenAIEmbedder:
    """OpenAI embedding model adapter implementing the repowise Embedder protocol.

    Args:
        api_key: OpenAI API key. Falls back to OPENAI_API_KEY env var.
        model:   Embedding model name. Default: "text-embedding-3-small".
        base_url: Optional custom base URL for OpenAI-compatible endpoints.
        dimensions: Output width for a model not in ``_DIMS`` (e.g. a local
            OpenAI-compatible embedder). Falls back to REPOWISE_EMBEDDING_DIMS,
            then to ``declared_dimensions``, then to the known-model table,
            then 1536. Also sent to the API as the ``dimensions`` parameter,
            for a model that supports reshaping its output (Matryoshka); an
            endpoint that does not implement the parameter ignores it.
        declared_dimensions: Output width for a model whose width is fixed
            and that rejects the ``dimensions`` parameter — e.g. NVIDIA's
            Nemotron-3-Embed-1B, which 400s with "does not support Matryoshka
            embeddings; dimensions must be unset" if it's sent at all. Falls
            back to REPOWISE_EMBEDDING_DECLARED_DIMS. Unlike ``dimensions``,
            never sent to the API — it only tells this adapter, and therefore
            the vector store, what width to expect. Ignored when ``dimensions``
            (or REPOWISE_EMBEDDING_DIMS) is also set.
    """

    _DIMS: ClassVar[dict[str, int]] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    # Also bounds the query path, where the SDK retries twice — a bigger default
    # would triple into the caller's budget. Local endpoints raise it by env.
    _DEFAULT_TIMEOUT: float = 10.0

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        timeout: float | None = None,
        base_url: str | None = None,
        dimensions: int | None = None,
        declared_dimensions: int | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OpenAI API key required. Pass api_key= or set OPENAI_API_KEY env var."
            )
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self._model = model
        self._timeout = resolve_embedding_timeout(
            timeout, self._DEFAULT_TIMEOUT, provider_env="OPENAI_EMBEDDING_TIMEOUT"
        )
        # When the user overrides the width via `dimensions`, request that width
        # from the API too, so the returned vectors match the declaration instead
        # of the model's default — otherwise the store is sized to a width the
        # vectors never have. Servers that don't implement the parameter ignore
        # it (returning their native width, which the override then correctly
        # declares); one that can't honour it rejects the request loudly rather
        # than silently returning a mismatched width.
        #
        # `declared_dimensions` exists for the model that can't honour it and
        # doesn't reject it loudly either — it rejects the request outright,
        # because the parameter asks for reshaping (Matryoshka) support the
        # model doesn't have. There, the only way to tell repowise the real
        # width is to declare it without ever sending it. Neither knob hardcodes
        # a model name: the operator declares the width for whatever model
        # they're pointing at, and the endpoint decides what it accepts.
        self._dimensions, self._request_dimensions, self._dims_declared_only = (
            self._resolve_dimensions(dimensions, declared_dimensions, model)
        )
        self._client: object | None = None  # cached; created once on first embed()

    @classmethod
    def _resolve_dimensions(
        cls, dimensions: int | None, declared_dimensions: int | None, model: str
    ) -> tuple[int, int | None, bool]:
        """Resolve ``(declared_width, request_override, declared_only)``.

        ``request_override`` is the value to send the API as the ``dimensions``
        parameter, or ``None`` to keep the request byte-identical to the stock
        call. ``declared_only`` is True when the width came from
        ``declared_dimensions`` / ``REPOWISE_EMBEDDING_DECLARED_DIMS`` rather
        than from ``dimensions`` / ``REPOWISE_EMBEDDING_DIMS`` or the ``_DIMS``
        table — kept only so :meth:`embed`'s width-mismatch error can name the
        setting that actually chose the number.

        Precedence for the declared width: explicit ``dimensions=`` >
        ``REPOWISE_EMBEDDING_DIMS`` > explicit ``declared_dimensions=`` >
        ``REPOWISE_EMBEDDING_DECLARED_DIMS`` > the ``_DIMS`` table > 1536.
        The first two are also requested from the API; the last three never
        are, ``_DIMS`` because it is not a user override at all, and
        ``declared_dimensions`` because it exists specifically for a model
        that must never receive the parameter.

        A local OpenAI-compatible embedder (e.g. a self-hosted model) is not in
        ``_DIMS``; without a declared width its width would silently default to
        1536 and mismatch the store. Mirrors the Ollama/Gemini embedders, which
        already honour REPOWISE_EMBEDDING_DIMS.
        """
        if dimensions is None:
            env = os.environ.get("REPOWISE_EMBEDDING_DIMS")
            if env:
                dimensions = _parse_dimensions_env(env)
        if dimensions is not None:
            width = _validate_dimensions(dimensions)
            return width, width, False

        if declared_dimensions is None:
            env = os.environ.get("REPOWISE_EMBEDDING_DECLARED_DIMS")
            if env:
                declared_dimensions = _parse_dimensions_env(env)
        if declared_dimensions is not None:
            width = _validate_dimensions(declared_dimensions)
            return width, None, True

        return cls._DIMS.get(model, 1536), None, False

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using OpenAI.

        Runs the synchronous SDK call in a thread pool to avoid blocking the
        asyncio event loop.

        Args:
            texts: Non-empty list of strings to embed.

        Returns:
            List of L2-normalized float vectors.
        """
        if not texts:
            return []

        model = self._model
        timeout = self._timeout
        request_dimensions = self._request_dimensions
        expected_dimensions = self._dimensions
        dims_declared_only = self._dims_declared_only

        def _embed_sync() -> list[list[float]]:
            import openai  # type: ignore[import-untyped]

            # Cache client — create once with timeout, reuse across calls.
            if self._client is None:
                self._client = openai.OpenAI(
                    api_key=self._api_key,
                    timeout=timeout,
                    base_url=self._base_url,
                )
            create_kwargs: dict[str, Any] = {"model": model, "input": texts}
            if request_dimensions is not None:
                create_kwargs["dimensions"] = request_dimensions
            response = self._client.embeddings.create(**create_kwargs)  # type: ignore[union-attr]
            raw_vectors = [list(item.embedding) for item in response.data]
            widths = {len(v) for v in raw_vectors}
            if widths and widths != {expected_dimensions}:
                actual = min(widths - {expected_dimensions})
                if request_dimensions is not None:
                    hint = (
                        f"Set REPOWISE_EMBEDDING_DIMS={actual} to match the server's"
                        f" native output, or remove the override to use the model's default."
                    )
                elif dims_declared_only:
                    hint = (
                        f"The width {expected_dimensions} came from REPOWISE_EMBEDDING_DECLARED_DIMS"
                        f" (or declared_dimensions=), which is deliberately never sent as the API's"
                        f" 'dimensions' parameter. Set it to {actual} to match what the server"
                        f" actually returns, or — if this endpoint does accept the parameter after"
                        f" all — switch to REPOWISE_EMBEDDING_DIMS={actual} instead."
                    )
                else:
                    hint = (
                        f"The width {expected_dimensions} came from the built-in _DIMS table for"
                        f" {model!r}. Add or update OpenAIEmbedder._DIMS[{model!r}] = {actual},"
                        f" or set REPOWISE_EMBEDDING_DIMS={actual}."
                    )
                reason = (
                    "The endpoint likely ignored the 'dimensions' parameter."
                    if request_dimensions is not None
                    else "Nothing told this endpoint to produce that width — it never was."
                )
                raise ValueError(
                    f"OpenAIEmbedder declared {expected_dimensions}-dimensional vectors but the"
                    f" API returned {actual} (model={model!r}). {reason} {hint}"
                )
            return [_l2_normalize(v) for v in raw_vectors]

        return await asyncio.to_thread(_embed_sync)


def _validate_dimensions(value: int) -> int:
    """Raise the one message every bad width — explicit or from env — shares."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("dimensions must be a positive integer")
    return value


def _parse_dimensions_env(raw: str) -> int:
    """Parse an env var's dimensions value, or raise the same message a bad
    explicit ``dimensions=`` / ``declared_dimensions=`` does.
    """
    try:
        parsed = int(raw)
    except ValueError:
        raise ValueError("dimensions must be a positive integer") from None
    return _validate_dimensions(parsed)


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length (cosine similarity = dot product)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        norm = 1.0
    return [x / norm for x in vec]
