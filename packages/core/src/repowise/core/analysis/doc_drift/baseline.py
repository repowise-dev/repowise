"""A committed file of accepted documentation drift.

The baseline lets a repository adopt the gate without first fixing every
existing finding: what it lists does not fail, anything new does. It is a file
path the caller names, never ``.repowise/config.yaml``, because that config is
local state and a CI policy has to live in the repository.

Entries are keyed on the line-independent fingerprint and sorted, and the bytes
are deterministic, so rewriting an unchanged baseline is a no-op diff.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .serialize import fingerprint_of

BASELINE_VERSION = 1

BASELINE_BASIS = (
    "Documentation drift accepted as known. A listed finding does not fail the "
    "gate; it is keyed on document, kind and target, not line."
)


class BaselineError(ValueError):
    """A baseline file that cannot be used; the message says why."""


def build_baseline(findings: Iterable[Mapping[str, Any]]) -> dict:
    """The baseline document for *findings*, one entry per fingerprint."""
    entries: dict[str, dict] = {}
    for finding in findings:
        fp = fingerprint_of(finding)
        entries.setdefault(
            fp,
            {
                "fingerprint": fp,
                "file_path": finding["file_path"],
                "kind": str(finding["kind"]),
                "target": finding["target"],
            },
        )
    ordered = sorted(
        entries.values(), key=lambda e: (e["file_path"], e["kind"], e["target"])
    )
    return {"version": BASELINE_VERSION, "basis": BASELINE_BASIS, "entries": ordered}


def write_baseline(path: Path | str, findings: Iterable[Mapping[str, Any]]) -> int:
    """Write the baseline for *findings* to *path*; returns entries written."""
    doc = build_baseline(findings)
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    Path(path).write_text(text, encoding="utf-8")
    return len(doc["entries"])


def read_baseline(path: Path | str) -> frozenset[str]:
    """The fingerprints *path* accepts; raises :class:`BaselineError` if unusable."""
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
    out: set[str] = set()
    for i, entry in enumerate(entries):
        fp = entry.get("fingerprint") if isinstance(entry, dict) else None
        if not isinstance(fp, str) or not fp:
            raise BaselineError(f"baseline {path} entry {i} has no fingerprint")
        out.add(fp)
    return frozenset(out)
