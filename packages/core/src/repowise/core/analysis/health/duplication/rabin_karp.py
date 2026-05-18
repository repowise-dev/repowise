"""Rabin-Karp rolling hash over normalized token windows.

We compute a 64-bit rolling polynomial hash over fixed-size windows of
token ``kind`` strings. Equal hashes flag a candidate clone; the
verifier in ``detector.py`` then confirms by comparing the actual token
sequences (so we don't trust a hash collision alone).

The math:

    H(w_0..w_{n-1}) = sum_{i=0..n-1} h(w_i) * B^(n-1-i)  (mod M)

Roll forward by one position:

    H' = (H - h(w_0)*B^(n-1)) * B + h(w_{n-1+1})  (mod M)

We use Python ints (arbitrary precision) but pin both base and modulus
to 64-bit values so the math is portable and hash collisions are well
distributed. Modulus is a large prime under 2**63.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .tokenizer import Token

# Pinned constants — changing these invalidates any persisted hashes.
_BASE = 1_000_003
_MODULUS = 9_223_372_036_854_775_783  # largest prime < 2**63


def _token_hash(tok_kind: str) -> int:
    """Stable per-kind hash. Plain ``hash()`` is randomized by Python's
    hash seed — we need determinism across processes."""
    h = 1469598103934665603
    for ch in tok_kind.encode("utf-8"):
        h ^= ch
        h = (h * 1099511628211) % _MODULUS
    return h


@dataclass(frozen=True)
class WindowHash:
    """One rolling-hash window with origin file + line span."""

    file_path: str
    hash_value: int
    start_index: int  # token index of the window's first token
    start_line: int
    end_line: int


def rolling_hashes(
    file_path: str,
    tokens: list[Token],
    window: int,
) -> list[WindowHash]:
    """Compute rolling hashes for every window of length *window*.

    Returns an empty list when the file has fewer tokens than the
    window size — callers filter clone candidates by minimum size
    upstream so the empty-result case is well-defined.
    """
    n = len(tokens)
    if n < window or window <= 0:
        return []

    base_pow = pow(_BASE, window - 1, _MODULUS)
    kind_hashes = [_token_hash(t.kind) for t in tokens]

    h = 0
    for i in range(window):
        h = (h * _BASE + kind_hashes[i]) % _MODULUS

    out: list[WindowHash] = []
    out.append(
        WindowHash(
            file_path=file_path,
            hash_value=h,
            start_index=0,
            start_line=tokens[0].start_line,
            end_line=tokens[window - 1].end_line,
        )
    )
    for i in range(1, n - window + 1):
        h = ((h - kind_hashes[i - 1] * base_pow) * _BASE + kind_hashes[i + window - 1]) % _MODULUS
        # Python's % already yields a non-negative result for a positive
        # modulus, but we keep the guard for clarity.
        if h < 0:
            h += _MODULUS
        out.append(
            WindowHash(
                file_path=file_path,
                hash_value=h,
                start_index=i,
                start_line=tokens[i].start_line,
                end_line=tokens[i + window - 1].end_line,
            )
        )
    return out


def index_by_hash(hashes: Iterable[WindowHash]) -> dict[int, list[WindowHash]]:
    """Group windows by their hash so collisions are easy to walk."""
    bucket: dict[int, list[WindowHash]] = {}
    for w in hashes:
        bucket.setdefault(w.hash_value, []).append(w)
    return bucket


def iter_collisions(
    bucket: dict[int, list[WindowHash]],
) -> Iterator[tuple[WindowHash, WindowHash]]:
    """Yield (a, b) pairs of windows that share a hash, a different file
    or non-overlapping windows in the same file. The verifier in
    ``detector.py`` then confirms by token-by-token compare."""
    for windows in bucket.values():
        if len(windows) < 2:
            continue
        for i in range(len(windows)):
            for j in range(i + 1, len(windows)):
                a, b = windows[i], windows[j]
                if a.file_path == b.file_path and abs(a.start_index - b.start_index) < (
                    b.end_line - b.start_line + 1
                ):
                    # Skip overlapping windows in the same file.
                    continue
                yield a, b
