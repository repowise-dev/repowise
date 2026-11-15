"""Fix-commit shapes kept between runs, so an update only opens new fix diffs.

A commit's diff never changes, so its :func:`.fix_shape.classify_fix_shape`
verdict only changes with the classifier. The cache is keyed on a fingerprint
of the code and extension tables that verdict reads; any edit there discards
every cached shape, so the cache can only save time, never change a count.

Ceiling: git's own diff settings (``diff.renames``, ``diff.algorithm``) also
shape the patch. A shape cached before such a config change stays until the
commit leaves the window; hashing ``git config`` into the key would lift it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_CACHE_NAME = "fix_shape_cache.json"


def _classifier_fingerprint() -> str:
    """Hash of everything a shape verdict depends on besides the diff itself."""
    from repowise.core.analysis import changed_lines
    from repowise.core.support_paths import DOC_EXTENSIONS

    from . import fix_shape, prior_defects
    from ._constants import _CODE_EXTENSIONS

    digest = hashlib.sha256()
    # prior_defects holds the diff command, changed_lines parses it.
    for module in (fix_shape, prior_defects, changed_lines):
        digest.update(Path(module.__file__).read_bytes())
    digest.update(repr(sorted(DOC_EXTENSIONS)).encode())
    digest.update(repr(sorted(_CODE_EXTENSIONS)).encode())
    return digest.hexdigest()


class FixShapeCache:
    """``sha -> (shape_kind, committer ts)`` for fix commits whose diff was classified."""

    def __init__(
        self, path: Path | None, shapes: dict[str, tuple[str, int]], fingerprint: str
    ) -> None:
        self._path = path
        self._fingerprint = fingerprint
        self.shapes = shapes

    @classmethod
    def load(cls, cache_dir: Path | None) -> FixShapeCache:
        """The cache under *cache_dir*; empty when absent, unreadable or stale."""
        try:
            fingerprint = _classifier_fingerprint()
        except (OSError, TypeError):
            # Sources not on disk (e.g. a zipped install): run without a cache.
            return cls(None, {}, "")
        path = Path(cache_dir) / _CACHE_NAME if cache_dir is not None else None
        shapes: dict[str, tuple[str, int]] = {}
        if path is not None and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("fingerprint") == fingerprint:
                    shapes = {
                        str(sha): (str(kind), int(ts)) for sha, (kind, ts) in data["shapes"].items()
                    }
            except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
                logger.debug("fix_shape_cache_read_failed", error=str(exc))
        return cls(path, shapes, fingerprint)

    def kinds(self) -> dict[str, str]:
        return {sha: kind for sha, (kind, _) in self.shapes.items()}

    def update(self, fresh: dict[str, tuple[str, int]], *, oldest_ts: int) -> None:
        """Add *fresh* shapes and drop those older than the walk's oldest fix.

        Evicting by time, not by the walk's shas, keeps fixes a narrower file
        set skipped this run; the window's trailing edge bounds the file.
        """
        kept = {sha: entry for sha, entry in self.shapes.items() if entry[1] >= oldest_ts}
        self.shapes = {**kept, **fresh}

    def save(self) -> None:
        if self._path is None:
            return
        try:
            from repowise.core.fsutils import atomic_write_text

            atomic_write_text(
                self._path,
                json.dumps({"fingerprint": self._fingerprint, "shapes": self.shapes}),
            )
        except OSError as exc:
            logger.debug("fix_shape_cache_write_failed", error=str(exc))
