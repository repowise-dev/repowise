"""In-process local embeddings via fastembed (ONNX): no server to run, no key.

Optional: ``pip install "repowise[local-embed]"`` (``pip install fastembed``
on Python 3.13+). The model (default
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


# (model, cache dir) -> why it could not load, so a run that cannot get the
# model tries once, not once per embed call. A process that started offline
# keeps failing until it restarts.
_LOAD_FAILURES: dict[tuple[str, str], str] = {}
# (model, cache dir, threads) -> the loaded model: init builds its embedder
# twice, and each ONNX session holds about 190 MB.
_MODELS: dict[tuple[str, str, int | None], Any] = {}


def _download_once(model_name: str, cache_dir: str) -> bool:
    """Download *model_name* with a single try; False if fastembed's API moved.

    ``TextEmbedding`` always makes three tries and sleeps 3 + 9 + 27 s between
    them, so an offline first run waited 39 s before it could fall back. A
    failed download is tried again on the next run. Goes through fastembed's
    model classes, which are not public API: when their shape changes this
    returns False and the caller downloads the usual way, retries included.
    """
    for model_class in getattr(fastembed.TextEmbedding, "EMBEDDINGS_REGISTRY", ()):
        try:
            description = model_class._get_model_description(model_name)
        except ValueError:
            continue  # another class serves this model
        except (AttributeError, TypeError, NotImplementedError):
            return False
        try:
            model_class.download_model(description, cache_dir, retries=1)
        except (AttributeError, TypeError):
            return False  # its signature moved
        return True
    return False


def _quiet_fastembed() -> Any:
    """fastembed logs every failed source and retry as an ERROR; the caller says it once."""
    try:
        from loguru import logger
    except ImportError:  # pragma: no cover - fastembed depends on loguru
        return lambda: None
    logger.disable("fastembed")
    return lambda: logger.enable("fastembed")


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
            dims = len(next(iter(self.load().query_embed(["x"]))))
            _DIMS[self._model_name] = dims
        return dims

    def load(self) -> Any:
        """Load the model now, downloading it on first use.

        Raises:
            RuntimeError: The model could not be loaded (or downloaded). Raised
                again at once for the same model and cache in this process.
        """
        with self._lock:
            if self._model is None:
                cache_dir = _cache_dir()
                key = (self._model_name, cache_dir)
                loaded_key = (*key, self._threads)
                self._model = _MODELS.get(loaded_key)
                if self._model is not None:
                    return self._model
                failure = _LOAD_FAILURES.get(key)
                if failure is not None:
                    raise RuntimeError(failure)
                restore_logging = _quiet_fastembed()
                try:
                    self._model = _MODELS[loaded_key] = self._open(cache_dir)
                except Exception as exc:
                    failure = (
                        f"fastembed could not load {self._model_name} from {cache_dir} "
                        f"or download it: {exc}"
                    )
                    _LOAD_FAILURES[key] = failure
                    raise RuntimeError(failure) from exc
                finally:
                    restore_logging()
        return self._model

    def _open(self, cache_dir: str) -> Any:
        """The cached model with no network call, else download it and open that."""

        def model(**kwargs: Any) -> Any:
            return fastembed.TextEmbedding(
                self._model_name, cache_dir=cache_dir, threads=self._threads, **kwargs
            )

        try:
            return model(local_files_only=True)
        except Exception:
            pass  # not cached yet
        if _download_once(self._model_name, cache_dir):
            return model(local_files_only=True)
        return model()

    def _embed_sync(self, texts: list[str], kind: str) -> list[list[float]]:
        model = self.load()
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
