"""A committed file of accepted findings, shared by every gate that takes one.

The baseline lets a repository adopt a gate without first fixing every existing
finding: what it lists does not fail, anything new does. It is a file path the
caller names, never ``.repowise/config.yaml``, because that config is local
state and a CI policy has to live in the repository.

Each feature supplies its entries (a ``fingerprint`` plus whatever a reader
needs to recognise the finding) and their order; this module owns the envelope.
One entry per fingerprint, sorted, deterministic bytes, so rewriting an
unchanged baseline is a no-op diff.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

BASELINE_VERSION = 1


class BaselineError(ValueError):
    """A baseline file that cannot be used; the message says why."""


def build_document(
    entries: Iterable[Mapping[str, Any]],
    *,
    basis: str,
    sort_key: Callable[[dict], Any],
) -> dict:
    """The baseline document: the first entry per fingerprint, ordered by *sort_key*."""
    unique: dict[str, dict] = {}
    for entry in entries:
        unique.setdefault(entry["fingerprint"], dict(entry))
    ordered = sorted(unique.values(), key=sort_key)
    return {"version": BASELINE_VERSION, "basis": basis, "entries": ordered}


def write_document(path: Path | str, doc: Mapping[str, Any]) -> int:
    """Write *doc* to *path*; returns the entries written."""
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    Path(path).write_text(text, encoding="utf-8")
    return len(doc["entries"])


def read_entries(path: Path | str) -> list[dict]:
    """The entries *path* holds; raises :class:`BaselineError` if unusable."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BaselineError(f"cannot read baseline {path}: {exc}") from exc
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"baseline {path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise BaselineError(f"baseline {path} must be a JSON object")
    version = doc.get("version")
    if version != BASELINE_VERSION:
        raise BaselineError(
            f"baseline {path} has version {version!r}; this build reads "
            f"version {BASELINE_VERSION}"
        )
    entries = doc.get("entries")
    if not isinstance(entries, list):
        raise BaselineError(f"baseline {path} has no 'entries' list")
    for i, entry in enumerate(entries):
        fp = entry.get("fingerprint") if isinstance(entry, dict) else None
        if not isinstance(fp, str) or not fp:
            raise BaselineError(f"baseline {path} entry {i} has no fingerprint")
    return entries


def read_baseline(path: Path | str) -> frozenset[str]:
    """The fingerprints *path* accepts; raises :class:`BaselineError` if unusable."""
    return frozenset(entry["fingerprint"] for entry in read_entries(path))
