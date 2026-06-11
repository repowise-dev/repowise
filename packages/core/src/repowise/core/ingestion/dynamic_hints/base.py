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

    # Shared walk snapshot, attached by HintRegistry.extract_all so the ~40
    # _rglob queries the extractor fleet issues replay ONE filesystem walk
    # instead of each walking the tree again. None -> live walk (direct
    # extractor use in tests keeps working unchanged).
    _walk_snapshot = None

    @abstractmethod
    def extract(self, repo_root: Path) -> list[DynamicEdge]: ...

    def _rglob(self, root: Path, pattern: str) -> Iterator[Path]:
        """Pruned replacement for :py:meth:`pathlib.Path.rglob`.

        Skips ``node_modules``, ``.venv``, ``.next``, etc. so large
        polyrepos don't tank the dynamic-hints phase. See
        :mod:`dynamic_hints._walk` for the full prune list. Served from
        the registry's shared :class:`~repowise.core.fs_walk.WalkSnapshot`
        when one is attached.
        """
        if self._walk_snapshot is not None:
            return self._walk_snapshot.iter_glob(root, pattern)
        return iter_glob(root, pattern)
