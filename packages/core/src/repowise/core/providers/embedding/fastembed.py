"""In-process local embeddings via fastembed (ONNX): no server to run, no key.

Optional: ``pip install "repowise[local-embed]"``. The model (default
``BAAI/bge-small-en-v1.5``, 384 dims, ~65 MB) downloads once on first use to
``FASTEMBED_CACHE_PATH`` or ``~/.repowise/models``. For air-gapped machines,
copy that folder across and set ``HF_HUB_OFFLINE=1``.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any

# Module level on purpose: the registry imports this module only when the
# embedder is asked for, so a missing extra surfaces there as its install hint
# and a base install never needs fastembed.
import fastembed

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
# Widths of the fastembed models worth naming; any other model is asked once.
_DIMS = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "nomic-ai/nomic-embed-text-v1.5": 768,
    "jinaai/jina-embeddings-v2-base-code": 768,
}


def _cache_dir() -> str:
    return os.environ.get("FASTEMBED_CACHE_PATH") or str(Path.home() / ".repowise" / "models")


class FastEmbedEmbedder:
    """Embedder protocol over ``fastembed.TextEmbedding``, loaded on first use.

    Args:
        model: A fastembed model name. Defaults to ``BAAI/bge-small-en-v1.5``.
        threads: ONNX runtime threads; fastembed's default when unset.
    """

    def __init__(self, model: str | None = None, threads: int | None = None, **_: Any) -> None:
        self._model_name = model or _DEFAULT_MODEL
        self._threads = threads
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def dimensions(self) -> int:
        dims = _DIMS.get(self._model_name)
        if dims is None:
            dims = len(next(iter(self._load().query_embed(["x"]))))
            _DIMS[self._model_name] = dims
        return dims

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                self._model = fastembed.TextEmbedding(
                    self._model_name, cache_dir=_cache_dir(), threads=self._threads
                )
        return self._model

    def _embed_sync(self, texts: list[str], kind: str) -> list[list[float]]:
        model = self._load()
        # Each side through its own call, so a fastembed model that frames
        # queries differently does it itself. bge-small embeds both as they are.
        vectors = model.query_embed(texts) if kind == "query" else model.passage_embed(texts)
        out = []
        for vector in vectors:
            values = [float(x) for x in vector]
            # The protocol promises unit length; not every fastembed model normalizes.
            norm = sum(x * x for x in values) ** 0.5 or 1.0
            out.append([x / norm for x in values])
        return out

    async def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        if not texts:
            return []
        # ONNX inference is CPU-bound: off the event loop, so a server keeps serving.
        return await asyncio.to_thread(self._embed_sync, list(texts), kind)
