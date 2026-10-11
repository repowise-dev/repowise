"""Native Rabin-Karp duplication detection over tree-sitter tokens."""

from .detector import (
    DEFAULT_MIN_LINES,
    DEFAULT_WINDOW_TOKENS,
    ClonePair,
    DuplicationReport,
    detect_clones,
)
from .limits import DuplicationDiagnostics, DuplicationLimits, looks_minified
from .tokenizer import Token, tokenize_file, tokenize_tree

__all__ = [
    "DEFAULT_MIN_LINES",
    "DEFAULT_WINDOW_TOKENS",
    "ClonePair",
    "DuplicationDiagnostics",
    "DuplicationLimits",
    "DuplicationReport",
    "Token",
    "detect_clones",
    "looks_minified",
    "tokenize_file",
    "tokenize_tree",
]
