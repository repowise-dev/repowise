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

from collections.abc import Iterable
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


@dataclass(frozen=True, slots=True)
class WindowHash:
    """One rolling-hash window with origin file + line span."""

    file_path: str
    hash_value: int
    start_index: int  # token index of the window's first token
    start_line: int
    end_line: int


# One window as a plain tuple: (hash_value, start_index, start_line, end_line).
WindowRow = tuple[int, int, int, int]


def window_rows(
    kinds: list[str],
    start_lines: list[int],
    end_lines: list[int],
    window: int,
) -> list[WindowRow]:
    """Rolling hashes for every window of length *window*, as plain tuples.

    The parallel lists describe one token stream. Returns an empty list when
    the stream is shorter than one window. A repository scan rolls millions
    of windows, so the per-kind hash is computed once per distinct spelling
    and no object is built per window.
    """
    n = len(kinds)
    if n < window or window <= 0:
        return []

    base, modulus = _BASE, _MODULUS
    base_pow = pow(base, window - 1, modulus)
    per_kind = {kind: _token_hash(kind) for kind in set(kinds)}
    kind_hashes = [per_kind[kind] for kind in kinds]

    h = 0
    for i in range(window):
        h = (h * base + kind_hashes[i]) % modulus

    out: list[WindowRow] = [(h, 0, start_lines[0], end_lines[window - 1])]
    append = out.append
    # Python's % yields a non-negative result for a positive modulus.
    for i in range(1, n - window + 1):
        last = i + window - 1
        h = ((h - kind_hashes[i - 1] * base_pow) * base + kind_hashes[last]) % modulus
        append((h, i, start_lines[i], end_lines[last]))
    return out


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
    rows = window_rows(
        [t.kind for t in tokens],
        [t.start_line for t in tokens],
        [t.end_line for t in tokens],
        window,
    )
    return [
        WindowHash(file_path=file_path, hash_value=h, start_index=si, start_line=sl, end_line=el)
        for h, si, sl, el in rows
    ]


def index_by_hash(hashes: Iterable[WindowHash]) -> dict[int, list[WindowHash]]:
    """Group windows by their hash so collisions are easy to walk."""
    bucket: dict[int, list[WindowHash]] = {}
    for w in hashes:
        bucket.setdefault(w.hash_value, []).append(w)
    return bucket
