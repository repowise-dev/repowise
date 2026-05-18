"""Native Rabin-Karp duplication detection over tree-sitter tokens."""

from .detector import (
    DEFAULT_MIN_LINES,
    DEFAULT_WINDOW_TOKENS,
    ClonePair,
    DuplicationReport,
    detect_clones,
)
from .tokenizer import Token, tokenize_file, tokenize_tree

__all__ = [
    "DEFAULT_MIN_LINES",
    "DEFAULT_WINDOW_TOKENS",
    "ClonePair",
    "DuplicationReport",
    "Token",
    "detect_clones",
    "tokenize_file",
    "tokenize_tree",
]
