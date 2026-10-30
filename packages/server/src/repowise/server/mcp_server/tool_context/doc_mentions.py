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

from repowise.core.support_paths import DOC_EXTENSIONS, classification_token
from repowise.server.mcp_server._edit_sites import _GENERIC_STEMS, MAX_TEXT_CHARS, _read_lines

#: Read alongside the stored rows; a mention is a lead, not a resolved reference.
MENTIONS_BASIS = (
    "Documentation lines naming this file by basename or folder/stem, which the "
    "references above cannot resolve. Check each before editing."
)
_MAX_DOCUMENTS = 50
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


def doc_mentions(
    root: Path, target: str, listed: set[tuple[str, int]]
) -> list[dict[str, Any]]:
    """``{document, line, text}`` rows naming *target* by a short form, minus *listed* sites."""
    from repowise.server.mcp_server._line_hits import _files_holding, _listed_files

    forms = short_forms(target)
    documents: set[str] = set()
    for form in forms:
        held = _files_holding(root, form)
        # Without git every listed document is read instead.
        documents.update(_listed_files(root) if held is None else held)
    docs = sorted(d for d in documents if classification_token(d) in DOC_EXTENSIONS)
    alt = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    # A form inside a longer word or file name (``x.test.tsx``) is another file.
    pattern = re.compile(rf"(?<![\w.-])(?:{alt})(?!\.?[\w-])")
    rows: list[dict[str, Any]] = []
    for doc in docs[:_MAX_DOCUMENTS]:
        if doc == target:
            continue
        for n, text in enumerate(_read_lines(root, doc) or [], 1):
            if (doc, n) not in listed and pattern.search(text):
                rows.append({"document": doc, "line": n, "text": text.strip()[:MAX_TEXT_CHARS]})
    return rows
