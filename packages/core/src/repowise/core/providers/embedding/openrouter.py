"""OpenRouter embedding support for repowise semantic search.

Uses the OpenAI-compatible endpoint at ``https://openrouter.ai/api/v1``.
No additional pip install required — uses the ``openai`` package.

Default model: google/gemini-embedding-001 (768 dims)

Usage:
    from repowise.core.providers.embedding.openrouter import OpenRouterEmbedder

    embedder = OpenRouterEmbedder(api_key="sk-or-...")
    vectors = await embedder.embed(["some text"])
"""

from __future__ import annotations

import asyncio
import math
import os
from typing import Any, ClassVar

from repowise.core.providers.embedding.base import client_for_kind, resolve_embedding_timeout


class OpenRouterEmbedder:
    """OpenRouter embedding adapter implementing the repowise Embedder protocol.

    Args:
        api_key:    OpenRouter API key. Falls back to OPENROUTER_API_KEY env var.
        model:      Embedding model name. Default: "google/gemini-embedding-001".
        dimensions: Output width. Falls back to REPOWISE_EMBEDDING_DIMS, then
                    ``declared_dimensions``, then ``_DIMS``. Unless it came from
                    ``declared_dimensions``, the resolved width is both the width
                    the vector store trusts and the ``dimensions`` parameter sent
                    with every request, so the declaration and the server's output
                    cannot disagree. OpenRouter's own default for
                    ``google/gemini-embedding-001`` is 3072; without the parameter
                    the 768 declared below would fail on every batch.
        declared_dimensions: Output width for a model whose width is fixed and
                    that rejects the ``dimensions`` parameter. Falls back to
                    REPOWISE_EMBEDDING_DECLARED_DIMS. Never sent to the API: it
                    only tells this adapter, and so the vector store, what width
                    to expect. Ignored when ``dimensions`` (or
                    REPOWISE_EMBEDDING_DIMS) is also set. Mirrors the knob of the
                    same name on ``OpenAIEmbedder``.
    """

    _DIMS: ClassVar[dict[str, int]] = {
        "google/gemini-embedding-001": 768,
        "openai/text-embedding-3-small": 1536,
        "openai/text-embedding-3-large": 3072,
    }

    _DEFAULT_TIMEOUT: float = 10.0

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "google/gemini-embedding-001",
        timeout: float | None = None,
        dimensions: int | None = None,
        declared_dimensions: int | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OpenRouter API key required. Pass api_key= or set OPENROUTER_API_KEY env var."
            )
        if model not in self._DIMS:
            known = ", ".join(sorted(self._DIMS))
            raise ValueError(
                f"Unknown embedding model {model!r}. Stored vectors would be mis-sized "
                f"against the model's real output, silently corrupting the vector store. "
                f"Add {model!r} to OpenRouterEmbedder._DIMS with its correct dimension count, "
                f"or pick a known model: {known}."
            )
        self._model = model
        self._timeout = resolve_embedding_timeout(
            timeout, self._DEFAULT_TIMEOUT, provider_env="OPENROUTER_EMBEDDING_TIMEOUT"
        )
        self._dimensions, self._request_dimensions = self._resolve_dimensions(
            dimensions, declared_dimensions, model
        )
        self._client: object | None = None

    @classmethod
    def _resolve_dimensions(
        cls, dimensions: int | None, declared_dimensions: int | None, model: str
    ) -> tuple[int, int | None]:
        """Resolve ``(declared_width, request_dimensions)``.

        Precedence for the declared width: explicit ``dimensions=`` >
        ``REPOWISE_EMBEDDING_DIMS`` > explicit ``declared_dimensions=`` >
        ``REPOWISE_EMBEDDING_DECLARED_DIMS`` > the ``_DIMS`` table.

        ``request_dimensions`` is the ``dimensions`` parameter to send, or
        ``None`` to leave it off the request. Unlike ``OpenAIEmbedder``, the
        ``_DIMS`` width is sent too: OpenRouter serves the default model at a
        different width unless asked. Only a ``declared_dimensions`` width is
        never sent, because it exists for a model that refuses the parameter.
        """
        if dimensions is None:
            env = os.environ.get("REPOWISE_EMBEDDING_DIMS")
            if env:
                dimensions = _parse_dimensions_env(env)
        if dimensions is not None:
            width = _validate_dimensions(dimensions)
            return width, width

        if declared_dimensions is None:
            env = os.environ.get("REPOWISE_EMBEDDING_DECLARED_DIMS")
            if env:
                declared_dimensions = _parse_dimensions_env(env)
        if declared_dimensions is not None:
            return _validate_dimensions(declared_dimensions), None

        width = cls._DIMS[model]
        return width, width

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        """Embed a batch of texts using OpenRouter.

        Runs the synchronous SDK call in a thread pool to avoid blocking the
        asyncio event loop.

        Args:
            texts: Non-empty list of strings to embed.
            kind: Not used as a prefix: none of the models in ``_DIMS`` are
                configured with one. A ``"query"`` call skips SDK retries.
        """
        if not texts:
            return []

        model = self._model
        timeout = self._timeout
        expected_dimensions = self._dimensions
        request_dimensions = self._request_dimensions

        def _embed_sync() -> list[list[float]]:
            import openai

            if self._client is None:
                self._client = openai.OpenAI(
                    api_key=self._api_key,
                    base_url="https://openrouter.ai/api/v1",
                    timeout=timeout,
                )
            client = client_for_kind(self._client, kind)
            # Request the declared width rather than relying on the model's default:
            # OpenRouter serves gemini-embedding-001 at 3072 unless told otherwise.
            # A declared_dimensions width is left off: that model refuses the parameter.
            create_kwargs: dict[str, Any] = {"model": model, "input": texts}
            if request_dimensions is not None:
                create_kwargs["dimensions"] = request_dimensions
            response = client.embeddings.create(**create_kwargs)  # type: ignore[union-attr]
            raw_vectors = [list(item.embedding) for item in response.data]
            widths = {len(v) for v in raw_vectors}
            if widths and widths != {expected_dimensions}:
                actual = min(widths - {expected_dimensions})
                if request_dimensions is None:
                    hint = (
                        f"The width came from REPOWISE_EMBEDDING_DECLARED_DIMS (or"
                        f" declared_dimensions=), which is never sent to the API. Set it to"
                        f" {actual} to match the server's real output."
                    )
                else:
                    hint = (
                        f"The endpoint did not honour the 'dimensions' parameter. Update"
                        f" OpenRouterEmbedder._DIMS[{model!r}] = {actual}, or set"
                        f" REPOWISE_EMBEDDING_DECLARED_DIMS={actual} if the model's width is"
                        f" fixed."
                    )
                raise ValueError(
                    f"OpenRouterEmbedder declared {expected_dimensions}-dimensional vectors but"
                    f" the API returned {actual} (model={model!r}). {hint}"
                )
            return [_l2_normalize(v) for v in raw_vectors]

        return await asyncio.to_thread(_embed_sync)


def _validate_dimensions(value: int) -> int:
    """Raise the one message every bad width, explicit or from env, shares."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("dimensions must be a positive integer")
    return value


def _parse_dimensions_env(raw: str) -> int:
    try:
        parsed = int(raw)
    except ValueError:
        raise ValueError("dimensions must be a positive integer") from None
    return _validate_dimensions(parsed)


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        norm = 1.0
    return [x / norm for x in vec]
