"""The one IO adapter: both sides of a change, read from a revision source."""

from __future__ import annotations

from collections.abc import Mapping

from ..change_health.sources import RevisionPair, RevisionSource
from .detect import detect_diff_risk
from .models import DiffRiskFinding, FileTexts
from .paths import is_relevant

#: Larger files are bundles or data, not hand-written code.
MAX_FILE_BYTES = 1_000_000


def read_change_texts(source: RevisionSource, pair: RevisionPair) -> list[FileTexts]:
    """Both sides of every relevant changed file, in two bulk reads.

    A rename reads its base text at the old path. A side that should exist
    and cannot be read drops the file: a failed read must never pass for a
    new file, where every marker would read as added.
    """
    changes = [c for c in pair.changes if is_relevant(c.path)]
    head_paths = [c.head_path for c in changes if c.head_path]
    base_paths = [c.base_path for c in changes if c.base_path]
    head = (
        source.read_working_tree(head_paths)
        if pair.working_tree
        else source.read(pair.head_sha, head_paths)
    )
    base = source.read(pair.base_sha, base_paths)
    texts = (_texts(c.base_path, c.head_path, base, head) for c in changes)
    return [t for t in texts if t is not None]


def _texts(
    base_path: str | None, head_path: str | None, base: dict[str, bytes], head: dict[str, bytes]
) -> FileTexts | None:
    """Both sides of one file, or ``None`` when a side that exists did not read as text."""
    sides: list[str | None] = []
    for path, blobs in ((base_path, base), (head_path, head)):
        text = None if path is None else _decode(blobs.get(path))
        if path is not None and text is None:
            return None
        sides.append(text)
    return FileTexts(base_path, head_path, sides[0], sides[1])


def _decode(raw: bytes | None) -> str | None:
    """Text of *raw*; ``None`` for a missing, binary or oversized blob."""
    if raw is None or len(raw) > MAX_FILE_BYTES or b"\x00" in raw:
        return None
    return raw.decode("utf-8", errors="replace")


def diff_risk_for_change(
    source: RevisionSource,
    pair: RevisionPair,
    *,
    severities: Mapping[str, str] | None = None,
) -> list[DiffRiskFinding]:
    """:func:`.detect.detect_diff_risk` over a resolved revision pair."""
    return detect_diff_risk(read_change_texts(source, pair), severities=severities)
