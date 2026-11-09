"""OpenRouter embedding support for repowise semantic search.

Uses the OpenAI-compatible endpoint at ``https://openrouter.ai/api/v1``.
No additional pip install required — uses the ``openai`` package.

Default model: google/gemini-embedding-001 (768 dims)

Model discovery: a model is no longer rejected merely because it is absent
from the built-in ``_DIMS`` table. For a model the table does not know, the
adapter asks OpenRouter's ``GET /models`` catalog (via the shared
``fetch_model_list`` helper) which models are embedding-capable, caches that
answer for ``_MODEL_CACHE_TTL_S`` per base URL, and accepts the configured
model as an opaque OpenRouter identifier when the catalog says it can embed.
When the catalog cannot be reached, the ``_DIMS`` table is the deterministic
fallback; a model in neither place is still accepted, with the width left
unknown until the first embed returns — the API itself is the final authority
on whether the model exists, and the returned vector length is authoritative
for the width. Set ``REPOWISE_OPENROUTER_MODEL_DISCOVERY=0`` to skip the
catalog entirely (offline use / private OpenRouter-compatible endpoints);
then an unknown model needs an explicit width via ``dimensions`` /
``REPOWISE_EMBEDDING_DIMS`` (sent to the API) or ``declared_dimensions`` /
``REPOWISE_EMBEDDING_DECLARED_DIMS`` (declared only, never sent).

Dimensions: ``dimensions`` (or ``REPOWISE_EMBEDDING_DIMS``) is both the
declared width and the ``dimensions`` parameter sent in the embeddings
request, so a Matryoshka model such as ``mongodb/voyage-code-4`` actually
returns the selected width (256/512/1024/2048; 1024 is its default). With no
width configured, no ``dimensions`` parameter is sent and the model's default
applies; the returned vector length then becomes the declared width.
``declared_dimensions`` (or ``REPOWISE_EMBEDDING_DECLARED_DIMS``) is the
declaration-only path for an endpoint that rejects the parameter outright.

Usage:
    from repowise.core.providers.embedding.openrouter import OpenRouterEmbedder

    embedder = OpenRouterEmbedder(api_key="sk-or-...")
    vectors = await embedder.embed(["some text"])
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
import time
from typing import Any, ClassVar

import structlog

from repowise.core.providers.embedding.base import client_for_kind, resolve_embedding_timeout
from repowise.core.providers.llm.openai_compat import fetch_model_list

log = structlog.get_logger(__name__)

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "google/gemini-embedding-001"

#: How long a fetched ``GET /models`` answer is trusted, per base URL.
#: One fetch per TTL per endpoint; a failed refresh keeps serving the stale
#: entry, so normal operation never depends on a live catalog request.
_MODEL_CACHE_TTL_S: float = 3600.0

#: base_url -> (fetched_at_monotonic, (embedding-capable ids, all listed ids)).
#: Bounded: one entry per base URL, and each entry is two id sets.
_MODEL_CACHE: dict[str, tuple[float, tuple[frozenset[str], frozenset[str]]]] = {}


def _is_embedding_capable(entry: dict[str, Any]) -> bool:
    """Whether one OpenRouter ``/models`` entry can serve embeddings.

    The catalog's capability metadata is the primary signal: an
    ``architecture.modality`` naming embeddings (``"text->text-embedding"``),
    an ``output_modalities`` entry naming embeddings, or a
    ``supported_parameters`` entry for ``dimensions`` (only embedding
    endpoints advertise it). A model id containing ``embed`` is the
    last-resort signal for catalogs that omit capability metadata (private
    OpenRouter-compatible endpoints included): it is how every embedding
    model OpenRouter lists today is named, and a false positive there is
    caught by the API itself at embed time.
    """
    architecture = entry.get("architecture")
    if isinstance(architecture, dict):
        modality = architecture.get("modality")
        if isinstance(modality, str) and "embedding" in modality:
            return True
        output_modalities = architecture.get("output_modalities")
        if isinstance(output_modalities, list) and any(
            isinstance(m, str) and "embedding" in m for m in output_modalities
        ):
            return True
    supported = entry.get("supported_parameters")
    if isinstance(supported, list) and "dimensions" in supported:
        return True
    model_id = entry.get("id")
    return isinstance(model_id, str) and "embed" in model_id.lower()


def _fetch_catalog(api_key: str, base_url: str) -> tuple[frozenset[str], frozenset[str]] | None:
    """``(embedding-capable ids, all listed ids)`` from ``GET /models``.

    ``None`` when the catalog cannot be reached: ``fetch_model_list`` already
    returns ``None`` on any transport/parse failure. An empty-but-reachable
    catalog is a real (empty) answer, not a failure.
    """
    data = fetch_model_list(api_key, base_url)
    if data is None:
        return None
    capable: set[str] = set()
    all_ids: set[str] = set()
    for raw in data:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        all_ids.add(raw["id"])
        if _is_embedding_capable(raw):
            capable.add(raw["id"])
    return frozenset(capable), frozenset(all_ids)


def _discover_models(api_key: str, base_url: str) -> tuple[frozenset[str], frozenset[str]] | None:
    """Cached catalog for *base_url*: ``(embedding-capable ids, all ids)``.

    One fetch per ``_MODEL_CACHE_TTL_S`` per base URL; a failed refresh keeps
    serving the stale entry, so a transient catalog outage never blocks
    construction. Returns ``None`` only when no entry was ever fetched.
    """
    now = time.monotonic()
    cached = _MODEL_CACHE.get(base_url)
    if cached is not None and now - cached[0] < _MODEL_CACHE_TTL_S:
        return cached[1]
    fresh = _fetch_catalog(api_key, base_url)
    if fresh is None:
        return cached[1] if cached is not None else None
    _MODEL_CACHE[base_url] = (now, fresh)
    return fresh


def clear_embedding_model_cache() -> None:
    """Drop the cached catalog (tests; a process that changes endpoint)."""
    _MODEL_CACHE.clear()


def _discovery_enabled(explicit: bool | None) -> bool:
    """Whether to consult the ``/models`` catalog for unknown models.

    Explicit arg wins; otherwise ``REPOWISE_OPENROUTER_MODEL_DISCOVERY``
    disables it (``0``/``false``/``off``/``no``). Default: enabled.
    """
    if explicit is not None:
        return explicit
    raw = os.environ.get("REPOWISE_OPENROUTER_MODEL_DISCOVERY", "").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _validate_dimensions(value: int) -> int:
    """Raise the one message every bad width — explicit or from env — shares."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("dimensions must be a positive integer")
    return value


