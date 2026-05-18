from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field  # noqa: F401  — re-exported
from pathlib import Path

from ._walk import iter_glob


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

    @staticmethod
    def _rglob(root: Path, pattern: str) -> Iterator[Path]:
        """Pruned replacement for :py:meth:`pathlib.Path.rglob`.

        Skips ``node_modules``, ``.venv``, ``.next``, etc. so large
        polyrepos don't tank the dynamic-hints phase. See
        :mod:`dynamic_hints._walk` for the full prune list.
        """
        return iter_glob(root, pattern)
