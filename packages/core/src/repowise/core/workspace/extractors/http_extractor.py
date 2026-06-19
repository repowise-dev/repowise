"""Backwards-compatible re-exports for the HTTP contract extractor.

The implementation moved into the :mod:`.http` dialect package. This shim keeps
``from ...extractors.http_extractor import HttpExtractor, normalize_http_path``
working for existing imports.
"""

from __future__ import annotations

from .http import HttpExtractor, normalize_http_path

__all__ = ["HttpExtractor", "normalize_http_path"]
