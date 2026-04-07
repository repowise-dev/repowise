from __future__ import annotations

from pathlib import Path

import structlog

from .base import DynamicEdge, DynamicHintExtractor
from .django import DjangoDynamicHints
from .pytest_hints import PytestDynamicHints
from .node import NodeDynamicHints

log = structlog.get_logger(__name__)


class HintRegistry:
    def __init__(self, extractors: list[DynamicHintExtractor] | None = None) -> None:
        self._extractors = extractors or [
            DjangoDynamicHints(),
            PytestDynamicHints(),
            NodeDynamicHints(),
        ]

    def extract_all(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []
        for ex in self._extractors:
            try:
                got = ex.extract(repo_root)
                edges.extend(got)
                log.debug("dynamic_hints", extractor=ex.name, count=len(got))
            except Exception as e:
                log.warning("dynamic_hints_failed", extractor=ex.name, error=str(e))
        return edges
