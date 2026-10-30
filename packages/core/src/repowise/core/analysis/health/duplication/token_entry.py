"""One file's duplication token-cache entry, derived from its bytes.

Everything that shapes a cached entry lives here or in the tokenizer and
rolling hash it calls, so the token cache's code fingerprint can cover
exactly that code and nothing the clone verifier changes.
"""

from __future__ import annotations

from itertools import accumulate

from .rabin_karp import WindowRow, window_rows
from .tokenizer import tokenize_file_rows

# A window above this share of literal/punctuation tokens is a data literal.
_MAX_NOISE_SHARE = 0.8

# (kinds, identifier names, nloc, window tuples)
TokenEntry = tuple[list[str], list[str], int, list[WindowRow]]


def tokenize_entry(
    language: str, source: bytes, path: str, window_tokens: int, max_tokens: int
) -> TokenEntry | None:
    """The entry for *source*, or ``None`` when it has more than *max_tokens* tokens."""
    rows = tokenize_file_rows(language, source, path)
    if len(rows) > max_tokens:
        return None
    kinds: list[str] = []
    start_lines: list[int] = []
    end_lines: list[int] = []
    names: list[str] = []
    if rows:
        kinds, start_lines, end_lines, _, _, names = map(list, zip(*rows, strict=True))
    del rows
    windows = _informative_windows(
        window_rows(kinds, start_lines, end_lines, window_tokens), kinds, window_tokens
    )
    return kinds, names, _nloc(source), windows


def _is_noise(kind: str) -> bool:
    return kind == "LIT" or not any(ch.isalnum() for ch in kind)


def _informative_windows(
    windows: list[WindowRow], kinds: list[str], window: int
) -> list[WindowRow]:
    """Drop windows that are more than ``_MAX_NOISE_SHARE`` literals/punctuation."""
    noisy = {kind: _is_noise(kind) for kind in set(kinds)}
    noise = list(accumulate((noisy[k] for k in kinds), initial=0))
    limit = _MAX_NOISE_SHARE * window
    return [w for w in windows if noise[w[1] + window] - noise[w[1]] <= limit]


def _nloc(source: bytes) -> int:
    try:
        text = source.decode("utf-8", errors="replace")
    except Exception:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())
