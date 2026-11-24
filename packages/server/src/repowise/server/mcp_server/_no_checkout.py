"""``tools/list`` wording for a server with no checkout of the repository.

A host that serves the tools from a stored index cannot read live files, so
the default descriptions overpromise there: ``lines`` read from live files
and ``complete``, a line-verified skeleton, verified source, a live line
range, uncommitted work. Each entry swaps one such phrase for what holds
without a checkout. Phrases, not whole descriptions, so an edit elsewhere in
a docstring carries over; a test fails when a phrase no longer matches.
"""

from __future__ import annotations

from typing import Any

#: Tool name -> (where, old, new). *where* is ``"description"`` or the name of
#: an input property whose description holds the phrase.
NO_CHECKOUT_WORDING: dict[str, tuple[tuple[str, str, str], ...]] = {
    "search_codebase": (
        (
            "description",
            "read\nfrom live files; when `complete` is true they are every match, so no\n"
            "grep is needed.",
            "from\nthe index, never every match: grep your checkout before editing each use.",
        ),
    ),
    "get_context": (
        (
            "description",
            'Whole file, body-elided and line-verified: include=["skeleton"]\n'
            "in ONE call, or Read it; do not call get_symbol per signature. Rename:\n"
            'include=["references"] is the whole edit set when `complete`.',
            "Whole file: Read it\nin your checkout; do not call get_symbol per signature. Rename:\n"
            "grep your checkout for the edit set.",
        ),
    ),
    "get_symbol": (
        (
            "description",
            'whole file `get_context(include=["skeleton"])` or a Read is one call.',
            "whole file a Read of your checkout is one call.",
        ),
        (
            "description",
            "Returns verified, line-numbered\nsource;",
            "Served from the index with no\ncheckout, so unverified;",
        ),
        (
            "symbol_id",
            '"path/to/file.py::Name", "path/to/file.py:140-180" for a live range, or an '
            "omission ref.",
            '"path/to/file.py::Name" or an omission ref.',
        ),
    ),
    "get_change_risk": (
        (
            "description",
            "Review a commit, ``base..head`` range, or uncommitted work.",
            "Review an indexed commit or ``base..head`` range.",
        ),
        (
            "description",
            "revspec: commit or ``base..head``. Omit for uncommitted work, or\n"
            "            ``HEAD`` when the tree is clean.",
            "revspec: an indexed commit or ``base..head``.",
        ),
    ),
    "get_health": (
        (
            "description",
            "Never recomputes;\n    ``repowise update`` does.",
            "Never recomputes;\n    a re-index does.",
        ),
    ),
}


def without_checkout(definition: dict[str, Any]) -> dict[str, Any]:
    """*definition* (a dumped ``tools/list`` entry) worded for no checkout, in place."""
    properties = definition.get("inputSchema", {}).get("properties", {})
    for where, old, new in NO_CHECKOUT_WORDING.get(definition["name"], ()):
        holder = definition if where == "description" else properties.get(where)
        if holder and old in (holder.get("description") or ""):
            holder["description"] = holder["description"].replace(old, new)
    return definition