def _parse_dimensions_env(raw: str) -> int:
    """Parse an env var's dimensions value, or raise the shared bad-width message."""
    try:
        parsed = int(raw)
    except ValueError:
        raise ValueError("dimensions must be a positive integer") from None
    return _validate_dimensions(parsed)


class OpenRouterEmbedder:
    """OpenRouter embedding adapter implementing the repowise Embedder protocol.

    Args:
        api_key:    OpenRouter API key. Falls back to OPENROUTER_API_KEY env var.
        model:      Embedding model name. Default: "google/gemini-embedding-001".
                    Any model the OpenRouter catalog lists as embedding-capable
                    is accepted as an opaque identifier — it need not appear in
                    ``_DIMS``.
        base_url:   OpenRouter API base URL. Falls back to OPENROUTER_BASE_URL,
                    then "https://openrouter.ai/api/v1". Override it to point
                    at a private OpenRouter-compatible endpoint.
        dimensions: Output width to request from the API as the ``dimensions``
                    parameter (e.g. a Matryoshka width such as 1024 for
                    ``mongodb/voyage-code-4``). Falls back to
                    REPOWISE_EMBEDDING_DIMS. Also the declared width the
                    vector store trusts. With neither set, the model's default
                    applies and the returned vector length is authoritative.
        declared_dimensions: Declared width that is never sent to the API, for
                    an endpoint that rejects the ``dimensions`` parameter
                    outright. Falls back to REPOWISE_EMBEDDING_DECLARED_DIMS.
                    Ignored when ``dimensions`` (or REPOWISE_EMBEDDING_DIMS)
                    is also set.
        discover_models: Consult OpenRouter's ``/models`` catalog to check an
                    unknown model. Falls back to
                    REPOWISE_OPENROUTER_MODEL_DISCOVERY; default True. Set
                    False for offline use or a private endpoint that does not
                    serve a catalog — then an unknown model needs an explicit
                    width (``dimensions`` or ``declared_dimensions``).
    """

    #: Deterministic fallback for the width of known models, used when the
    #: catalog is unavailable and no width was configured. Not a registry:
    #: unknown models are resolved through the catalog, not by adding rows.
    _DIMS: ClassVar[dict[str, int]] = {
        "google/gemini-embedding-001": 768,
        "openai/text-embedding-3-small": 1536,
        "openai/text-embedding-3-large": 3072,
    }

    _DEFAULT_TIMEOUT: float = 10.0

    def __init__(
        self,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        timeout: float | None = None,
        base_url: str | None = None,
        dimensions: int | None = None,
        declared_dimensions: int | None = None,
        *,
        discover_models: bool | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not resolved_key:
            raise ValueError(
                "OpenRouter API key required. Pass api_key= or set OPENROUTER_API_KEY env var."
            )
        self._api_key = resolved_key
        self._model = model
        self._base_url = (
            base_url or os.environ.get("OPENROUTER_BASE_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout = resolve_embedding_timeout(
            timeout, self._DEFAULT_TIMEOUT, provider_env="OPENROUTER_EMBEDDING_TIMEOUT"
        )
        discover = _discovery_enabled(discover_models)
        # Declared width, width to request, and whether the width is
        # declaration-only. Precedence: explicit dimensions >
        # REPOWISE_EMBEDDING_DIMS > explicit declared_dimensions >
        # REPOWISE_EMBEDDING_DECLARED_DIMS > the _DIMS table > the catalog
        # (capability only — it carries no width, so the width stays unknown
        # until the first embed returns and becomes authoritative).
        self._dimensions, self._request_dimensions, self._dims_declared_only = (
            self._resolve_dimensions(dimensions, declared_dimensions, discover, resolved_key)
        )
        self._client: object | None = None

    def _resolve_dimensions(
        self,
        dimensions: int | None,
        declared_dimensions: int | None,
        discover: bool,
        api_key: str,
    ) -> tuple[int | None, int | None, bool]:
        """Resolve ``(declared_width, request_width, declared_only)``.

        ``request_width`` is sent as the API's ``dimensions`` parameter;
        ``None`` keeps the request byte-identical to the stock call and lets
        the model default apply. ``declared_width`` is ``None`` only when the
        model is accepted opaquely with no configured width — the first
        embed's returned length then becomes the declared width.
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

        if self._model in self._DIMS:
            return self._DIMS[self._model], None, False

        return self._resolve_unknown_model(api_key, discover)

    def _resolve_unknown_model(self, api_key: str, discover: bool) -> tuple[None, None, bool]:
        """Accept or reject a model that is in no table and has no width.

        Discovery on: the catalog decides. A model the catalog lists as
        embedding-capable is accepted opaquely; one the catalog lists as
        something else is rejected with an actionable error; one the catalog
        does not list at all is accepted opaquely with a warning — the
        catalog may be stale, and the API is the final authority. A catalog
        that cannot be reached at all falls back the same way, so a catalog
        outage never blocks construction; the width guard in :meth:`embed`
        still protects the vector store.

        Discovery off: there is nothing left to resolve the width from, so
        the operator must declare one.
        """
        model = self._model
        if not discover:
            raise ValueError(
                f"Unknown embedding model {model!r} and no width configured. "
                "Set REPOWISE_EMBEDDING_DIMS=<width> to request that width from the API "
                "(or REPOWISE_EMBEDDING_DECLARED_DIMS=<width> to declare it without "
                "sending it), or re-enable model discovery "
                "(REPOWISE_OPENROUTER_MODEL_DISCOVERY=1) so the OpenRouter catalog can "
                f"verify {model!r}."
            )
        catalog = _discover_models(api_key, self._base_url)
        if catalog is not None:
            capable, all_ids = catalog
            if model in capable:
                return None, None, False
            if model in all_ids:
                raise ValueError(
                    f"Model {model!r} is not an embedding model according to the "
                    "OpenRouter catalog — it cannot serve embeddings. Pick an "
                    "embedding-capable model (for example "
                    "'google/gemini-embedding-001' or 'mongodb/voyage-code-4'), "
                    "or point at a different endpoint with OPENROUTER_BASE_URL."
                )
            log.warning(
                "openrouter.model_not_in_catalog",
                model=model,
                detail="accepting as an opaque identifier; the API will reject it if invalid",
            )
            print(
                f"Model {model!r} is not in the OpenRouter catalog; accepting it as an "
                "opaque identifier. If the model does not exist, the API will reject "
                "the first embed.",
                file=sys.stderr,
            )
        else:
            log.warning(
                "openrouter.catalog_unavailable",
                model=model,
                detail="accepting as an opaque identifier; width comes from the first response",
            )
            print(
                f"Could not reach the OpenRouter model catalog to verify {model!r}; "
                "accepting it as an opaque identifier. The first embed's returned "
                "vector length will set the width.",
                file=sys.stderr,
            )
        return None, None, False

    @property
    def dimensions(self) -> int | None:
        """Declared width, or ``None`` while the model default applies and the
        width is not known yet. ``None`` means the store cannot confirm stored
        vectors are current (it re-embeds instead of trusting them) until the
        first embed returns and the width becomes authoritative; set
        ``REPOWISE_EMBEDDING_DIMS`` to declare it up front.
        """
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
        base_url = self._base_url
        timeout = self._timeout
        request_dimensions = self._request_dimensions
        expected_dimensions = self._dimensions
        dims_declared_only = self._dims_declared_only

        def _embed_sync() -> list[list[float]]:
            import openai

            if self._client is None:
                self._client = openai.OpenAI(
                    api_key=self._api_key,
                    base_url=base_url,
                    timeout=timeout,
                )
            create_kwargs: dict[str, Any] = {"model": model, "input": texts}
            if request_dimensions is not None:
                create_kwargs["dimensions"] = request_dimensions
            client = client_for_kind(self._client, kind)
            response = client.embeddings.create(**create_kwargs)  # type: ignore[union-attr]
            raw_vectors = [list(item.embedding) for item in response.data]
            widths = {len(v) for v in raw_vectors}
            if expected_dimensions is None:
                # No width was configured and the model is not in _DIMS: the
                # returned length is authoritative. A batch that disagrees
                # with itself is a server error, not a width to learn.
                if len(widths) > 1:
                    raise ValueError(
                        f"OpenRouterEmbedder got mixed-width vectors in one batch "
                        f"({sorted(widths)}, model={model!r}). The server returned "
                        "inconsistent widths; set REPOWISE_EMBEDDING_DIMS to the "
                        "width you expect."
                    )
                if widths:
                    self._dimensions = widths.pop()
            elif widths and widths != {expected_dimensions}:
                actual = min(widths - {expected_dimensions})
                if request_dimensions is not None:
                    reason = "The endpoint likely ignored the 'dimensions' parameter."
                    hint = (
                        f"Set REPOWISE_EMBEDDING_DIMS={actual} to match the server's"
                        " native output, or remove the override to use the model's default."
                    )
                elif dims_declared_only:
                    reason = "Nothing told this endpoint to produce that width — it never was."
                    hint = (
                        f"The width {expected_dimensions} came from "
                        "REPOWISE_EMBEDDING_DECLARED_DIMS (or declared_dimensions=), "
                        "which is deliberately never sent as the API's 'dimensions' "
                        f"parameter. Set it to {actual} to match what the server "
                        "actually returns, or — if this endpoint does accept the "
                        "parameter after all — switch to "
                        f"REPOWISE_EMBEDDING_DIMS={actual} instead."
                    )
                else:
                    reason = "Nothing told this endpoint to produce that width — it never was."
                    hint = (
                        f"The width {expected_dimensions} came from the built-in _DIMS table for"
                        f" {model!r}. Set REPOWISE_EMBEDDING_DIMS={actual} to override it."
                    )
                raise ValueError(
                    f"OpenRouterEmbedder declared {expected_dimensions}-dimensional vectors but the"
                    f" API returned {actual} (model={model!r}). {reason} {hint}"
                )
            return [_l2_normalize(v) for v in raw_vectors]

        return await asyncio.to_thread(_embed_sync)


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        norm = 1.0
    return [x / norm for x in vec]
