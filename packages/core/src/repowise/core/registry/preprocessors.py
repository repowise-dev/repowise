"""Repository-aware, fail-fast preprocessing before discovery or change detection."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

Preprocessor = Callable[[Path], Iterable[str]]


class RepositoryPreprocessors:
    """Named synchronous callbacks returning repository-relative managed output paths."""

    def __init__(self) -> None:
        self._callbacks: dict[str, Preprocessor] = {}

    def register(self, name: str, callback: Preprocessor) -> None:
        self._callbacks[name] = callback

    def run(self, repo_path: Path) -> frozenset[str]:
        root = repo_path.resolve()
        paths: set[str] = set()
        for name, callback in self._callbacks.items():
            try:
                for value in callback(root):
                    path = Path(value)
                    if path.is_absolute() or ".." in path.parts or path == Path("."):
                        raise ValueError(f"invalid managed output path: {value!r}")
                    if not (root / path).resolve().is_relative_to(root):
                        raise ValueError(f"managed output escapes repository: {value!r}")
                    if path.parts[0] in {".git", ".repowise"}:
                        raise ValueError(f"reserved managed output path: {value!r}")
                    paths.add(path.as_posix())
            except Exception as exc:
                raise RuntimeError(f"Repository preprocessor {name!r} failed: {exc}") from exc
        return frozenset(paths)


repository_preprocessors = RepositoryPreprocessors()


def preprocess_repository(repo_path: Path) -> frozenset[str]:
    """Load installed plugins and run before enumerating any repository paths.

    Unlike observational phase hooks, preprocessing failures must abort indexing.
    Callers must skip this operation on a dry run and before read-only analysis.
    """
    from .plugins import load_plugins

    load_plugins()
    return repository_preprocessors.run(repo_path)
