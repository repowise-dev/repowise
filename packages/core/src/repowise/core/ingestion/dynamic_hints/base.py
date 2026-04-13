from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DynamicEdge:
    source: str  # repo-relative path
    target: str  # repo-relative path
    edge_type: str  # "dynamic_uses" | "dynamic_imports" | "url_route"
    hint_source: str  # extractor name
    weight: float = 1.0


class DynamicHintExtractor(ABC):
    name: str

    @abstractmethod
    def extract(self, repo_root: Path) -> list[DynamicEdge]: ...
