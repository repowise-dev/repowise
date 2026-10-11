"""Embedder protocol and implementations for repowise vector search.

The Embedder protocol is structural (runtime_checkable) so any object with
an `embed()` method and a `dimensions` property satisfies it without
inheriting from a base class.

MockEmbedder is the built-in test implementation: deterministic, 8-dimensional,
zero external dependencies.
"""

from __future__ import annotations

import hashlib
import math
import os
import sys
from typing import Protocol, runtime_checkable

import structlog

log = structlog.get_logger(__name__)


def resolve_embedding_timeout(
    explicit: float | None, default: float, *, provider_env: str | None = None
) -> float:
    """Seconds allowed per embedding request.

    Precedence: explicit arg > ``provider_env`` > ``REPOWISE_EMBEDDING_TIMEOUT``
    > *default*. Raisable because against a local endpoint one request is GPU
    work, and an expired batch is not retried — it surfaces only as "N/N items
    failed to embed".

    A malformed *env* value warns and falls back; raising would reach
    ``build_embedder``'s ``except Exception`` and downgrade the run to a keyless
    8-wide index. An invalid *explicit* argument is a caller bug and raises.
    """
    if explicit is not None:
        if isinstance(explicit, bool) or not isinstance(explicit, int | float):
            raise ValueError("timeout must be a positive number")
        if not math.isfinite(explicit) or explicit <= 0:
            raise ValueError("timeout must be a positive number")
        return float(explicit)
    names = [provider_env] if provider_env else []
    names.append("REPOWISE_EMBEDDING_TIMEOUT")
    invalid: list[tuple[str, str]] = []
    selected = default
    for name in names:
        raw = os.environ.get(name)
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            value = math.nan
        # isfinite also rejects "inf" — a plausible way to ask for no limit, and
        # the one value that hangs a stalled endpoint forever.
        if math.isfinite(value) and value > 0:
            selected = value
            break
        # A value that failed to parse is not a value, so it must not consume
        # the narrower variable's precedence over the shared one.
        invalid.append((name, raw))
    for name, raw in invalid:
        _report_invalid_timeout(name, raw, selected)
    return selected


def _report_invalid_timeout(var: str, raw: str, default: float) -> None:
    """Say it where the user will actually see it.

    The CLI pins structlog to ERROR unless ``-v``, so a warning here reaches
    nobody on the path that matters — and the symptom this variable exists to
    cure ("N/N items failed to embed") is exactly what a silently-ignored value
    produces. stderr matches how ``build_embedder`` reports the same class of
    misconfiguration.
    """
    log.warning("embedding_timeout_invalid", var=var, value=raw, using=default)
    print(
        f"{var}={raw!r} is not a positive number of seconds; using {default}s.",
        file=sys.stderr,
    )


@runtime_checkable
class Embedder(Protocol):
    """Structural protocol for text embedding models.

    All implementations must produce unit-length (L2-normalized) vectors so
    that cosine similarity equals the dot product — important for InMemoryVectorStore
    and pgvector's <=> operator (which uses cosine distance by default).
    """

    @property
    def dimensions(self) -> int:
        """Number of dimensions in the embedding vector."""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts.

        Args:
            texts: Non-empty list of strings to embed.

        Returns:
            List of unit-length float vectors, one per input string.
            Each vector has exactly ``self.dimensions`` elements.
        """
        ...


class MockEmbedder:
    """Deterministic 8-dimensional embedder for testing.

    Uses the first 8 bytes of SHA-256(text) interpreted as 8 unsigned bytes,
    divided by 255.0, then L2-normalised to unit length.

    Properties:
    - Deterministic: same input always produces the same vector.
    - Different texts virtually always produce different vectors (hash collision
      probability negligible for test inputs).
    - All output vectors have unit L2 norm (cosine similarity = dot product).
    - Zero external dependencies.
    """

    dimensions: int = 8

    async def embed(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            # Take first 8 bytes as raw floats via struct unpack
            # (4 bytes each → 2 float32 values would only give 2 dims)
            # Instead: use 8 unsigned bytes normalised to [0, 1]
            raw = [digest[i] / 255.0 for i in range(8)]
            norm = math.sqrt(sum(x * x for x in raw))
            if norm == 0.0:
                norm = 1.0
            results.append([x / norm for x in raw])
        return results


class KeylessEmbedder(MockEmbedder):
    """What an index built without an API key embeds with.

    Identical arithmetic to :class:`MockEmbedder`, and a separate type on
    purpose. The two produce the same vectors and mean opposite things:

    - In a **test**, ``MockEmbedder`` stands in for a real embedder. The suite
      wants the vector leg to run, and controls what comes back by seeding the
      store or scripting ``search``, so the vectors themselves are incidental.
    - In **production**, this class is what you get when nobody supplied a key.
      Its vectors are the only ones there are, they carry no signal, and the
      vector leg must therefore not run at all.

    One class cannot be told apart at the point that decision is made, which is
    the whole reason this one exists. Nothing else differs, and subclassing
    keeps ``isinstance(x, MockEmbedder)`` true for the width and dimension
    checks that already depend on it.

    Why the vectors carry no signal: every component is non-negative, so they
    all lie in the positive orthant of an 8-dimensional space and no two can
    point meaningfully different ways. Measured over 2,000 texts, two
    *unrelated* strings score 0.750 cosine on average, never below 0.207, and
    the nearest neighbours of any query sit at 0.926-0.962. They are distinct,
    which is what the base class promises; they are not discriminative, which
    is what a retriever needs.
    """


def is_semantic_embedder(embedder: object) -> bool:
    """Whether *embedder* produces vectors worth ranking on.

    False for :class:`KeylessEmbedder` and for nothing else. **Deliberately
    fails open**: anything unrecognised, including ``None``, counts as semantic.

    The asymmetry is the point. This predicate exists to switch off a leg that
    is actively harmful in one identifiable configuration, so it should refuse
    only what it can positively identify. Failing closed would mean any store
    this cannot introspect loses semantic search with no error and no log line,
    which is a far worse failure than the one being fixed: it would hit custom
    and out-of-tree stores that hold a perfectly good embedder somewhere other
    than the attribute below.
    """
    return not isinstance(embedder, KeylessEmbedder)


def store_has_semantic_vectors(store: object) -> bool:
    """Whether *store*'s vectors carry signal, i.e. whether to run its vector leg.

    Reads the embedder off the store rather than off any module-level server
    state, because workspace mode builds one context per repo through its own
    registry and factory (``core/workspace/registry.py``), so there is no single
    global that describes them all.

    Every concrete store takes an embedder as a required constructor argument
    and keeps it on ``_embedder``, so in this repo the attribute is always
    there. A store without one still returns True, per the fail-open rule in
    :func:`is_semantic_embedder`; only ``None`` is False, and that means "no
    store", not "a store that cannot rank".
    """
    if store is None:
        return False
    return is_semantic_embedder(getattr(store, "_embedder", None))
