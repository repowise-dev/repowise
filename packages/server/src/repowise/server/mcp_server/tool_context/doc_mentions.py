"""Documentation lines naming a file by a short form the drift index cannot resolve.

The drift pass stores a reference only when the document's path resolves to
the file. Docs often name a file as ``folder/stem`` or by its basename, which
resolve to nothing on their own; this reads those lines live, so the
``doc_drift`` block of a file still shows the documents a change must touch.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from repowise.server.mcp_server._edit_sites import _GENERIC_STEMS, MAX_TEXT_CHARS, _read_lines
from repowise.server.mcp_server._edit_sites_wider import documents_holding

#: Read alongside the stored rows; a mention is a lead, not a resolved reference.
MENTIONS_BASIS = (
    "Documentation lines naming this file by basename or folder/stem, which the "
    "references above cannot resolve. Check each before editing."
)
# A bare stem names one file only when it carries a separator: ``decisions-table``.
_DISTINCT_STEM = re.compile(r"[-_]")


def short_forms(target: str) -> list[str]:
    """The ways a document names *target* short of its full path."""
    path = PurePosixPath(target)
    forms = [path.name]
    if path.parent.name:
        forms.append(f"{path.parent.name}/{path.stem}")
    if path.stem not in _GENERIC_STEMS and _DISTINCT_STEM.search(path.stem):
        forms.append(path.stem)
    return forms


def _mention_pattern(target: str) -> re.Pattern[str]:
    """The short forms of *target* as whole tokens.

    A bare basename or stem after a ``/`` belongs to another folder's file, and
    one inside a longer name (``x.test.tsx``) is another file.
    """
    path = PurePosixPath(target)
    folder_form = f"{path.parent.name}/{path.stem}"
    forms = short_forms(target)
    own_suffix = rf"(?:{re.escape(path.suffix)})?" if path.suffix else ""
    folder = re.escape(folder_form) + own_suffix if folder_form in forms else None
    bare_forms = sorted(set(forms) - {folder_form}, key=len, reverse=True)
    bare = "|".join(re.escape(f) for f in bare_forms)
    alts = [rf"(?<![\w./-])(?:{bare})"] + ([rf"(?<![\w.-]){folder}"] if folder else [])
    return re.compile(rf"(?:{'|'.join(alts)})(?!\.?[\w-])")


def doc_mentions(
    root: Path, target: str, listed: set[tuple[str, int]]
) -> list[dict[str, Any]]:
    """``{document, line, text}`` rows naming *target* by a short form, minus *listed* sites."""
    docs = {d for form in short_forms(target) for d in documents_holding(root, form)}
    pattern = _mention_pattern(target)
    rows: list[dict[str, Any]] = []
    for doc in sorted(docs - {target}):
        for n, text in enumerate(_read_lines(root, doc) or [], 1):
            if (doc, n) not in listed and pattern.search(text):
                rows.append({"document": doc, "line": n, "text": text.strip()[:MAX_TEXT_CHARS]})
    return rows
